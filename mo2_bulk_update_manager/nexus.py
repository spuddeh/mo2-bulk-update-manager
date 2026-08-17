"""A small asynchronous client for the Nexus Mods v1 API.

Built on QtNetwork so the MO2 UI stays responsive during a scan -- no threads,
no blocking sockets. Requests are queued and run a few at a time, and every
reply's rate-limit headers are recorded so the caller can back off before Nexus
starts refusing.

Only the endpoints this plugin needs are wrapped. Notably ``mods/updated`` and
``changelogs`` are not reachable through MO2's own ``IModRepositoryBridge``,
which is the reason this client exists at all.
"""

from __future__ import annotations

import json
from collections import deque
from typing import Callable, Optional

try:
    from PyQt5.QtCore import QObject, QUrl, pyqtSignal
    from PyQt5.QtNetwork import (
        QNetworkAccessManager,
        QNetworkReply,
        QNetworkRequest,
    )
except ImportError:
    from PyQt6.QtCore import QObject, QUrl, pyqtSignal
    from PyQt6.QtNetwork import (
        QNetworkAccessManager,
        QNetworkReply,
        QNetworkRequest,
    )

from ._version import VERSION
from .log import get_logger
from .log import tag as _tag  # `tag` is also a parameter/attribute name here

_log = get_logger("nexus")

API_BASE = "https://api.nexusmods.com/v1"

# The v3 API models what v1 leaves to inference: a "mod file" *is* the update
# chain an author declares, and each version carries a `position` where higher
# means newer. That removes the need to order free-text version strings, which
# is where every classification bug so far has come from.
#
# Two of its endpoints take up to 2000 ids at once, and the ids are computable
# rather than looked up: `uid = game_id << 32 | legacy_id`, for both mods and
# file versions. So the status of an entire modlist costs one request.
#
# Nexus badges these endpoints **Experimental** -- "may change significantly or
# be removed", with no deprecation window (the 90-day promise covers Stable
# only). They were adopted deliberately with that understood.
#
# v3 has no equivalent of `mods/updated` and no changelog *read* endpoint, so
# change detection and changelogs stay on v1.
API_V3 = "https://api.nexusmods.com/v3"

# Both batch endpoints cap at 2000 ids per request.
BATCH_LIMIT = 2000


def composite_uid(game_id: int, legacy_id: int) -> str:
    """The v3 id for a mod or file version, from its game and legacy id."""
    return str((int(game_id) << 32) | int(legacy_id))


def legacy_id(uid, game_id: int) -> int:
    """The inverse of :func:`composite_uid`."""
    return int(uid) - (int(game_id) << 32)

# The v1 API accepts an OAuth bearer token on every mod/game endpoint, but
# ``users/validate`` is API-key only -- OAuth sessions identify themselves via
# the accounts service instead (see ``nxmaccessmanager.cpp:195``).
OAUTH_USERINFO_URL = "https://users.nexusmods.com/oauth/userinfo"

# Leave enough headroom that a scan never locks the user out of MO2's own Nexus
# features. The real allowance arrives in the x-rl-* headers on every reply.
DEFAULT_HOURLY_FLOOR = 10


class Response:
    """The outcome of a single API call."""

    __slots__ = ("ok", "status", "data", "error", "tag")

    def __init__(self, ok, status, data, error, tag):
        self.ok = ok
        self.status = status
        self.data = data
        self.error = error
        self.tag = tag

    @property
    def missing(self) -> bool:
        """True when Nexus says the resource is gone (deleted or fully hidden)."""
        return self.status == 404

    @property
    def unauthorized(self) -> bool:
        return self.status in (401, 403)

    def __repr__(self):
        return f"<Response ok={self.ok} status={self.status} tag={self.tag!r}>"


# A server error or a dropped connection says nothing about the request, so it
# is worth repeating. A scan of a thousand chains will meet one eventually --
# one 500 cost a mod its result on a real run.
MAX_RETRIES = 2


class _Pending:
    __slots__ = ("url", "callback", "tag", "payload", "attempts")

    def __init__(self, url, callback, tag, payload=None):
        self.url = url
        self.callback = callback
        self.tag = tag
        self.payload = payload  # set for the v3 batch endpoints, which are POSTs
        self.attempts = 0


class NexusClient(QObject):
    """Queued, rate-limit-aware GET client for the Nexus v1 API."""

    rateLimitChanged = pyqtSignal(object, object)  # hourly remaining, daily remaining
    queueChanged = pyqtSignal(int, int)  # completed, total

    MAX_CONCURRENT = 4

    def __init__(self, auth, app_version: str = "", parent=None):
        super().__init__(parent)
        self._auth = auth
        self._app_version = app_version or "2.5"
        self._nam = QNetworkAccessManager(self)
        self._queue: deque[_Pending] = deque()
        self._active = 0
        self._cancelled = False
        self._issued = 0
        self._completed = 0
        self._replies: list = []

        self.hourly_remaining: Optional[int] = None
        self.daily_remaining: Optional[int] = None
        self.hourly_floor = DEFAULT_HOURLY_FLOOR
        self.throttled = False

    # -- lifecycle ---------------------------------------------------------

    def cancel(self) -> None:
        """Drop everything queued and abort what is in flight."""
        self._cancelled = True
        self._queue.clear()
        for reply in list(self._replies):
            reply.abort()

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    @property
    def pending(self) -> int:
        return len(self._queue) + self._active

    # -- endpoints ---------------------------------------------------------

    def validate(self, callback: Callable[[Response], None]) -> None:
        """Confirm the credentials work and find out whether the account is premium."""
        if self._auth.scheme == "oauth":
            url = OAUTH_USERINFO_URL
        else:
            url = f"{API_BASE}/users/validate.json"
        self._enqueue(url, callback, "validate")

    @staticmethod
    def is_premium(user: dict) -> bool:
        """Normalise the two different shapes the two auth paths return."""
        if not user:
            return False
        if "is_premium" in user:
            return bool(user.get("is_premium"))
        roles = user.get("membership_roles") or []
        return any(str(role).lower() in ("premium", "lifetimepremium") for role in roles)

    @staticmethod
    def user_name(user: dict) -> str:
        return str((user or {}).get("name") or (user or {}).get("user_name") or "")

    def updated_mods(self, domain: str, period: str, callback) -> None:
        """Every mod in ``domain`` touched within ``period`` ('1d', '1w', '1m').

        One request covers the whole game, which is what makes checking a large
        modlist cheap.
        """
        self._enqueue(
            f"{API_BASE}/games/{domain}/mods/updated.json?period={period}",
            callback,
            ("updated", domain, period),
        )

    def mod_files(self, domain: str, mod_id: int, callback) -> None:
        self._enqueue(
            f"{API_BASE}/games/{domain}/mods/{mod_id}/files.json",
            callback,
            ("files", domain, mod_id),
        )

    def changelogs(self, domain: str, mod_id: int, callback) -> None:
        self._enqueue(
            f"{API_BASE}/games/{domain}/mods/{mod_id}/changelogs.json",
            callback,
            ("changelogs", domain, mod_id),
        )

    # -- v3 endpoints ------------------------------------------------------

    def game_id(self, domain: str, sample_mod_id: int, callback) -> None:
        """Learn a game's numeric id, needed to build every other v3 id.

        There is no games endpoint on v3, but any mod on the game reports its
        `game_id`, so one known mod is enough.
        """
        self._enqueue(
            f"{API_V3}/games/{domain}/mods/{sample_mod_id}",
            callback,
            ("game", domain, sample_mod_id),
        )

    def mods_batch(self, domain: str, uids: list, callback) -> None:
        """Mod-level status for up to 2000 mods in one request.

        A mod that is hidden, moderated or removed contributes no row, which is
        how delisting is detected -- for the whole modlist, every scan.
        """
        self._enqueue(
            f"{API_V3}/mods/batch",
            callback,
            ("mods_batch", domain, len(uids)),
            payload={"mod_ids": [str(u) for u in uids]},
        )

    def file_versions_batch(self, domain: str, uids: list, callback) -> None:
        """Resolve file versions to their update chain and position in it."""
        self._enqueue(
            f"{API_V3}/mod-file-versions/batch",
            callback,
            ("versions_batch", domain, len(uids)),
            payload={"version_ids": [str(u) for u in uids]},
        )

    def chain_versions(self, domain: str, chain_id, callback) -> None:
        """Every version in one update chain, ordered by `position`."""
        self._enqueue(
            f"{API_V3}/mod-files/{chain_id}/versions",
            callback,
            ("chain", domain, str(chain_id)),
        )

    def mod_chains(self, domain: str, mod_uid, callback) -> None:
        """The update chains on one mod page.

        Only needed for mods whose installed file id MO2 never recorded, so
        there is nothing to resolve in the batch.
        """
        self._enqueue(
            f"{API_V3}/mods/{mod_uid}/files",
            callback,
            ("chains", domain, str(mod_uid)),
        )

    # -- plumbing ----------------------------------------------------------

    def _enqueue(self, url: str, callback, tag, payload=None) -> None:
        if self._cancelled:
            return
        self._issued += 1
        self._queue.append(_Pending(url, callback, tag, payload))
        self.queueChanged.emit(self._completed, self._issued)
        self._pump()

    def _pump(self) -> None:
        while (
            not self._cancelled
            and self._queue
            and self._active < self.MAX_CONCURRENT
            and not self._out_of_budget()
        ):
            self._send(self._queue.popleft())

        if self._out_of_budget() and self._queue and not self.throttled:
            self.throttled = True
            _log.warning(
                _tag(
                    f"Stopping early: {self.hourly_remaining} Nexus request(s) left "
                    f"this hour (floor {self.hourly_floor}), {len(self._queue)} still "
                    "queued"
                )
            )
            # Fail the rest fast rather than hanging the dialog on a wall of 429s.
            stalled, self._queue = list(self._queue), deque()
            for item in stalled:
                self._finish(
                    item,
                    Response(
                        False,
                        429,
                        None,
                        "Nexus API hourly limit nearly exhausted; stopped early.",
                        item.tag,
                    ),
                )

    def _out_of_budget(self) -> bool:
        return (
            self.hourly_remaining is not None
            and self.hourly_remaining <= self.hourly_floor
        )

    def _send(self, item: _Pending) -> None:
        request = QNetworkRequest(QUrl(item.url))
        request.setRawHeader(b"Accept", b"application/json")
        request.setRawHeader(b"Application-Name", b"MO2")
        request.setRawHeader(b"Application-Version", self._app_version.encode("utf-8"))
        request.setRawHeader(b"Protocol-Version", b"1.0.0")
        request.setRawHeader(
            b"User-Agent",
            f"MO2-BulkUpdateManager/{VERSION} (MO2 {self._app_version})".encode("utf-8"),
        )
        self._auth.apply_to(request.setRawHeader)

        if item.payload is None:
            reply = self._nam.get(request)
        else:
            request.setRawHeader(b"Content-Type", b"application/json")
            reply = self._nam.post(
                request, json.dumps(item.payload).encode("utf-8")
            )
        self._active += 1
        self._replies.append(reply)
        reply.finished.connect(lambda r=reply, i=item: self._on_finished(r, i))

    def _on_finished(self, reply, item: _Pending) -> None:
        self._active -= 1
        if reply in self._replies:
            self._replies.remove(reply)

        self._read_rate_limits(reply)

        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        status = int(status) if status is not None else 0
        payload = bytes(reply.readAll())
        net_error = reply.error()
        reply.deleteLater()

        if net_error == QNetworkReply.NetworkError.OperationCanceledError:
            self._finish(item, Response(False, status, None, "Cancelled.", item.tag))
            return

        if 200 <= status < 300:
            try:
                data = json.loads(payload.decode("utf-8")) if payload else None
            except (ValueError, UnicodeDecodeError) as exc:
                self._finish(
                    item,
                    Response(False, status, None, f"Bad JSON from Nexus: {exc}", item.tag),
                )
                return
            self._finish(item, Response(True, status, data, "", item.tag))
            return

        # Retry a server error or a dropped connection; a 4xx is an answer.
        if (status >= 500 or status == 0) and item.attempts < MAX_RETRIES:
            item.attempts += 1
            _log.debug(
                _tag(
                    f"Retrying {item.tag} after {status or 'network error'} "
                    f"(attempt {item.attempts + 1} of {MAX_RETRIES + 1})"
                )
            )
            self._queue.appendleft(item)
            self._pump()
            return

        error = self._describe(status, reply, payload)
        # 404 is an ordinary answer here -- it is how a delisted mod presents --
        # so it is not worth a warning.
        (_log.debug if status == 404 else _log.warning)(
            _tag(f"Nexus request failed: {item.tag} -> {status} ({error})")
        )
        self._finish(item, Response(False, status, None, error, item.tag))

    def _finish(self, item: _Pending, response: Response) -> None:
        self._completed += 1
        self.queueChanged.emit(self._completed, self._issued)
        try:
            item.callback(response)
        finally:
            self._pump()

    def _read_rate_limits(self, reply) -> None:
        changed = False
        for header, attr in (
            (b"x-rl-hourly-remaining", "hourly_remaining"),
            (b"x-rl-daily-remaining", "daily_remaining"),
        ):
            raw = bytes(reply.rawHeader(header)).decode("ascii", "ignore").strip()
            if not raw:
                continue
            try:
                value = int(raw)
            except ValueError:
                continue
            if getattr(self, attr) != value:
                setattr(self, attr, value)
                changed = True
        if changed:
            _log.debug(
                _tag(
                    f"Nexus budget: {self.hourly_remaining} left this hour, "
                    f"{self.daily_remaining} today"
                )
            )
            self.rateLimitChanged.emit(self.hourly_remaining, self.daily_remaining)

    @staticmethod
    def _describe(status: int, reply, payload: bytes) -> str:
        if status == 401:
            return "Nexus rejected the credentials (401). Log in again in MO2, or set an API key in the plugin settings."
        if status == 403:
            return "Nexus refused the request (403)."
        if status == 404:
            return "Not found on Nexus (404)."
        if status == 429:
            return "Nexus rate limit reached (429). Try again later."
        if status >= 500:
            return f"Nexus server error ({status})."
        if status == 0:
            return reply.errorString() or "Network error."

        detail = ""
        try:
            body = json.loads(payload.decode("utf-8"))
            detail = body.get("message") or body.get("error") or ""
        except Exception:
            detail = ""
        return f"Nexus returned {status}{': ' + detail if detail else ''}."
