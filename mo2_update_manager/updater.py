"""The scan engine: decide what to ask Nexus, then classify the answers.

Classification runs on the **v3 API**, which models what v1 leaves to
inference. A "mod file" there *is* the update chain an author declares, and
every version in it carries a `position` where higher means newer. That
replaces three heuristics the v1 path needed -- grouping files by name,
stripping version tokens out of those names, and falling back to Nexus'
category when version strings would not order -- each of which was the cause
of a real misclassification.

The shape of a scan:

1. **Game ids.** Every v3 id is ``game_id << 32 | legacy_id``, computed
   locally. One request per game ever, then it is cached.
2. **What changed.** v1 ``mods/updated`` -- one request per game, and v3 has
   no equivalent, so this stays where it is.
3. **Status for the whole modlist.** ``POST /v3/mods/batch``, 2000 mods per
   request. A mod that is hidden, moderated or removed contributes no row, so
   delisting is now checked for *every* mod on *every* scan rather than a
   rotating slice.
4. **Where each installed file sits.** ``POST /v3/mod-file-versions/batch``,
   2000 per request, giving the chain and position of what you have. The
   answer never changes for a given file, so it is cached forever.
5. **What is newest in those chains.** ``GET /v3/mod-files/{id}/versions``,
   one per chain, and only for chains whose mod actually changed.

Steps 3 and 4 cost one request each for a thousand-mod profile. Step 5 is the
only part that scales with the modlist, and only with the part of it that
moved.
"""

from __future__ import annotations

import time
from typing import Optional

try:
    from PyQt5.QtCore import QObject, pyqtSignal
except ImportError:
    from PyQt6.QtCore import QObject, pyqtSignal

from .downloads import READY as DOWNLOAD_READY
from .downloads import find as find_download
from .log import get_logger, tag
from .nexus import BATCH_LIMIT, composite_uid
from .scanner import (
    ModEntry,
    as_file_record,
    choose_chain,
    is_ignored,
    newest_in_chain,
    position_of,
    versions_match,
)

# Nexus only accepts these three windows on the v1 change feed.
_PERIODS = (("1d", 1), ("1w", 7), ("1m", 28))

# v3 mod statuses that mean the page is not normally reachable. A mod missing
# from a batch response is invisible for one of these reasons without saying
# which, so absence is reported as hidden rather than guessed at.
_GONE_STATUSES = {"removed", "wastebinned", "deleted"}
_HIDDEN_STATUSES = {"hidden", "under_moderation", "not_published", "publish_with_game"}

# How long a chain's contents are trusted before being re-fetched, for mods the
# change feed did not mention.
_CHAIN_TTL_DAYS = 30

_log = get_logger("scan")


def _chunks(items: list, size: int = BATCH_LIMIT):
    for start in range(0, len(items), size):
        yield items[start : start + size]


class UpdateScan(QObject):
    """Drives a full pass over the modlist and reports classified results."""

    progress = pyqtSignal(str, int, int)  # message, done, total
    finished = pyqtSignal(list, str)  # entries, closing note
    failed = pyqtSignal(str)
    identified = pyqtSignal(dict)  # the Nexus account the scan is running as

    def __init__(self, client, cache, parent=None):
        super().__init__(parent)
        self._client = client
        self._cache = cache
        self._entries: list[ModEntry] = []
        self._by_key: dict[tuple[str, int], list[ModEntry]] = {}
        self._deep = False
        self._recheck_days = _CHAIN_TTL_DAYS
        self._notes: list[str] = []
        self._downloads: dict = {}
        self._domains: list[str] = []
        self._changed: dict[str, set] = {}
        self._pending = 0
        self._done = 0
        self._total = 0
        self._chain_wanted: dict[str, list] = {}
        self.user: dict = {}

    # -- entry point -------------------------------------------------------

    def start(
        self,
        entries: list[ModEntry],
        deep: bool,
        recheck_days: int = _CHAIN_TTL_DAYS,
        downloads: Optional[dict] = None,
    ) -> None:
        self._entries = entries
        self._downloads = downloads or {}
        self._deep = deep
        self._recheck_days = max(1, recheck_days)
        self._notes = []
        self._by_key = {}
        self._changed = {}
        self._chain_wanted = {}
        for entry in entries:
            self._by_key.setdefault(entry.key, []).append(entry)
        self._domains = sorted({entry.domain for entry in entries})

        _log.info(
            tag(
                f"{'Deep' if deep else 'Quick'} scan starting: {len(entries)} mod(s) "
                f"across {len(self._by_key)} Nexus page(s), {len(self._domains)} game(s)"
            )
        )

        if not entries:
            self.finished.emit([], "No Nexus-backed mods found in this profile.")
            return

        self.progress.emit("Checking your Nexus login...", 0, 0)
        self._client.validate(self._on_validated)

    def _on_validated(self, response) -> None:
        if not response.ok:
            self.failed.emit(response.error)
            return
        self.user = response.data or {}
        self.identified.emit(self.user)
        self._begin_game_ids()

    # -- phase 1: game ids -------------------------------------------------

    def _begin_game_ids(self) -> None:
        missing = [d for d in self._domains if not self._cache.game_id(d)]
        if not missing:
            self._begin_change_feed()
            return

        self.progress.emit("Identifying games...", 0, 0)
        self._pending = len(missing)
        for domain in missing:
            sample = next(k[1] for k in self._by_key if k[0] == domain)
            self._client.game_id(domain, sample, self._on_game_id)

    def _on_game_id(self, response) -> None:
        _, domain, _ = response.tag
        if response.ok:
            data = (response.data or {}).get("data") or {}
            if data.get("game_id"):
                self._cache.put_game_id(domain, int(data["game_id"]))
                _log.debug(tag(f"{domain} is game id {data['game_id']}"))
        else:
            self._notes.append(f"{domain}: could not be identified ({response.error}).")
            _log.warning(tag(f"Could not resolve game id for {domain}: {response.error}"))

        self._pending -= 1
        if self._pending <= 0:
            self._begin_change_feed()

    # -- phase 2: what changed ---------------------------------------------

    def _begin_change_feed(self) -> None:
        usable = [d for d in self._domains if self._cache.game_id(d)]
        if not usable:
            self.failed.emit(
                "No game could be identified on Nexus, so nothing could be checked."
            )
            return
        self._domains = usable

        if self._deep:
            for domain in self._domains:
                self._changed[domain] = None  # None means "everything"
                self._cache.mark_scan(domain, full=True)
            self._begin_status()
            return

        self._pending = len(self._domains)
        for domain in self._domains:
            period = self._period_for(domain)
            if period is None:
                self._changed[domain] = None
                self._cache.mark_scan(domain, full=True)
                self._notes.append(
                    f"{domain}: last check was too long ago for a quick lookup, "
                    "so every mod was queried."
                )
                self._feed_done()
                continue
            self.progress.emit(f"Asking Nexus what changed in {domain}...", 0, 0)
            self._client.updated_mods(domain, period, self._on_updated_list)

    def _period_for(self, domain: str) -> Optional[str]:
        last = self._cache.last_scan(domain)
        if not last:
            return None
        gap_days = (time.time() - last) / 86400.0
        for period, span in _PERIODS:
            if gap_days <= span:
                return period
        return None

    def _on_updated_list(self, response) -> None:
        _, domain, _ = response.tag
        if response.ok:
            rows = response.data or []
            self._changed[domain] = {
                int(r["mod_id"]) for r in rows if r.get("mod_id")
            }
            self._cache.mark_scan(domain, full=False)
        else:
            if response.unauthorized:
                self.failed.emit(response.error)
                return
            self._changed[domain] = None
            self._notes.append(
                f"{domain}: quick lookup failed ({response.error}) so every mod was queried."
            )
        self._feed_done()

    def _feed_done(self) -> None:
        self._pending -= 1
        if self._pending <= 0:
            self._begin_status()

    # -- phase 3: status for every mod -------------------------------------

    def _begin_status(self) -> None:
        self._batches = []
        for domain in self._domains:
            game = self._cache.game_id(domain)
            uids = [
                composite_uid(game, key[1]) for key in self._by_key if key[0] == domain
            ]
            for chunk in _chunks(uids):
                self._batches.append((domain, chunk))

        _log.info(
            tag(
                f"Checking status of {len(self._by_key)} page(s) in "
                f"{len(self._batches)} batch request(s)"
            )
        )
        self.progress.emit(
            f"Checking {len(self._by_key)} Nexus page(s)...", 0, len(self._by_key)
        )
        self._seen_status: dict[tuple[str, int], dict] = {}
        self._pending = len(self._batches)
        for domain, chunk in self._batches:
            self._client.mods_batch(domain, chunk, self._on_status_batch)

    def _on_status_batch(self, response) -> None:
        _, domain, _ = response.tag
        game = self._cache.game_id(domain)

        if response.ok:
            for mod in ((response.data or {}).get("data") or {}).get("mods") or []:
                try:
                    mod_id = int(mod.get("id", 0)) - (int(game) << 32)
                except (TypeError, ValueError):
                    continue
                self._seen_status[(domain, mod_id)] = mod
        else:
            self._notes.append(f"{domain}: status check failed ({response.error}).")
            _log.warning(tag(f"Status batch failed for {domain}: {response.error}"))
            if response.unauthorized:
                self.failed.emit(response.error)
                return

        self._pending -= 1
        if self._pending <= 0:
            self._apply_status()

    def _apply_status(self) -> None:
        """Record availability, and rule out mods there is no point checking."""
        self._live: set = set()
        for key, entries in self._by_key.items():
            domain, mod_id = key
            mod = self._seen_status.get(key)

            if mod is None:
                # No row: Nexus does not consider this mod visible.
                for entry in entries:
                    entry.status = ModEntry.HIDDEN
                    entry.message = (
                        "Not visible on Nexus -- hidden, under moderation, or removed."
                    )
                self._cache.put_status(domain, mod_id, "invisible", "")
                continue

            status = str(mod.get("status") or "").lower()
            name = str(mod.get("name") or "")
            self._cache.put_status(domain, mod_id, status or "published", name)
            for entry in entries:
                entry.nexus_name = name

            if status in _GONE_STATUSES:
                for entry in entries:
                    entry.status = ModEntry.DELISTED
                    entry.message = "Removed from Nexus."
                continue
            if status in _HIDDEN_STATUSES:
                for entry in entries:
                    entry.status = ModEntry.HIDDEN
                    entry.message = f"Not publicly available on Nexus (status: {status})."
                continue

            self._live.add(key)

        gone = len(self._by_key) - len(self._live)
        if gone:
            _log.info(tag(f"{gone} page(s) are not visible on Nexus"))
        self._begin_installed()

    # -- phase 4: where each installed file sits ---------------------------

    def _begin_installed(self) -> None:
        wanted: dict[str, list] = {}
        for key in self._live:
            domain, _ = key
            game = self._cache.game_id(domain)
            for entry in self._by_key[key]:
                cached = None
                for file_id in entry.installed_file_ids:
                    cached = self._cache.get_installed(domain, file_id)
                    if cached:
                        entry.chain_id = cached.get("chain") or ""
                        entry.chain_position = float(cached.get("position") or 0)
                        entry.file_line = cached.get("name") or ""
                        break
                if cached or not entry.installed_file_ids:
                    continue
                for file_id in entry.installed_file_ids:
                    wanted.setdefault(domain, []).append(
                        composite_uid(game, file_id)
                    )

        batches = [
            (domain, chunk)
            for domain, uids in wanted.items()
            for chunk in _chunks(sorted(set(uids)))
        ]
        if not batches:
            self._begin_chains()
            return

        _log.info(
            tag(
                f"Resolving {sum(len(c) for _, c in batches)} installed file(s) to "
                f"their update chain in {len(batches)} batch request(s)"
            )
        )
        self.progress.emit("Resolving installed files...", 0, 0)
        self._pending = len(batches)
        for domain, chunk in batches:
            self._client.file_versions_batch(domain, chunk, self._on_installed_batch)

    def _on_installed_batch(self, response) -> None:
        _, domain, _ = response.tag
        game = self._cache.game_id(domain)

        if response.ok:
            for version in ((response.data or {}).get("data") or {}).get("versions") or []:
                try:
                    file_id = int(version.get("id", 0)) - (int(game) << 32)
                except (TypeError, ValueError):
                    continue
                self._cache.put_installed(domain, file_id, version)
        else:
            self._notes.append(
                f"{domain}: could not resolve installed files ({response.error})."
            )
            _log.warning(tag(f"Installed batch failed for {domain}: {response.error}"))

        self._pending -= 1
        if self._pending <= 0:
            self._attach_installed()

    def _attach_installed(self) -> None:
        needs_lookup: list[ModEntry] = []
        for key in self._live:
            domain, _ = key
            for entry in self._by_key[key]:
                if entry.chain_id:
                    continue
                for file_id in entry.installed_file_ids:
                    cached = self._cache.get_installed(domain, file_id)
                    if cached and cached.get("chain"):
                        entry.chain_id = cached["chain"]
                        entry.chain_position = float(cached.get("position") or 0)
                        entry.file_line = cached.get("name") or ""
                        break
                if not entry.chain_id:
                    needs_lookup.append(entry)

        if not needs_lookup:
            self._begin_chains()
            return

        # MO2 only began recording installed file ids at some point, so a few
        # percent of any real profile has nothing to resolve. Those mods need
        # their page's chains listed before they can be placed.
        _log.info(
            tag(f"{len(needs_lookup)} mod(s) have no recorded file id; listing chains")
        )
        self._pending = len(needs_lookup)
        self._lookup = {}
        for entry in needs_lookup:
            game = self._cache.game_id(entry.domain)
            uid = composite_uid(game, entry.mod_id)
            self._lookup.setdefault(uid, []).append(entry)
        self._pending = len(self._lookup)
        for uid, group in self._lookup.items():
            self._client.mod_chains(group[0].domain, uid, self._on_mod_chains)

    def _on_mod_chains(self, response) -> None:
        _, _, uid = response.tag
        group = self._lookup.get(str(uid), [])

        if response.ok:
            chains = ((response.data or {}).get("data") or {}).get("mod_files") or []
            for entry in group:
                chosen = choose_chain(entry, chains)
                if chosen:
                    entry.chain_id = str(chosen.get("id") or "")
                    entry.file_line = str(chosen.get("name") or "")
        else:
            _log.debug(tag(f"Chain listing failed for {uid}: {response.error}"))

        self._pending -= 1
        if self._pending <= 0:
            self._begin_chains()

    # -- phase 5: what is newest in each chain -----------------------------

    def _begin_chains(self) -> None:
        for key in self._live:
            domain, mod_id = key
            changed = self._changed.get(domain)
            for entry in self._by_key[key]:
                if not entry.chain_id:
                    continue
                stale = self._cache.chain_age_days(entry.chain_id) >= self._recheck_days
                touched = changed is None or mod_id in changed
                if touched or stale or self._cache.get_chain(entry.chain_id) is None:
                    self._chain_wanted.setdefault(entry.chain_id, []).append(entry)

        self._total = len(self._chain_wanted)
        self._done = 0
        if not self._chain_wanted:
            self._classify()
            return

        _log.info(tag(f"Fetching {self._total} update chain(s)"))
        self.progress.emit(f"Checking {self._total} update chain(s)...", 0, self._total)
        self._pending = self._total
        for chain_id, group in self._chain_wanted.items():
            self._client.chain_versions(group[0].domain, chain_id, self._on_chain)

    def _on_chain(self, response) -> None:
        _, _, chain_id = response.tag
        if response.ok:
            versions = ((response.data or {}).get("data") or {}).get("versions") or []
            self._cache.put_chain(chain_id, versions)
        else:
            _log.debug(tag(f"Chain {chain_id} failed: {response.error}"))
            for entry in self._chain_wanted.get(str(chain_id), []):
                entry.status = ModEntry.ERROR
                entry.message = response.error

        self._done += 1
        self.progress.emit(
            f"Checked {self._done} of {self._total} update chain(s)...",
            self._done,
            self._total,
        )
        self._pending -= 1
        if self._pending <= 0:
            self._classify()

    # -- classify ----------------------------------------------------------

    def _classify(self) -> None:
        for key in self._live:
            for entry in self._by_key[key]:
                if entry.status in (ModEntry.DELISTED, ModEntry.HIDDEN, ModEntry.ERROR):
                    continue
                self._decide(entry)
        self._complete()

    def _decide(self, entry: ModEntry) -> None:
        versions = self._cache.get_chain(entry.chain_id) if entry.chain_id else None
        if not versions:
            entry.status = ModEntry.UNCHECKED
            entry.message = (
                "Could not work out which upload this came from."
                if not entry.chain_id
                else "No version information for this mod's update chain."
            )
            return

        newest = newest_in_chain(versions)
        entry.latest_file = as_file_record(newest)
        entry.latest_version = entry.latest_file["version"]
        entry.file_line = entry.file_line or entry.latest_file["name"]
        if entry.picked_file_id is None:
            entry.picked_file_id = entry.latest_file["file_id"] or None

        installed_position = entry.chain_position
        if not installed_position:
            # The file id was never recorded, so position within the chain is
            # unknown. Fall back to matching the installed version string.
            match = next(
                (
                    v
                    for v in versions
                    if versions_match(entry.installed_version, str(v.get("version") or ""))
                ),
                None,
            )
            installed_position = position_of(match) if match else 0.0
            entry.chain_position = installed_position

        newest_position = position_of(newest)
        if installed_position and newest_position > installed_position:
            entry.status = ModEntry.UPDATE
            entry.message = ""
            self._note_download(entry)
        elif not installed_position:
            entry.status = ModEntry.UNCHECKED
            entry.message = (
                "MO2 did not record which file this came from, and its version "
                "does not match any upload in the chain."
            )
        else:
            entry.status = ModEntry.CURRENT
            entry.message = ""

    def _note_download(self, entry: ModEntry) -> None:
        """Flag updates that are ignored, or already sitting in the downloads."""
        if is_ignored(entry):
            entry.status = ModEntry.IGNORED
            entry.message = f"Update to {entry.latest_version} ignored in MO2."
            return

        if not self._downloads:
            return

        info = find_download(
            self._downloads, entry.mod_id, (entry.latest_file or {}).get("file_id")
        )
        if info is None or not info.usable:
            return

        entry.download = info
        entry.status = ModEntry.DOWNLOADED
        entry.message = (
            "Already downloaded, not installed."
            if info.state == DOWNLOAD_READY
            else "Already downloaded (MO2 has installed this archive before)."
        )

    # -- wrap up -----------------------------------------------------------

    def _complete(self) -> None:
        error = self._cache.save()
        if error:
            _log.error(tag(error))
            self._notes.append(error)

        counts: dict = {}
        for entry in self._entries:
            counts[entry.status] = counts.get(entry.status, 0) + 1
        _log.info(
            tag(
                "Scan finished: "
                + (", ".join(f"{n} {s}" for s, n in sorted(counts.items())) or "nothing")
            )
        )
        for entry in self._entries:
            if entry.status != ModEntry.CURRENT:
                _log.debug(
                    tag(
                        f"{entry.status} [{entry.domain}/{entry.mod_id}] "
                        f"{entry.display_name}: installed "
                        f"{entry.installed_version or '?'} (chain {entry.chain_id or '-'} "
                        f"pos {entry.chain_position}), latest "
                        f"{entry.latest_version or '?'}"
                        + (f" ({entry.message})" if entry.message else "")
                    )
                )

        self.finished.emit(self._entries, "  ".join(self._notes))
