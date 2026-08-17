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
    __slots__ = ("mod_id", "file_id", "file_name", "path", "version", "state")

    def __init__(self, mod_id, file_id, file_name, path, version, state):
        self.mod_id = mod_id
        self.file_id = file_id
        self.file_name = file_name
        self.path = path
        self.version = version
        self.state = state

    @property
    def usable(self) -> bool:
        return self.state in (READY, INSTALLED)

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
            values.get("version") or "",
            state,
        )

    return index


def find(index: dict, mod_id: int, file_id: Optional[int]) -> Optional[DownloadInfo]:
    if not index or not file_id:
        return None
    return index.get((int(mod_id), int(file_id)))
