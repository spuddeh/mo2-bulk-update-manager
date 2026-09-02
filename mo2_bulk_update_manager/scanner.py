"""Turn MO2's modlist into a work list of Nexus-backed mods.

Two things here are less obvious than they look.

**Game names.** MO2 records a mod's origin as a *game short name*
(``Starfield``, ``SkyrimSE``) while the Nexus API is keyed on a *domain*
(``starfield``, ``skyrimspecialedition``). ``IPluginGame.gameNexusName()``
does the translation for any game plugin MO2 has loaded, so ask MO2 first and
fall back to a static table only for games it cannot resolve.

**Update chains.** A Nexus page can host several unrelated downloads, and
plenty of authors never bump the page version when they update one of them.
Comparing an installed mod against the *page* version therefore both misses
updates and collapses several MO2 mods into one row. What matters is the
sequence a file belongs to -- "Window Utils" v1.0.0 -> v1.0.3 alongside "Window
Utils Showcase" v1.0.0b -> v1.0.1b on the same page. The v3 API calls that an
*update chain* and states it outright, so it no longer has to be inferred from
names and version strings; ``choose_chain`` here only covers the mods MO2 never
recorded a file id for, and everything else lives in ``updater``.

``read_installed_file_ids`` is what pins a mod to its chain, because MO2
records the exact Nexus file id it installed in the mod's ``meta.ini`` and
exposes no accessor for it.
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


# Per-mod plugin settings. MO2 keeps these in the mod's own meta.ini under
# ``[Plugins]`` and writes them itself, so this is the one place the plugin can
# remember something about a mod without editing a file MO2 owns.
FORCE_SETTING = "force_update"  # a version to offer despite MO2's ignore flag
NOTE_SETTING = "note"  # why the user decided whatever they decided
NOTE_VERSION_SETTING = "note_version"  # the latest version when they wrote it


def _text(value) -> str:
    """A plugin setting as a string, whatever MO2 hands back for an empty one."""
    return "" if value is None else str(value)


class ModEntry:
    """One installed mod that can be checked against Nexus."""

    __slots__ = (
        "internal_name",
        "display_name",
        "mod_id",
        "domain",
        "installed_version",
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
        "forced_version",
        "note",
        "note_version",
        "chain_id",
        "chain_position",
    )

    # status values
    UPDATE = "update"
    DOWNLOADING = "downloading"  # queued with MO2, not finished yet
    DOWNLOADED = "downloaded"  # the newer file is already in MO2's downloads
    IGNORED = "ignored"  # an update exists, but MO2 was told to ignore it
    # Nexus has retired the installed file, its update chain holds nothing
    # newer, and the page version does not settle what replaced it. Something
    # on the page probably supersedes it, but only a person can say what.
    SUPERSEDED = "superseded"
    DELISTED = "delisted"
    HIDDEN = "hidden"
    CURRENT = "current"
    ERROR = "error"
    UNCHECKED = "unchecked"

    def __init__(self, mod, domain: str, settings: Optional[dict] = None):
        self.internal_name = mod.name()
        self.display_name = mod.name()
        self.mod_id = mod.nexusId()
        self.domain = domain
        self.installed_version = mod.version().canonicalString()
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
        # Everything this window has been told about the mod by hand, kept as
        # plugin settings in the mod's own meta.ini -- which MO2 reads and
        # writes itself, so none of it goes behind its back.
        settings = settings or {}
        # A version this window was told to offer anyway, in spite of MO2's
        # ignore flag. Scoped to that one version, exactly as MO2 scopes the
        # dismissal it overrides -- a later release is a new decision.
        self.forced_version = _text(settings.get(FORCE_SETTING))
        # Why the user made whatever decision they made about this mod, in
        # their own words, and the latest version at the time they wrote it.
        self.note = _text(settings.get(NOTE_SETTING))
        self.note_version = _text(settings.get(NOTE_VERSION_SETTING))
        # The v3 update chain this mod's installed file belongs to, and where
        # in that chain it sits. `position` is Nexus's own ordering: higher is
        # newer, and it does not depend on parsing the version string.
        self.chain_id = ""
        self.chain_position = 0.0

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
        """The mod's Nexus page, derived the way MO2 derives it.

        ``NexusInterface::getModURL`` builds this from the mod id and the game
        name and lower-cases the domain, and MO2's own "Visit on Nexus" uses
        nothing else. In particular it does not use ``IModInterface::url()``:
        that is the *custom* URL from the Nexus tab, which MO2 ignores whenever
        the mod id is valid (``modinforegular.cpp:157``) because the value is
        unreliable -- the fomod installer has been known to write a previous
        installation's URL into it, and opening the Nexus tab bakes in whatever
        case ``gameNexusName()`` reported. Every mod that gets this far has a
        valid mod id, so deriving is both simpler and what MO2 would show.
        """
        return f"https://www.nexusmods.com/{self.domain.lower()}/mods/{self.mod_id}"

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
    """Map an MO2 game short name to its Nexus domain, '' when unknown.

    Returned in whatever case the game plugin reports it, because that is what
    the API is given. ``page_url`` lower-cases it for the website, at the same
    point MO2 does.
    """
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


def read_overrides(mod, plugin_name: str) -> dict:
    """Everything this plugin has stored on a mod, in one call.

    ``pluginSettings`` returns the whole group at once, which matters on a
    profile with a thousand mods: asking key by key would be one round trip
    through MO2 per setting per mod.
    """
    if not plugin_name:
        return {}
    try:
        return dict(mod.pluginSettings(plugin_name) or {})
    except Exception:
        return {}


def collect_mods(
    organizer: mobase.IOrganizer,
    plugin_name: str = "",
    include_disabled: bool = True,
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
        entry = ModEntry(mod, domain, read_overrides(mod, plugin_name))
        entry.enabled = enabled
        entries.append(entry)

    entries.sort(key=lambda e: e.display_name.lower())
    return entries, skipped, disabled


def _squash(text: str) -> str:
    """Reduce a name to bare lowercase alphanumerics for loose comparison."""
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


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


def position_of(version: dict) -> float:
    """A v3 version's place in its chain. Higher is newer."""
    try:
        return float((version or {}).get("position") or 0)
    except (TypeError, ValueError):
        return 0.0


# v3 spells these lower-case; they mean the same as the v1 categories.
_RETIRED = ("archived", "old_version")


def is_retired(version: dict) -> bool:
    return str((version or {}).get("category") or "").lower() in _RETIRED


def current_in_chain(versions: list) -> Optional[dict]:
    """The version of a chain a person should actually have.

    Not simply the highest `position`. Position records where an upload sits in
    the chain, and an author who back-fills old files gets them appended at the
    end: chain 7237540 has v1.07 (main, primary) at position 3.77 and an
    archived "v1.04 do not download" at 4.0. Taking the maximum there reports a
    downgrade as an update.

    `is_primary` is Nexus' own statement of which file is the current download,
    so it wins. Failing that, the newest upload that has not been retired.
    """
    if not versions:
        return None

    # `category` is the one field that holds up. Neither of the obvious
    # alternatives does:
    #
    # * **Highest position** fails when an author back-fills old uploads --
    #   chain 7237540 has an archived "v1.04 do not download" at position 4.0
    #   above the live v1.07 at 3.77.
    # * **is_primary** fails the same way and more often: in four of five
    #   chains examined it was set on an *archived* upload holding the highest
    #   whole-numbered position, while the file people actually want sat just
    #   below it at a fractional position. Chain 2764699: v2.0.17 archived and
    #   primary at 31.0, v2.0.21 main at 30.9.
    mains = [v for v in versions if str(v.get("category") or "").lower() == "main"]
    if mains:
        return max(mains, key=position_of)

    live = [v for v in versions if not is_retired(v)]
    if live:
        return max(live, key=position_of)

    primary = [v for v in versions if v.get("is_primary")]
    return max(primary or versions, key=position_of)


def find_in_chain(
    versions: list, file_ids, installed_version: str = ""
) -> Optional[dict]:
    """Locate the installed upload inside its chain.

    By file id when MO2 recorded one -- `game_scoped_id` is that same id -- and
    otherwise by version string, which is all there is to go on.
    """
    if not versions:
        return None

    wanted = {int(f) for f in (file_ids or [])}
    if wanted:
        for version in versions:
            try:
                if int(version.get("game_scoped_id") or 0) in wanted:
                    return version
            except (TypeError, ValueError):
                continue

    if installed_version:
        for version in versions:
            if versions_match(installed_version, str(version.get("version") or "")):
                return version
    return None


def as_file_record(version: dict) -> dict:
    """Present a v3 chain version in the shape the rest of the UI expects.

    `game_scoped_id` is the legacy file id, which is what MO2 records and what
    `IDownloadManager` needs, so it becomes `file_id` here. Normalising at this
    one boundary keeps the v3 vocabulary out of the window.
    """
    version = version or {}
    try:
        file_id = int(version.get("game_scoped_id") or 0)
    except (TypeError, ValueError):
        file_id = 0
    return {
        "file_id": file_id,
        "name": str(version.get("name") or ""),
        "version": str(version.get("version") or ""),
        "category_name": str(version.get("category") or "").upper(),
        "is_primary": bool(version.get("is_primary")),
        "position": version.get("position"),
        "uploaded_at": version.get("uploaded_at"),
    }


_TRAILING_WORD = re.compile(r"^([0-9.]*?)([A-Za-z][A-Za-z0-9]*)$")


def versions_match(mo2_version: str, nexus_version: str) -> bool:
    """Whether MO2's version string and a Nexus one describe the same upload.

    MO2 pads to four segments and Nexus does not, so ``1.37.1.0`` and ``1.37.1``
    are the same release, and ``1.0.0.0apartments`` is ``1.0.0apartments`` with
    a zero segment inserted before the suffix. Only used when MO2 recorded no
    file id and there is nothing exact to go on.
    """
    left, right = (mo2_version or "").strip(), (nexus_version or "").strip()
    if not left or not right:
        return False
    if _normalize(left) == _normalize(right):
        return True

    a, b = _numeric_tuple(left), _numeric_tuple(right)
    if a is not None and b is not None:
        return a == b

    ma, mb = _TRAILING_WORD.match(left), _TRAILING_WORD.match(right)
    if ma and mb and ma.group(2).lower() == mb.group(2).lower():
        a = _numeric_tuple(ma.group(1).rstrip("."))
        b = _numeric_tuple(mb.group(1).rstrip("."))
        return a is not None and a == b
    return False


def _numeric_tuple(text: str) -> Optional[tuple]:
    """A dotted number with trailing zero segments removed, or None.

    MO2 pads to four segments, so ``1.0.0.0`` and ``1`` are the same release
    and have to compare equal.
    """
    cleaned = (text or "").strip().lstrip("vV")
    if not cleaned:
        return None
    parts = [p for p in cleaned.split(".") if p != ""]
    if not parts or not all(p.isdigit() for p in parts):
        return None
    numbers = [int(p) for p in parts]
    while len(numbers) > 1 and numbers[-1] == 0:
        numbers.pop()
    return tuple(numbers)


def chain_signature(entry: ModEntry) -> str:
    """What a chain choice for this mod was actually based on.

    Used as the cache key, so remembering an answer cannot outlive the evidence
    that produced it, and two mods from the same page keep separate answers.
    """
    return entry.installation_file or entry.display_name or ""


def choose_chain(entry: ModEntry, chains: list) -> Optional[dict]:
    """Pick the chain a mod came from when its file id was never recorded.

    MO2 only started recording `[installedFiles]` at some point, so a few
    percent of any real profile has nothing to resolve. Matching falls back to
    the archive name, longest match first, exactly as the legacy path did.
    """
    if not chains:
        return None
    if len(chains) == 1:
        return chains[0]

    # The archive name is the best evidence, but it is not always there: one
    # mod on a real profile had no `installationFile` at all. The MO2 mod name
    # is a reasonable second, because people name a mod after the variant they
    # installed.
    #
    # The *page* name is deliberately not used. It describes the page rather
    # than any one download, so it cannot discriminate between that page's
    # chains -- and it usually reads like the longest of them, which made it
    # actively wrong. Page 9643 offers "LaserSightDots_Enabled" and
    # "LaserSightDots_Enabled_BulletFollowsDot"; matching on the page name
    # picked the latter for a mod installed from the former.
    haystacks = [
        _squash(text)
        for text in (entry.installation_file, entry.display_name)
        if text
    ]
    if haystacks:
        hits = [
            c
            for c in chains
            if _squash(str(c.get("name") or ""))
            and any(_squash(str(c.get("name") or "")) in h for h in haystacks)
        ]
        if hits:
            # Longest wins, so "Ellie - A Female Preset SAVE FILE" beats
            # "Ellie - A Female Preset" when the name really does say SAVE FILE.
            return max(hits, key=lambda c: len(_squash(str(c.get("name") or ""))))

    active = [c for c in chains if c.get("is_active")]
    if len(active) == 1:
        return active[0]
    return None


# Only a plain dotted number counts here. Anything else -- "1.0.0joker",
# "1.0.1b", a date, a build string -- means the author is numbering that file
# on its own scheme, and comparing it with the page version is meaningless.
_PLAIN_VERSION = re.compile(r"^\d+(?:\.\d+)*$")


def _plain_version(text: str) -> Optional[tuple]:
    """Parse a strictly numeric version, or None if it is anything else.

    Deliberately stricter than ``mobase.VersionInfo``, whose regex is a prefix
    match (``versioninfo.cpp:27``) and so reads "1.0.0joker" as a perfectly
    good 1.0.0. That leniency is right when asking "is this file newer?" and
    wrong when asking "has the page overtaken my file?", where it manufactures
    comparisons between unrelated numbering schemes.
    """
    cleaned = (text or "").strip()
    if cleaned[:1].lower() == "v":
        cleaned = cleaned[1:].strip()
    if not cleaned or not _PLAIN_VERSION.match(cleaned):
        return None
    return tuple(int(part) for part in cleaned.split("."))


def page_ahead_of(file_version: str, page_version: str) -> bool:
    """True when the page's own version has run past a file's version.

    The last word on whether a retired file has a successor. Nexus models a
    page as a set of *update chains*, one per download, keyed on the file's
    name -- so an author who names each upload after its version gets a fresh
    one-entry chain per release, and an installed file's chain dead-ends the
    moment it is retired. Nothing inside that chain can say what replaced it.

    The page version can. Native Interactions Framework 1.1.0 was retired and
    the page moved to 1.1.1, which is a real update; Praetor Suit Flashlight
    Fix was retired while its page stayed at 1.0, because what is current
    there is a different product, not a successor. Both look identical from
    inside the chain.
    """
    mine = _plain_version(file_version)
    theirs = _plain_version(page_version)
    if mine is None or theirs is None:
        return False

    width = max(len(mine), len(theirs))
    mine += (0,) * (width - len(mine))
    theirs += (0,) * (width - len(theirs))
    return theirs > mine


# Categories Nexus uses for an upload that something else has replaced.
_RETIRED_CATEGORIES = ("OLD_VERSION", "ARCHIVED")


def current_on_page(files: list) -> Optional[dict]:
    """The download a page leads with, from a v1 file list.

    Only used to *name* a candidate for a retired file whose chain ended, so
    the user has something concrete to judge. Deliberately not used to decide
    anything: on page 12903 this returns the opaque-visor patch, which really
    was the successor to the flashlight fix, and on another page the same shape
    would return an unrelated download.
    """
    live = [
        f
        for f in (files or [])
        if str(f.get("category_name") or "").upper() not in _RETIRED_CATEGORIES
    ]
    if not live:
        return None
    return max(
        live,
        key=lambda f: (
            1 if str(f.get("category_name") or "").upper() == "MAIN" else 0,
            1 if f.get("is_primary") else 0,
            int(f.get("uploaded_timestamp") or 0),
        ),
    )


def is_ignored(entry: ModEntry) -> bool:
    """True when MO2 was told to ignore exactly this version.

    MO2 dismisses a *specific* version, so anything newer than what the user
    waved away should start nagging again -- and a version this window was
    told to offer anyway outranks the dismissal entirely.
    """
    ignored = (entry.ignored_version or "").strip()
    if not ignored or not entry.latest_version:
        return False

    forced = (entry.forced_version or "").strip()
    if forced and _normalize(forced) == _normalize(entry.latest_version):
        return False

    if _normalize(ignored) == _normalize(entry.latest_version):
        return True
    # Ignored something even newer (rare, but possible after a downgrade).
    return not is_newer(ignored, entry.latest_version)


# MO2 keeps a mod's ignore flag as a plain ``ignoredVersion=`` under
# ``[General]`` (``modinforegular.cpp:96``), and clearing it writes the key
# back empty rather than removing it (``modinforegular.cpp:260``).
_IGNORED_KEY = "ignoredversion="


def clear_ignored_version(mod_path: str) -> Optional[str]:
    """Clear MO2's "ignore update" flag on a mod. Returns an error, or None.

    ``IModInterface`` exposes ``ignoredVersion()`` but no setter -- the Python
    bindings stop at the getter (``basic_classes.cpp:253``) -- so the flag can
    only be reached through the mod's ``meta.ini``, which this plugin already
    reads for its installed file ids.

    Rewrites the one line and leaves the rest of the file byte-for-byte alone,
    for the same reason ``downloads.hide`` does: a mod's meta carries the whole
    Nexus description as one escaped value, and round-tripping that through an
    INI parser is a large risk for no gain.

    **MO2 holds its own copy.** ``ModInfoRegular`` reads meta.ini once and
    writes it back from memory whenever the mod is marked changed, including
    at shutdown (``modinforegular.cpp:68``). So this lands for certain on MO2's
    next start, and can be undone within this session if something else edits
    the same mod. The caller is expected to say so.
    """
    if not mod_path:
        return "MO2 gave no folder for this mod."

    path = os.path.join(mod_path, "meta.ini")
    try:
        # newline="" so Windows line endings survive; surrogateescape so the
        # description blob comes back out exactly as it went in.
        with open(
            path, "r", encoding="utf-8", errors="surrogateescape", newline=""
        ) as handle:
            lines = handle.read().splitlines(keepends=True)
    except OSError as exc:
        return f"could not read meta.ini: {exc}"

    newline = "\r\n" if any(line.endswith("\r\n") for line in lines) else "\n"

    out, section, cleared = [], "", False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            section = stripped.lower()
        elif section == "[general]" and stripped.lower().startswith(_IGNORED_KEY):
            out.append(f"ignoredVersion={newline}")
            cleared = True
            continue
        out.append(line)

    if not cleared:
        # Nothing to clear is success, not failure: the flag is already off.
        return None

    temp = path + ".umd-tmp"
    try:
        with open(
            temp, "w", encoding="utf-8", errors="surrogateescape", newline=""
        ) as handle:
            handle.writelines(out)
        os.replace(temp, path)
    except OSError as exc:
        try:
            os.remove(temp)
        except OSError:
            pass
        return f"could not update meta.ini: {exc}"

    return None


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
