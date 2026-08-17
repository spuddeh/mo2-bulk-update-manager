"""The scan engine: decide what to ask Nexus, then classify the answers.

Two modes:

*Fast* (the default) leans on ``games/{domain}/mods/updated.json``, which
reports every mod in a game touched inside a 1d/1w/1m window in a single
request. Only the intersection with the installed modlist gets a follow-up
call, plus anything never seen before and a rotating slice of stale records so
delistings still surface without a full sweep.

*Deep* asks about every installed mod. It is the only way to be certain about
mods that were pulled from Nexus a long time ago, so it is offered explicitly
rather than run on every check.

Each page that does get queried costs two requests -- the page for its
availability, and its file list. The file list is what makes the comparison
honest: an author who updates one download without bumping the page version
would otherwise look up to date, and two MO2 mods installed from the same page
would otherwise be indistinguishable.
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
from .scanner import (
    ModEntry,
    is_ignored,
    is_newer,
    is_primary_file,
    page_ahead_of,
    resolve_file_line,
)

# Nexus only accepts these three windows.
_PERIODS = (("1d", 1), ("1w", 7), ("1m", 28))

# Statuses Nexus reports for pages that are no longer normally reachable.
_GONE_STATUSES = {"removed", "wastebinned", "deleted"}
_HIDDEN_STATUSES = {"hidden", "under_moderation", "not_published", "publish_with_game"}


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
        self._recheck_days = 30
        self._max_recheck = 25
        self._pending_domains = 0
        self._pending_requests = 0
        self._done_pages = 0
        self._total_pages = 0
        self._notes: list[str] = []
        self._candidates: list[tuple[str, int]] = []
        self._replies: dict = {}
        self._downloads: dict = {}
        self.user: dict = {}

    # -- entry point -------------------------------------------------------

    def start(
        self,
        entries: list[ModEntry],
        deep: bool,
        recheck_days: int = 30,
        downloads: Optional[dict] = None,
    ) -> None:
        self._entries = entries
        self._downloads = downloads or {}
        self._deep = deep
        self._recheck_days = max(1, recheck_days)
        self._notes = []
        self._by_key = {}
        for entry in entries:
            self._by_key.setdefault(entry.key, []).append(entry)

        if not entries:
            self.finished.emit([], "No Nexus-backed mods found in this profile.")
            return

        self.progress.emit("Checking your Nexus login...", 0, 0)
        self._client.validate(self._on_validated)

    # -- phase 1: credentials ---------------------------------------------

    def _on_validated(self, response) -> None:
        if not response.ok:
            self.failed.emit(response.error)
            return

        self.user = response.data or {}
        self.identified.emit(self.user)
        self._begin_domains()

    # -- phase 2: which pages are worth asking about -----------------------

    def _begin_domains(self) -> None:
        domains = sorted({entry.domain for entry in self._entries})

        if self._deep:
            self._candidates = sorted(self._by_key.keys())
            for domain in domains:
                self._cache.mark_scan(domain, full=True)
            self._begin_page_lookups()
            return

        self._candidates = []
        self._pending_domains = len(domains)
        for domain in domains:
            period = self._period_for(domain)
            if period is None:
                # Cache is too old for any window Nexus offers; sweep this game.
                self._notes.append(
                    f"{domain}: last check was too long ago for a quick lookup, "
                    "so every mod was queried."
                )
                self._candidates.extend(key for key in self._by_key if key[0] == domain)
                self._cache.mark_scan(domain, full=True)
                self._domain_done()
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

        if not response.ok:
            if response.unauthorized:
                self.failed.emit(response.error)
                return
            self._notes.append(
                f"{domain}: quick lookup failed ({response.error}) so every mod was queried."
            )
            self._candidates.extend(key for key in self._by_key if key[0] == domain)
            self._domain_done()
            return

        rows = response.data or []
        changed = {int(r["mod_id"]): int(r.get("latest_file_update") or 0) for r in rows if r.get("mod_id")}

        stale: list[tuple[float, tuple[str, int]]] = []
        for key in self._by_key:
            if key[0] != domain:
                continue
            _, mod_id = key
            record = self._cache.get(domain, mod_id)

            if mod_id in changed:
                self._candidates.append(key)
            elif record is None or self._cache.get_files(domain, mod_id) is None:
                # Never seen, or seen before the file list was being cached.
                self._candidates.append(key)
            else:
                age = self._cache.age_days(domain, mod_id)
                if age >= self._recheck_days:
                    stale.append((age, key))

        # Re-verify the oldest records so delistings surface over time without
        # a full sweep every run.
        stale.sort(reverse=True)
        rotated = [key for _, key in stale[: self._max_recheck]]
        self._candidates.extend(rotated)
        if len(stale) > len(rotated):
            self._notes.append(
                f"{domain}: {len(stale) - len(rotated)} mod(s) are due a "
                "delisting re-check; run a deep scan to cover them all now."
            )

        self._cache.mark_scan(domain, full=False)
        self._domain_done()

    def _domain_done(self) -> None:
        self._pending_domains -= 1
        if self._pending_domains <= 0:
            self._begin_page_lookups()

    # -- phase 3: ask about each candidate page ----------------------------

    def _begin_page_lookups(self) -> None:
        self._candidates = sorted(set(self._candidates))
        self._total_pages = len(self._candidates)
        self._done_pages = 0
        self._pending_requests = self._total_pages * 2
        self._replies = {key: {} for key in self._candidates}

        # Everything not queried keeps whatever the cache last knew.
        queried = set(self._candidates)
        for key, entries in self._by_key.items():
            if key in queried:
                continue
            record = self._cache.get(*key)
            files = self._cache.get_files(*key)
            for entry in entries:
                self._apply_cached(entry, record, files)

        if not self._candidates:
            self._complete()
            return

        self.progress.emit(
            f"Checking {self._total_pages} Nexus page(s)...", 0, self._total_pages
        )
        self._client.reset_progress()
        for domain, mod_id in self._candidates:
            self._client.mod_info(domain, mod_id, self._on_mod_info)
            self._client.mod_files(domain, mod_id, self._on_mod_files)

    def _on_mod_info(self, response) -> None:
        _, domain, mod_id = response.tag
        key = (domain, mod_id)
        state = self._replies.get(key)
        if state is None:
            return

        if response.ok:
            data = response.data or {}
            status = str(data.get("status") or "").lower() or "published"
            available = data.get("available")
            page = {
                "status": status,
                "available": True if available is None else bool(available),
                "version": str(data.get("version") or ""),
                "name": str(data.get("name") or ""),
                "updated": int(data.get("updated_timestamp") or 0),
            }
            state["page"] = page
            self._cache.put(
                domain,
                mod_id,
                version=page["version"],
                name=page["name"],
                status=page["status"],
                available=page["available"],
                latest_file_update=page["updated"],
            )

        elif response.missing:
            state["page"] = {
                "status": "deleted",
                "available": False,
                "version": "",
                "name": "",
                "updated": 0,
            }
            self._cache.put(
                domain,
                mod_id,
                version="",
                name="",
                status="deleted",
                available=False,
                latest_file_update=0,
            )

        else:
            state["page_error"] = response.error
            if response.unauthorized:
                self._settle(key, "page")
                self.failed.emit(response.error)
                return

        self._settle(key, "page")

    def _on_mod_files(self, response) -> None:
        _, domain, mod_id = response.tag
        key = (domain, mod_id)
        state = self._replies.get(key)
        if state is None:
            return

        if response.ok:
            files = (response.data or {}).get("files") or []
            state["files"] = files
            self._cache.put_files(domain, mod_id, files)
        else:
            # A missing file list is not fatal; the page version still gives a
            # usable, if coarser, answer.
            state["files"] = self._cache.get_files(domain, mod_id) or []

        self._settle(key, "files")

    def _settle(self, key, which: str) -> None:
        state = self._replies.get(key)
        if state is None:
            return
        if state.get(which + "_done"):
            return
        state[which + "_done"] = True

        # Classify before counting: _count_request finishes the whole scan when
        # the last request lands, and the results have to be in by then.
        if state.get("page_done") and state.get("files_done"):
            self._classify(key, state)
        self._count_request(key)

    # -- phase 4: classify -------------------------------------------------

    def _classify(self, key, state: dict) -> None:
        entries = self._by_key.get(key, [])
        page = state.get("page")
        files = state.get("files") or []

        if page is None:
            error = state.get("page_error") or "Could not be checked."
            for entry in entries:
                entry.status = ModEntry.ERROR
                entry.message = error
            return

        status = page["status"]
        if status in _GONE_STATUSES:
            verdict, note = ModEntry.DELISTED, "Removed from Nexus."
        elif not page["available"] or status in _HIDDEN_STATUSES:
            verdict = ModEntry.HIDDEN
            note = f"Not publicly available on Nexus (status: {status})."
        else:
            verdict, note = None, ""

        for entry in entries:
            entry.nexus_name = page["name"]
            entry.files = files or None
            if verdict is not None:
                entry.status = verdict
                entry.message = note
                entry.latest_version = page["version"]
                continue
            self._compare(entry, page, files)

    def _compare(self, entry: ModEntry, page: dict, files: list) -> None:
        """Decide update-or-not for one mod against its own file line."""
        installed, latest = resolve_file_line(entry, files)

        if latest is not None:
            entry.file_line = str(installed.get("name") or "")
            entry.latest_file = latest
            entry.latest_version = str(latest.get("version") or "")
            entry.latest_file_update = int(latest.get("uploaded_timestamp") or 0)
            if entry.picked_file_id is None:
                entry.picked_file_id = latest.get("file_id")

            if installed.get("file_id") != latest.get("file_id"):
                entry.status = ModEntry.UPDATE
                entry.message = (
                    ""
                    if is_newer(entry.installed_version, entry.latest_version)
                    # A newer upload whose version string is not higher: a
                    # re-upload or a silent hotfix. Still worth surfacing.
                    else "Newer upload with the same version number."
                )
            elif self._page_moved_on(installed, page):
                # This file line is current, but the page has moved past it --
                # typically an optional add-on for a main file that has since
                # been updated. MO2 reports that as an update. It is not one:
                # there is no newer file to fetch, so this is an annotation on
                # an up-to-date mod rather than a category of its own.
                entry.status = ModEntry.CURRENT
                entry.page_note = page.get("version", "")
                entry.latest_version = entry.page_note or entry.latest_version
                entry.message = (
                    f"Your file is the newest of its kind. The page itself is now at "
                    f"{entry.page_note}, so check it if this stops working."
                )
            else:
                entry.status = ModEntry.CURRENT
                entry.message = ""

            self._note_download(entry)
            return

        # No file line could be matched, so fall back to the page version.
        entry.latest_version = page.get("version", "")
        entry.latest_file_update = page.get("updated") or entry.latest_file_update
        if is_newer(entry.installed_version, entry.latest_version):
            entry.status = ModEntry.UPDATE
            entry.message = "Matched on the page version; check the file yourself."
            self._note_download(entry)
        else:
            entry.status = ModEntry.CURRENT
            entry.message = ""

    @staticmethod
    def _page_moved_on(installed: dict, page: dict) -> bool:
        """True when the page's own version has run past the installed file.

        Deliberately narrow. A Nexus page gains uploads constantly --
        translations, optional extras, patches -- and none of those mean the
        file you have is stale. An earlier version of this checked for *any*
        newer upload and flagged eleven mods that were perfectly current,
        because someone had added a French translation.

        Two conditions, both required:

        * The installed file is not the page's primary upload. If it is, the
          page version tracks it by definition and can never be ahead.
        * The page version genuinely parses as newer than the file's version.
        """
        if is_primary_file(installed):
            return False
        return page_ahead_of(
            str(installed.get("version") or ""), str(page.get("version") or "")
        )

    def _note_download(self, entry: ModEntry) -> None:
        """Flag updates whose file is already sitting in MO2's downloads."""
        if entry.status != ModEntry.UPDATE:
            return

        if is_ignored(entry):
            entry.status = ModEntry.IGNORED
            entry.message = f"Update to {entry.latest_version} ignored in MO2."
            return

        if not self._downloads:
            return

        file_id = (entry.latest_file or {}).get("file_id")
        info = find_download(self._downloads, entry.mod_id, file_id)
        if info is None or not info.usable:
            return

        entry.download = info
        entry.status = ModEntry.DOWNLOADED
        entry.message = (
            "Already downloaded, not installed."
            if info.state == DOWNLOAD_READY
            else "Already downloaded (MO2 has installed this archive before)."
        )

    def _count_request(self, key) -> None:
        self._pending_requests -= 1
        done = sum(
            1
            for state in self._replies.values()
            if state.get("page_done") and state.get("files_done")
        )
        if done != self._done_pages:
            self._done_pages = done
            self.progress.emit(
                f"Checked {done} of {self._total_pages} Nexus page(s)...",
                done,
                self._total_pages,
            )
        if self._pending_requests <= 0:
            self._complete()

    # -- phase 5: wrap up --------------------------------------------------

    def _apply_cached(
        self, entry: ModEntry, record: Optional[dict], files: Optional[list]
    ) -> None:
        if not record:
            entry.status = ModEntry.UNCHECKED
            entry.message = "Not checked yet."
            return

        entry.nexus_name = record.get("name", "")
        entry.files = files or None
        status = str(record.get("status") or "")

        if status in _GONE_STATUSES:
            entry.latest_version = record.get("version", "")
            entry.status = ModEntry.DELISTED
            entry.message = "Removed from Nexus (from the last check)."
            return

        if not record.get("available", True) or status in _HIDDEN_STATUSES:
            entry.latest_version = record.get("version", "")
            entry.status = ModEntry.HIDDEN
            entry.message = "Not publicly available on Nexus (from the last check)."
            return

        self._compare(
            entry,
            {
                "version": record.get("version", ""),
                "updated": int(record.get("latest_file_update") or 0),
            },
            files or [],
        )

    def _complete(self) -> None:
        error = self._cache.save()
        if error:
            self._notes.append(error)

        pages = len(self._by_key)
        if self._total_pages < pages and not self._deep:
            self._notes.insert(
                0,
                f"Queried {self._total_pages} of {pages} Nexus page(s); the rest came "
                "from the cache.",
            )

        self.finished.emit(self._entries, "  ".join(self._notes))
