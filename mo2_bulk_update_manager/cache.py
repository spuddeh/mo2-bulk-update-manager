"""On-disk memory of what Nexus last reported for each mod.

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

# `pluginDataPath()` is shared by every plugin, so this name has to be
# unmistakably this plugin's. Nothing here reads or removes a file it did not write:
# a neighbouring plugin's data is not this plugin's to touch, whatever it is
# called.
CACHE_FILENAME = "bulk_update_manager_cache.json"

# 2: added the per-page file list, needed to compare file lines rather than
#    page versions.
# 3: moved classification onto the v3 API -- update chains with an explicit
#    position instead of file lines inferred from names and version strings.
#    Older caches are discarded rather than migrated; a rebuild is one scan.
SCHEMA_VERSION = 3

# Bumped whenever the rule for choosing a mod's chain changes, so answers it
# produced are dropped without discarding the chain contents and installed-file
# resolutions, which are expensive and still correct.
CHAIN_MATCH_REVISION = 3

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
        # Chain choices made for mods with no recorded file id, keyed by the
        # evidence used rather than by the page -- several MO2 mods can share
        # one page and land on different chains.
        self._mod_chains: dict[str, dict] = {}
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
        self._mod_chains = raw.get("mod_chains") or {}

        if raw.get("chain_match") != CHAIN_MATCH_REVISION:
            # The rule for picking a mod's chain has changed, so any chain
            # chosen by the old one has to go. Everything else survives.
            self._mod_chains = {}
            for record in self._mods.values():
                record.pop("chain", None)
                record.pop("chain_name", None)
            self._dirty = True

    def save(self) -> Optional[str]:
        """Write the cache back. Returns an error string on failure."""
        if not self._dirty:
            return None
        payload = {
            "schema": SCHEMA_VERSION,
            "chain_match": CHAIN_MATCH_REVISION,
            "mods": self._mods,
            "games": self._games,
            "chains": self._chains,
            "installed": self._installed,
            "mod_chains": self._mod_chains,
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
        self._mod_chains = {}
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

    def put_mod_chain(
        self, domain: str, mod_id: int, signature: str, chain_id: str, name: str
    ) -> None:
        """Remember the chain a mod resolved to when it had no file id to use.

        Keyed on the evidence that produced the answer, not on the mod page:
        page 9643 hosts LaserSightDots_Enabled and its BulletFollowsDot
        variant, and two MO2 mods installed from it must not share one entry --
        keying by page let whichever was seen last overwrite the other.
        """
        self._mod_chains[f"{domain}/{mod_id}/{signature}"] = {
            "chain": str(chain_id),
            "name": name,
        }
        self._dirty = True

    def get_mod_chain(self, domain: str, mod_id: int, signature: str) -> Optional[tuple]:
        record = self._mod_chains.get(f"{domain}/{mod_id}/{signature}")
        if not record or not record.get("chain"):
            return None
        return record["chain"], record.get("name") or ""

    def put_status(self, domain: str, mod_id: int, status: str, name: str) -> None:
        record = self._mods.setdefault(self._key(domain, mod_id), {})
        record.update(
            {"status": status, "name": name, "checked": int(time.time())}
        )
        self._dirty = True

    # -- per-game bookkeeping ----------------------------------------------

    def last_scan(self, domain: str) -> int:
        return int((self._games.get(domain) or {}).get("last_scan", 0))

    def mark_scan(self, domain: str) -> None:
        self._games.setdefault(domain, {})["last_scan"] = int(time.time())
        self._dirty = True
