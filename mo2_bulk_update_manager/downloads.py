"""What is already sitting in MO2's downloads folder.

Every download MO2 makes gets a sidecar ``<archive>.meta`` recording the Nexus
``modID`` and ``fileID`` it came from, plus whether it was ever installed. That
is enough to answer the question that otherwise costs a wasted download: *do I
already have this file?*

An in-progress download is a ``<archive>.unfinished`` next to the meta, so a
half-finished file is never mistaken for a usable one.
"""

from __future__ import annotations

import os
from typing import Optional

# What the archive is doing, in the order the UI cares about.
READY = "ready"  # on disk, never installed
INSTALLED = "installed"  # on disk, installed at some point
INCOMPLETE = "incomplete"  # download was interrupted


class DownloadInfo:
    __slots__ = (
        "mod_id",
        "file_id",
        "file_name",
        "path",
        "meta_path",
        "version",
        "state",
        "hidden",
    )

    def __init__(
        self, mod_id, file_id, file_name, path, meta_path, version, state, hidden=False
    ):
        self.mod_id = mod_id
        self.file_id = file_id
        self.file_name = file_name
        self.path = path
        self.meta_path = meta_path
        self.version = version
        self.state = state
        # MO2 hides a download by writing removed=true; the archive is still on
        # disk but the Downloads tab does not show it. Worth knowing, because
        # telling someone a file is downloaded when they cannot see it reads as
        # a bug.
        self.hidden = hidden

    @property
    def usable(self) -> bool:
        """Whether this archive is genuinely waiting to be installed.

        An archive MO2 has already installed is not. It used to count, which
        put mods in "Downloaded, waiting to be installed" that had in fact been
        installed and then hidden -- invisible in the Downloads tab, so the
        claim looked simply false.
        """
        return self.state == READY

    def __repr__(self):
        return f"<DownloadInfo {self.file_name!r} {self.state}>"


def _read_general(path: str) -> dict:
    """Read the ``[General]`` section of a download's meta file."""
    values: dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            in_section = False
            for line in handle:
                stripped = line.strip()
                if stripped.startswith("["):
                    if in_section:
                        break
                    in_section = stripped.lower() == "[general]"
                    continue
                if not in_section or "=" not in stripped:
                    continue
                key, _, value = stripped.partition("=")
                key = key.strip().lower()
                if key in values:
                    continue
                values[key] = value.strip().strip('"')
    except OSError:
        return {}
    return values


def scan(downloads_path: str) -> dict[tuple[int, int], DownloadInfo]:
    """Index the downloads folder by ``(mod_id, file_id)``.

    Only Nexus downloads with both ids and an archive still on disk are
    returned; the newest wins if a file id somehow appears twice.
    """
    index: dict[tuple[int, int], DownloadInfo] = {}
    if not downloads_path or not os.path.isdir(downloads_path):
        return index

    try:
        names = os.listdir(downloads_path)
    except OSError:
        return index

    for name in names:
        if not name.endswith(".meta") or name.endswith(".unfinished.meta"):
            continue

        meta_path = os.path.join(downloads_path, name)
        archive_name = name[: -len(".meta")]
        archive_path = os.path.join(downloads_path, archive_name)

        values = _read_general(meta_path)
        try:
            mod_id = int(values.get("modid") or 0)
            file_id = int(values.get("fileid") or 0)
        except ValueError:
            continue
        if mod_id <= 0 or file_id <= 0:
            continue

        if os.path.exists(archive_path + ".unfinished"):
            state = INCOMPLETE
        elif not os.path.exists(archive_path):
            # MO2 keeps the meta after the archive is deleted; nothing to offer.
            continue
        elif (values.get("installed") or "").lower() == "true":
            state = INSTALLED
        else:
            state = READY

        key = (mod_id, file_id)
        existing = index.get(key)
        if existing is not None and existing.state == READY and state != READY:
            continue

        index[key] = DownloadInfo(
            mod_id,
            file_id,
            archive_name,
            archive_path,
            meta_path,
            values.get("version") or "",
            state,
            (values.get("removed") or "").lower() == "true",
        )

    return index


def hide(info: DownloadInfo) -> Optional[str]:
    """Hide a download from MO2's Downloads tab. Returns an error, or None.

    MO2 hides a download by writing ``removed=true`` into its meta file and
    nothing else (``downloadmanager.cpp:910``), so that is all this does. It is
    needed because MO2 only applies the "hide downloads after installation"
    setting on its own Downloads-tab install path
    (``organizercore.cpp:911``); the archive-path install that plugins get
    marks the download installed but never hides it.

    Rewrites one line and leaves the rest of the file byte-for-byte alone --
    these metas carry a large binary ``userData`` blob that is not worth
    round-tripping through an INI parser.
    """
    try:
        # newline="" so Windows line endings survive the round trip; without it
        # every line in the file changes, not just the intended one.
        with open(
            info.meta_path, "r", encoding="utf-8", errors="surrogateescape", newline=""
        ) as handle:
            lines = handle.read().splitlines(keepends=True)
    except OSError as exc:
        return f"could not read {os.path.basename(info.meta_path)}: {exc}"

    newline = "\n"
    for line in lines:
        if line.endswith("\r\n"):
            newline = "\r\n"
            break

    out, section, written, seen_general = [], "", False, False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            # Leaving [General] without having found the key: add it here.
            if section == "[general]" and not written:
                out.append(f"removed=true{newline}")
                written = True
            section = stripped.lower()
            seen_general = seen_general or section == "[general]"
        elif section == "[general]" and stripped.lower().startswith("removed="):
            out.append(f"removed=true{newline}")
            written = True
            continue
        out.append(line)

    if not written:
        if out and not out[-1].endswith(("\n", "\r")):
            out.append(newline)
        if not seen_general:
            out.append(f"[General]{newline}")
        out.append(f"removed=true{newline}")

    temp = info.meta_path + ".umd-tmp"
    try:
        with open(temp, "w", encoding="utf-8", errors="surrogateescape", newline="") as handle:
            handle.writelines(out)
        os.replace(temp, info.meta_path)
    except OSError as exc:
        try:
            os.remove(temp)
        except OSError:
            pass
        return f"could not update {os.path.basename(info.meta_path)}: {exc}"

    return None


def installable(index: dict, mod_id: int, file_id: Optional[int]):
    """The archive on disk for this file, if there is one worth installing.

    Distinct from :func:`find` plus ``usable``. ``usable`` answers "is this
    waiting to be installed", which is what a scan needs to decide whether a
    row belongs on the install queue. This answers "can this be installed right
    now", which is what a menu offering *Install from disk* needs, and the two
    differ for the case that matters most: MO2 marks a download installed and,
    if the user asked it to, hides it from the Downloads tab. The archive is
    still there, and the offer exists to install it again.

    :func:`scan` only indexes archives that exist on disk, so presence is
    enough. An interrupted download is not, because there is no whole archive
    behind it.
    """
    info = find(index, mod_id, file_id)
    if info is None or info.state == INCOMPLETE:
        return None
    return info


def find(index: dict, mod_id: int, file_id: Optional[int]) -> Optional[DownloadInfo]:
    if not index or not file_id:
        return None
    return index.get((int(mod_id), int(file_id)))
