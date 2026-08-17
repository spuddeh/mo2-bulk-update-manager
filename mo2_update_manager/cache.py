"""On-disk memory of what Nexus last told us about each mod.

Without this every scan would have to ask Nexus about every mod, which is
exactly what makes MO2's built-in check painful on a large modlist. With it,
a routine scan costs one request per game plus a handful of follow-ups.

The file lives in MO2's shared plugin data directory, so several instances
managing the same game reuse each other's results.
"""

from __future__ import annotations

import json
import os
import time
from typing import Optional

CACHE_FILENAME = "update_manager_cache.json"

# 2: added the per-page file list, needed to compare file lines rather than
#    page versions. Older caches are discarded rather than migrated.
SCHEMA_VERSION = 2

# Fields worth keeping from each file record; the rest is refetched on demand.
_FILE_FIELDS = ("file_id", "name", "version", "uploaded_timestamp", "category_name")


class ScanCache:
    def __init__(self, directory: str):
        self._path = os.path.join(directory, CACHE_FILENAME)
        self._mods: dict[str, dict] = {}
        self._games: dict[str, dict] = {}
        self._dirty = False
        self._load()

    # -- persistence -------------------------------------------------------

    def _load(self) -> None:
        try:
            with open(self._path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, ValueError):
            return

        if raw.get("schema") != SCHEMA_VERSION:
            return

        self._mods = raw.get("mods") or {}
        self._games = raw.get("games") or {}

    def save(self) -> Optional[str]:
        """Write the cache back. Returns an error string on failure."""
        if not self._dirty:
            return None
        payload = {"schema": SCHEMA_VERSION, "mods": self._mods, "games": self._games}
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            temp = self._path + ".tmp"
            with open(temp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, separators=(",", ":"))
            os.replace(temp, self._path)
        except OSError as exc:
            return f"Could not save the update cache: {exc}"
        self._dirty = False
        return None

    def clear(self) -> None:
        self._mods = {}
        self._games = {}
        self._dirty = True

    # -- per-mod records ---------------------------------------------------

    @staticmethod
    def _key(domain: str, mod_id: int) -> str:
        return f"{domain}/{mod_id}"

    def get(self, domain: str, mod_id: int) -> Optional[dict]:
        return self._mods.get(self._key(domain, mod_id))

    def put(
        self,
        domain: str,
        mod_id: int,
        *,
        version: str,
        name: str,
        status: str,
        available: bool,
        latest_file_update: int,
    ) -> None:
        record = self._mods.setdefault(self._key(domain, mod_id), {})
        record.update(
            {
                "version": version,
                "name": name,
                "status": status,
                "available": available,
                "latest_file_update": int(latest_file_update or 0),
                "checked": int(time.time()),
            }
        )
        self._dirty = True

    def put_files(self, domain: str, mod_id: int, files: list) -> None:
        """Remember the page's uploads so a quiet page needs no second request."""
        trimmed = [
            {field: info.get(field) for field in _FILE_FIELDS} for info in (files or [])
        ]
        record = self._mods.setdefault(self._key(domain, mod_id), {})
        record["files"] = trimmed
        self._dirty = True

    def get_files(self, domain: str, mod_id: int) -> Optional[list]:
        record = self.get(domain, mod_id)
        return record.get("files") if record else None

    def age_days(self, domain: str, mod_id: int) -> float:
        record = self.get(domain, mod_id)
        if not record:
            return float("inf")
        return max(0.0, (time.time() - record.get("checked", 0)) / 86400.0)

    # -- per-game bookkeeping ----------------------------------------------

    def last_full_scan(self, domain: str) -> int:
        return int((self._games.get(domain) or {}).get("last_full", 0))

    def last_scan(self, domain: str) -> int:
        return int((self._games.get(domain) or {}).get("last_scan", 0))

    def mark_scan(self, domain: str, full: bool) -> None:
        record = self._games.setdefault(domain, {})
        record["last_scan"] = int(time.time())
        if full:
            record["last_full"] = int(time.time())
        self._dirty = True
