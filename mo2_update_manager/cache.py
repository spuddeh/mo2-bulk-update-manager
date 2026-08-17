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
#    page versions.
# 3: moved classification onto the v3 API -- update chains with an explicit
#    position instead of file lines inferred from names and version strings.
#    Older caches are discarded rather than migrated; a rebuild is one scan.
SCHEMA_VERSION = 3

# Fields worth keeping from each v3 chain version. `game_scoped_id` is the
# legacy file id, which is what MO2 records and what downloads are keyed on.
_VERSION_FIELDS = (
    "id",
    "game_scoped_id",
    "name",
    "version",
    "position",
    "category",
    "is_primary",
    "uploaded_at",
)

# Kept from each v1 file record, for the Files tab only -- v3 does not carry
# size or description, and that pane is fetched lazily anyway.
_FILE_FIELDS = ("file_id", "name", "version", "uploaded_timestamp", "category_name")


class ScanCache:
    def __init__(self, directory: str):
        self._path = os.path.join(directory, CACHE_FILENAME)
        self._mods: dict[str, dict] = {}
        self._games: dict[str, dict] = {}
        # v3 update chains, keyed by chain id, shared across every mod that
        # draws from them.
        self._chains: dict[str, dict] = {}
        # Installed file id -> the chain and position it resolved to.
        self._installed: dict[str, dict] = {}
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
        self._chains = raw.get("chains") or {}
        self._installed = raw.get("installed") or {}

    def save(self) -> Optional[str]:
        """Write the cache back. Returns an error string on failure."""
        if not self._dirty:
            return None
        payload = {
            "schema": SCHEMA_VERSION,
            "mods": self._mods,
            "games": self._games,
            "chains": self._chains,
            "installed": self._installed,
        }
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
        self._chains = {}
        self._installed = {}
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

    # -- v3: update chains -------------------------------------------------

    def game_id(self, domain: str) -> Optional[int]:
        value = (self._games.get(domain) or {}).get("game_id")
        return int(value) if value else None

    def put_game_id(self, domain: str, game_id: int) -> None:
        self._games.setdefault(domain, {})["game_id"] = int(game_id)
        self._dirty = True

    def put_installed(self, domain: str, file_id: int, record: dict) -> None:
        """Remember which chain an installed file belongs to, and where in it.

        This never changes for a given file id, so once resolved it never needs
        asking again.
        """
        self._installed[self._key(domain, file_id)] = {
            "chain": str(record.get("mod_file_id") or ""),
            "position": str(record.get("position") or "0"),
            "version": str(record.get("version") or ""),
            "name": str(record.get("name") or ""),
        }
        self._dirty = True

    def get_installed(self, domain: str, file_id: int) -> Optional[dict]:
        return self._installed.get(self._key(domain, file_id))

    def put_chain(self, chain_id, versions: list) -> None:
        self._chains[str(chain_id)] = {
            "versions": [
                {field: v.get(field) for field in _VERSION_FIELDS}
                for v in (versions or [])
            ],
            "checked": int(time.time()),
        }
        self._dirty = True

    def get_chain(self, chain_id) -> Optional[list]:
        record = self._chains.get(str(chain_id))
        return record.get("versions") if record else None

    def chain_age_days(self, chain_id) -> float:
        record = self._chains.get(str(chain_id))
        if not record:
            return float("inf")
        return max(0.0, (time.time() - record.get("checked", 0)) / 86400.0)

    def put_status(self, domain: str, mod_id: int, status: str, name: str) -> None:
        record = self._mods.setdefault(self._key(domain, mod_id), {})
        record.update(
            {"status": status, "name": name, "checked": int(time.time())}
        )
        self._dirty = True

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
