"""Turn MO2's modlist into a work list of Nexus-backed mods.

Two things here are less obvious than they look.

**Game names.** MO2 records a mod's origin as a *game short name*
(``Starfield``, ``SkyrimSE``) while the Nexus API is keyed on a *domain*
(``starfield``, ``skyrimspecialedition``). ``IPluginGame.gameNexusName()``
does the translation for any game plugin MO2 has loaded, so ask MO2 first and
fall back to a static table only for games it cannot resolve.

**File lines.** A Nexus page can host several unrelated downloads, and plenty
of authors never bump the page version when they update one of them. Comparing
an installed mod against the *page* version therefore both misses updates and
collapses several MO2 mods into one row. What actually matters is the *file
line* -- the sequence of uploads sharing a file name, e.g. "Window Utils"
v1.0.0 -> v1.0.3 alongside "Window Utils Showcase" v1.0.0b -> v1.0.1b on the
same page. MO2 records the exact Nexus file id it installed in the mod's
``meta.ini``, which pins each mod to its line precisely.
"""

from __future__ import annotations

import os
import re
from typing import Optional

import mobase

# Only used when ``organizer.getGame()`` cannot resolve a mod's origin game --
# e.g. a mod installed under a game plugin that is no longer present.
_FALLBACK_DOMAINS = {
    "morrowind": "morrowind",
    "oblivion": "oblivion",
    "oblivionremastered": "oblivionremastered",
    "fallout3": "fallout3",
    "newvegas": "newvegas",
    "falloutnv": "newvegas",
    "ttw": "newvegas",
    "skyrim": "skyrim",
    "skyrimse": "skyrimspecialedition",
    "skyrimvr": "skyrimspecialedition",
    "enderal": "enderal",
    "enderalse": "enderalspecialedition",
    "fallout4": "fallout4",
    "fallout4vr": "fallout4",
    "fo4london": "fallout4",
    "fallout76": "fallout76",
    "starfield": "starfield",
    "cyberpunk2077": "cyberpunk2077",
    "witcher3": "witcher3",
    "stardewvalley": "stardewvalley",
    "baldursgate3": "baldursgate3",
}


class ModEntry:
    """One installed mod that can be checked against Nexus."""

    __slots__ = (
        "internal_name",
        "display_name",
        "mod_id",
        "domain",
        "installed_version",
        "url",
        "enabled",
        "status",
        "latest_version",
        "latest_file_update",
        "nexus_name",
        "message",
        "files",
        "changelog",
        "picked_file_id",
        "installed_file_ids",
        "installation_file",
        "file_line",
        "latest_file",
        "download",
        "ignored_version",
        "page_note",
    )

    # status values
    UPDATE = "update"
    DOWNLOADED = "downloaded"  # the newer file is already in MO2's downloads
    IGNORED = "ignored"  # an update exists, but MO2 was told to ignore it
    DELISTED = "delisted"
    # Not a status: an annotation key, used to colour the mark on rows whose
    # page has moved past the file they were installed from. It was a status
    # once, and being a peer of "Updates available" implied an action that does
    # not exist -- there is no newer file to fetch, only a page worth a glance.
    PAGE_CHANGED = "page_changed"
    HIDDEN = "hidden"
    CURRENT = "current"
    ERROR = "error"
    UNCHECKED = "unchecked"

    def __init__(self, mod, domain: str):
        self.internal_name = mod.name()
        self.display_name = mod.name()
        self.mod_id = mod.nexusId()
        self.domain = domain
        self.installed_version = mod.version().canonicalString()
        self.url = mod.url()
        self.enabled = True
        self.status = self.UNCHECKED
        self.latest_version = ""
        self.latest_file_update = 0
        self.nexus_name = ""
        self.message = ""
        self.files = None  # lazily filled from files.json
        self.changelog = None  # lazily filled from changelogs.json
        self.picked_file_id = None
        self.installation_file = mod.installationFile() or ""
        self.installed_file_ids = read_installed_file_ids(mod.absolutePath())
        self.file_line = ""  # Nexus file name this mod was installed from
        self.latest_file = None  # newest upload in that same file line
        self.download = None  # DownloadInfo when the newer file is already local
        # MO2's "Ignore update" records the version the user dismissed.
        self.ignored_version = mod.ignoredVersion().canonicalString()
        # Set to the page's version when the page has moved past this file.
        # Purely informational -- there is nothing to download.
        self.page_note = ""

    @property
    def row_label(self) -> str:
        """What to show in the list: the file line only when it adds something."""
        if not self.file_line:
            return ""
        if self.file_line.lower() in self.display_name.lower():
            return ""
        return self.file_line

    @property
    def page_url(self) -> str:
        if self.url:
            return self.url
        return f"https://www.nexusmods.com/{self.domain}/mods/{self.mod_id}"

    @property
    def key(self) -> tuple[str, int]:
        return (self.domain, self.mod_id)

    def __repr__(self):
        return f"<ModEntry {self.display_name!r} {self.domain}/{self.mod_id} {self.status}>"


def read_installed_file_ids(mod_path: str) -> list[int]:
    """Pull the Nexus file ids out of a mod's ``meta.ini``.

    MO2 records them under ``[installedFiles]`` as ``<n>\\fileid=<id>``. There
    is no accessor for this on ``IModInterface``, and the value is worth having:
    it identifies the exact upload a mod came from, which is the only reliable
    way to tell two downloads from the same Nexus page apart.
    """
    if not mod_path:
        return []

    path = os.path.join(mod_path, "meta.ini")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return []

    ids: list[int] = []
    in_section = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            in_section = stripped.lower() == "[installedfiles]"
            continue
        if not in_section or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        if key.strip().lower().endswith("fileid"):
            try:
                file_id = int(value.strip())
            except ValueError:
                continue
            if file_id > 0 and file_id not in ids:
                ids.append(file_id)
    return ids


def nexus_domain(organizer: mobase.IOrganizer, game_name: str) -> str:
    """Map an MO2 game short name to its Nexus domain, '' when unknown."""
    if not game_name:
        game = organizer.managedGame()
        if game is None:
            return ""
        return game.gameNexusName() or _FALLBACK_DOMAINS.get(
            game.gameShortName().lower(), ""
        )

    game = organizer.getGame(game_name)
    if game is not None:
        domain = game.gameNexusName()
        if domain:
            return domain

    return _FALLBACK_DOMAINS.get(game_name.lower(), "")


def collect_mods(
    organizer: mobase.IOrganizer, include_disabled: bool = True
) -> tuple[list[ModEntry], list[str], int]:
    """Gather every Nexus-backed mod in the current profile.

    Returns ``(entries, skipped, disabled)``: mods to check, the names of mods
    with no usable Nexus id -- separators, hand-built mods, output folders --
    and how many were left out for being disabled.
    """
    mod_list = organizer.modList()
    entries: list[ModEntry] = []
    skipped: list[str] = []
    disabled = 0

    for name in mod_list.allMods():
        mod = mod_list.getMod(name)
        if mod is None:
            continue

        # Separators, backups and the overwrite folder are not real mods, and
        # foreign entries (DLC, Creation Club) are managed by the game itself.
        if mod.isSeparator() or mod.isBackup() or mod.isOverwrite() or mod.isForeign():
            continue

        mod_id = mod.nexusId()
        if mod_id is None or mod_id <= 0:
            skipped.append(name)
            continue

        domain = nexus_domain(organizer, mod.gameName())
        if not domain:
            skipped.append(name)
            continue

        enabled = bool(mod_list.state(name) & mobase.ModState.active)
        if not enabled and not include_disabled:
            disabled += 1
            continue

        # Several MO2 mods can share one Nexus page -- a main file plus an
        # addon, say. Each keeps its own row and its own file line; the page
        # itself is only queried once (see UpdateScan).
        entry = ModEntry(mod, domain)
        entry.enabled = enabled
        entries.append(entry)

    entries.sort(key=lambda e: e.display_name.lower())
    return entries, skipped, disabled


_SUPERSEDED = ("OLD_VERSION", "ARCHIVED")

# A version stamped into a file name: "1.0.81", "v2", "1.0.0b". Anchored on
# word boundaries so "4K" and "2K" in a texture pack's name survive.
_VERSION_TOKEN = re.compile(r"\b[vV]?\d+(?:[._]\d+)*[a-z]?\b")


def _squash(text: str) -> str:
    """Reduce a name to bare lowercase alphanumerics for loose comparison."""
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def line_key(name: str) -> str:
    """The identity of a file *line*, ignoring any version in its name.

    Some authors name every upload after its version -- "World Builder 1.0.0",
    "World Builder 1.0.81" -- which would otherwise make each upload its own
    one-member line and hide every update on that page.
    """
    stripped = _squash(_VERSION_TOKEN.sub(" ", name or ""))
    return stripped or _squash(name)


def resolve_file_line(entry: ModEntry, files: list) -> tuple[Optional[dict], Optional[dict]]:
    """Work out which upload this mod came from, and what replaced it.

    Returns ``(installed_record, latest_record)``. Either may be None when the
    page's files cannot be matched -- the caller then falls back to the page
    version.
    """
    if not files:
        return None, None

    installed = _match_installed(entry, files)
    if installed is None:
        return None, None

    line = line_key(str(installed.get("name") or ""))
    same_line = [f for f in files if line_key(str(f.get("name") or "")) == line]

    # Prefer a current upload; if the whole line has been superseded, the
    # newest entry in it is still the honest answer.
    live = [
        f
        for f in same_line
        if str(f.get("category_name") or "").upper() not in _SUPERSEDED
    ]
    pool = live or same_line
    latest = max(pool, key=lambda f: int(f.get("uploaded_timestamp") or 0))
    return installed, latest


def _match_installed(entry: ModEntry, files: list) -> Optional[dict]:
    by_id = {int(f.get("file_id") or 0): f for f in files}

    # 1. The file id MO2 recorded at install time. Exact, when it is there.
    for file_id in entry.installed_file_ids:
        if file_id in by_id:
            return by_id[file_id]

    # 2. The installed version, matched against each upload's own version.
    wanted = _normalize(entry.installed_version)
    if wanted:
        matches = [f for f in files if _normalize(str(f.get("version") or "")) == wanted]
        if len(matches) == 1:
            return matches[0]
        if matches:
            # Ambiguous: fall through to the archive name to break the tie.
            files = matches

    # 3. The name of the archive it was installed from. Nexus file names are
    #    a prefix of the download filename, so take the longest that fits --
    #    "Window Utils Showcase" must win over "Window Utils".
    archive = _squash(entry.installation_file)
    if archive:
        candidates = [
            f
            for f in files
            if _squash(str(f.get("name") or "")) and _squash(str(f.get("name") or "")) in archive
        ]
        if candidates:
            return max(candidates, key=lambda f: len(_squash(str(f.get("name") or ""))))

    return None


def _normalize(version: str) -> str:
    """Strip a leading 'v' and trailing all-zero segments.

    MO2 canonicalises to four segments, Nexus rarely does, so "1.2" and
    "1.2.0.0" have to compare equal or every scan reports a phantom update.
    """
    text = (version or "").strip().lower()
    while text.startswith("v"):
        text = text[1:].lstrip()

    parts = text.split(".")
    while len(parts) > 2 and parts[-1] in ("0", ""):
        parts.pop()
    return ".".join(parts)


# Only a plain dotted number counts here. Anything else -- "1.0.0joker",
# "1.0.1b", a date, a build string -- means the author is numbering that file
# on its own scheme, and comparing it with the page version is meaningless.
_PLAIN_VERSION = re.compile(r"^\d+(?:\.\d+)*$")


def _plain_version(text: str) -> Optional[tuple]:
    """Parse a strictly numeric version, or None if it is anything else.

    Deliberately stricter than ``mobase.VersionInfo``, whose regex is a prefix
    match (``versioninfo.cpp:27``) and so reads "1.0.0joker" as a perfectly
    good 1.0.0. That leniency is right when asking "is there a newer file in
    this line?" and wrong when asking "has the page overtaken my file?", where
    it manufactures comparisons between unrelated numbering schemes.
    """
    cleaned = (text or "").strip()
    if cleaned[:1].lower() == "v":
        cleaned = cleaned[1:].strip()
    if not cleaned or not _PLAIN_VERSION.match(cleaned):
        return None
    return tuple(int(part) for part in cleaned.split("."))


def page_ahead_of(file_version: str, page_version: str) -> bool:
    """True when the page's own version has run past a file's version."""
    mine = _plain_version(file_version)
    theirs = _plain_version(page_version)
    if mine is None or theirs is None:
        return False

    width = max(len(mine), len(theirs))
    mine += (0,) * (width - len(mine))
    theirs += (0,) * (width - len(theirs))
    return theirs > mine


def is_primary_file(info: dict) -> bool:
    """True for the upload a page's own version number tracks."""
    if info.get("is_primary"):
        return True
    return str(info.get("category_name") or "").upper() == "MAIN"


def is_ignored(entry: ModEntry) -> bool:
    """True when MO2 was told to ignore exactly this version.

    MO2 dismisses a *specific* version, so anything newer than what the user
    waved away should start nagging again.
    """
    ignored = (entry.ignored_version or "").strip()
    if not ignored or not entry.latest_version:
        return False
    if _normalize(ignored) == _normalize(entry.latest_version):
        return True
    # Ignored something even newer (rare, but possible after a downgrade).
    return not is_newer(ignored, entry.latest_version)


def is_newer(installed: str, latest: str) -> bool:
    """True when ``latest`` looks like a genuine upgrade over ``installed``.

    Falls back to a plain string difference when the versions do not parse --
    Nexus version strings are free text and plenty of authors use dates, build
    numbers, or nothing at all.
    """
    if not latest:
        return False
    if not installed:
        return True

    if _normalize(installed) == _normalize(latest):
        return False

    try:
        current = mobase.VersionInfo(installed)
        newest = mobase.VersionInfo(latest)
    except Exception:
        return True

    if not current.isValid() or not newest.isValid():
        return True

    if newest > current:
        return True
    if current >= newest:
        # Installed build is equal or ahead once parsed -- treat the textual
        # difference as cosmetic rather than nagging every scan.
        return False
    return True
