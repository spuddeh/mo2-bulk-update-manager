"""The Update Manager window."""

from __future__ import annotations

import html
import os
import time
from typing import Optional

try:
    from PyQt5.QtCore import Qt, QUrl
    from PyQt5.QtGui import QBrush, QDesktopServices, QFont, QIcon
    from PyQt5.QtWidgets import (
        QAbstractItemView,
        QCheckBox,
        QDialog,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QMessageBox,
        QProgressBar,
        QPushButton,
        QSplitter,
        QTabWidget,
        QTextBrowser,
        QTreeWidget,
        QTreeWidgetItem,
        QVBoxLayout,
        QWidget,
    )
except ImportError:
    from PyQt6.QtCore import Qt, QUrl
    from PyQt6.QtGui import QBrush, QDesktopServices, QFont, QIcon
    from PyQt6.QtWidgets import (
        QAbstractItemView,
        QCheckBox,
        QDialog,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QMessageBox,
        QProgressBar,
        QPushButton,
        QSplitter,
        QTabWidget,
        QTextBrowser,
        QTreeWidget,
        QTreeWidgetItem,
        QVBoxLayout,
        QWidget,
    )

import mobase

from . import downloads as downloads_index
from ._version import VERSION
from .cache import ScanCache
from .credentials import resolve_auth
from .nexus import NexusClient
from .scanner import ModEntry, collect_mods
from .theme import Theme
from .updater import UpdateScan

_ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.svg")

_ENTRY_ROLE = Qt.ItemDataRole.UserRole

_GROUPS = (
    (ModEntry.DOWNLOADED, "Downloaded, waiting to be installed"),
    (ModEntry.UPDATE, "Updates available"),
    (ModEntry.DELISTED, "No longer on Nexus"),
    (ModEntry.HIDDEN, "Hidden or unavailable"),
    (ModEntry.PAGE_CHANGED, "Page updated, your file unchanged"),
    (ModEntry.IGNORED, "Ignored in MO2"),
    (ModEntry.ERROR, "Could not be checked"),
    (ModEntry.UNCHECKED, "Not checked"),
    (ModEntry.CURRENT, "Up to date"),
)

_GROUP_TITLES = dict(_GROUPS)

# Groups whose rows get a checkbox, and what the checked rows are for.
_DOWNLOADABLE = (ModEntry.UPDATE,)
_INSTALLABLE = (ModEntry.DOWNLOADED,)
_CHECKABLE = _DOWNLOADABLE + _INSTALLABLE

# Groups that start collapsed: nothing here needs the user to act.
_COLLAPSED = (ModEntry.CURRENT, ModEntry.PAGE_CHANGED, ModEntry.IGNORED)

# Groups whose rows carry a coloured mark; the rest read as ordinary text.
_MARKED = (
    ModEntry.DOWNLOADED,
    ModEntry.UPDATE,
    ModEntry.DELISTED,
    ModEntry.HIDDEN,
    ModEntry.PAGE_CHANGED,
)

# Nexus file categories, best-first, for picking a default download.
_CATEGORY_RANK = {
    "MAIN": 0,
    "UPDATE": 1,
    "OPTIONAL": 2,
    "MISCELLANEOUS": 3,
    "OLD_VERSION": 9,
    "ARCHIVED": 9,
}

# Categories that are never the right default download.
_SUPERSEDED = ("OLD_VERSION", "ARCHIVED")


def _timestamp(value) -> str:
    try:
        value = int(value)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    return time.strftime("%Y-%m-%d", time.localtime(value))


def _size(kb) -> str:
    try:
        kb = float(kb)
    except (TypeError, ValueError):
        return ""
    if kb >= 1024 * 1024:
        return f"{kb / (1024 * 1024):.1f} GB"
    if kb >= 1024:
        return f"{kb / 1024:.1f} MB"
    return f"{kb:.0f} KB"


class UpdateManagerDialog(QDialog):
    def __init__(self, organizer: mobase.IOrganizer, plugin_name: str, parent=None):
        super().__init__(parent)
        self._organizer = organizer
        self._plugin_name = plugin_name
        self._entries: list[ModEntry] = []
        self._client: Optional[NexusClient] = None
        self._scan: Optional[UpdateScan] = None
        self._cache = ScanCache(organizer.pluginDataPath())
        self._loading_details: set = set()
        self._skipped_count = 0
        self._account = ""
        self._downloads: dict = {}
        self._theme: Optional[Theme] = None

        self.setWindowTitle(f"Update Manager v{VERSION}")
        if os.path.exists(_ICON_PATH):
            self.setWindowIcon(QIcon(_ICON_PATH))
        self.setMinimumSize(1040, 640)
        self._build_ui()
        self._start_scan(deep=False)

    # -- construction ------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        header = QHBoxLayout()
        self._status_label = QLabel("Starting...")
        self._status_label.setWordWrap(True)
        header.addWidget(self._status_label, 1)
        self._rate_label = QLabel("")
        self._rate_label.setToolTip(
            "Nexus allows 100 API requests per hour and 2500 per day."
        )
        header.addWidget(self._rate_label, 0)
        layout.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(5)
        self._tree.setHeaderLabels(["Mod", "File", "Installed", "Latest", "Notes"])
        self._tree.setRootIsDecorated(True)
        self._tree.setAlternatingRowColors(True)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._tree.itemSelectionChanged.connect(self._on_selection_changed)
        self._tree.itemDoubleClicked.connect(lambda *_: self._open_page())
        self._tree.itemChanged.connect(self._on_item_changed)
        splitter.addWidget(self._tree)

        self._tabs = QTabWidget()

        self._changelog_view = QTextBrowser()
        self._changelog_view.setOpenExternalLinks(True)
        self._tabs.addTab(self._changelog_view, "Changelog")

        files_page = QWidget()
        files_layout = QVBoxLayout(files_page)
        files_layout.setContentsMargins(0, 0, 0, 0)
        self._files_tree = QTreeWidget()
        self._files_tree.setColumnCount(5)
        self._files_tree.setHeaderLabels(
            ["File", "Version", "Category", "Size", "Uploaded"]
        )
        self._files_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._files_tree.itemSelectionChanged.connect(self._on_file_selected)
        files_layout.addWidget(self._files_tree, 3)
        self._file_desc = QTextBrowser()
        self._file_desc.setOpenExternalLinks(True)
        files_layout.addWidget(self._file_desc, 2)
        self._use_file_btn = QPushButton("Download this file instead")
        self._use_file_btn.setEnabled(False)
        self._use_file_btn.clicked.connect(self._on_use_file)
        files_layout.addWidget(self._use_file_btn)
        self._tabs.addTab(files_page, "Files")

        self._details_view = QTextBrowser()
        self._details_view.setOpenExternalLinks(True)
        self._tabs.addTab(self._details_view, "Details")

        splitter.addWidget(self._tabs)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)

        self._progress = QProgressBar()
        self._progress.setTextVisible(False)
        self._progress.hide()
        layout.addWidget(self._progress)

        controls = QHBoxLayout()
        self._select_all = QCheckBox("Select all")
        self._select_all.setToolTip("Tick every update and every pending install.")
        self._select_all.stateChanged.connect(self._on_select_all)
        controls.addWidget(self._select_all)
        controls.addStretch(1)

        self._rescan_btn = QPushButton("Rescan")
        self._rescan_btn.setToolTip("Quick check using Nexus' recently-updated feed.")
        self._rescan_btn.clicked.connect(lambda: self._start_scan(deep=False))
        controls.addWidget(self._rescan_btn)

        self._deep_btn = QPushButton("Deep scan")
        self._deep_btn.setToolTip(
            "Query every mod individually. Slower and uses far more of the hourly "
            "API budget, but it is the only way to catch mods pulled from Nexus "
            "a long time ago."
        )
        self._deep_btn.clicked.connect(self._confirm_deep_scan)
        controls.addWidget(self._deep_btn)

        self._open_btn = QPushButton("Open on Nexus")
        self._open_btn.clicked.connect(self._open_page)
        self._open_btn.setEnabled(False)
        controls.addWidget(self._open_btn)

        self._install_btn = QPushButton("Install selected")
        self._install_btn.setToolTip(
            "Install the ticked archives that are already in MO2's downloads folder."
        )
        self._install_btn.clicked.connect(self._on_install)
        self._install_btn.setEnabled(False)
        controls.addWidget(self._install_btn)

        self._download_btn = QPushButton("Download selected")
        self._download_btn.setDefault(True)
        self._download_btn.clicked.connect(self._on_download)
        self._download_btn.setEnabled(False)
        controls.addWidget(self._download_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        controls.addWidget(close_btn)

        layout.addLayout(controls)

    # -- scanning ----------------------------------------------------------

    def _setting(self, key, fallback):
        value = self._organizer.pluginSetting(self._plugin_name, key)
        return fallback if value is None else value

    def _start_scan(self, deep: bool) -> None:
        auth, note = resolve_auth(str(self._setting("api_key", "")))
        if auth is None:
            self._status_label.setText(note)
            self._set_busy(False)
            QMessageBox.warning(self, "No Nexus credentials", note)
            return

        if self._client is not None:
            self._client.cancel()

        self._client = NexusClient(auth, str(self._organizer.version()), self)
        self._client.rateLimitChanged.connect(self._on_rate_limit)

        self._entries, skipped = collect_mods(self._organizer)
        self._skipped_count = len(skipped)
        # Cheap and local: knowing what is already downloaded turns a wasted
        # download into a one-click install.
        self._downloads = downloads_index.scan(self._organizer.downloadsPath())

        self._scan = UpdateScan(self._client, self._cache, self)
        self._scan.progress.connect(self._on_progress)
        self._scan.finished.connect(self._on_scan_finished)
        self._scan.failed.connect(self._on_scan_failed)
        self._scan.identified.connect(self._on_identified)

        self._set_busy(True)
        self._tree.clear()
        self._status_label.setText(
            (note + " " if note else "")
            + f"Signed in as: {auth.source}. Scanning {len(self._entries)} Nexus mod(s)..."
        )
        self._scan.start(
            self._entries,
            deep=deep,
            recheck_days=int(self._setting("recheck_days", 30)),
            downloads=self._downloads,
        )

    def _on_identified(self, user: dict) -> None:
        name = NexusClient.user_name(user)
        tier = "Premium" if NexusClient.is_premium(user) else "Free"
        self._account = f"{name} ({tier})" if name else tier
        if not NexusClient.is_premium(user):
            self._download_btn.setToolTip(
                "Nexus only hands direct download links to Premium accounts. On a free "
                "account use 'Open on Nexus' and click 'Mod Manager Download'."
            )

    def _confirm_deep_scan(self) -> None:
        count = len(self._entries) or len(collect_mods(self._organizer)[0])
        budget = ""
        if self._client is not None and self._client.hourly_remaining is not None:
            budget = (
                f"\n\nYou have {self._client.hourly_remaining} Nexus request(s) left "
                "this hour."
            )
        answer = QMessageBox.question(
            self,
            "Deep scan",
            f"A deep scan sends one Nexus request per mod ({count} in this profile). "
            "It is the only way to catch mods that were pulled from Nexus long ago."
            f"{budget}\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._start_scan(deep=True)

    def _set_busy(self, busy: bool) -> None:
        self._rescan_btn.setEnabled(not busy)
        self._deep_btn.setEnabled(not busy)
        if busy:
            self._download_btn.setEnabled(False)
            self._install_btn.setEnabled(False)
        else:
            self._on_item_changed()
        self._progress.setVisible(busy)

    def _on_progress(self, message: str, done: int, total: int) -> None:
        self._status_label.setText(message)
        if total > 0:
            self._progress.setRange(0, total)
            self._progress.setValue(done)
        else:
            self._progress.setRange(0, 0)

    def _on_rate_limit(self, hourly, daily) -> None:
        parts = []
        if hourly is not None:
            parts.append(f"{hourly} left this hour")
        if daily is not None:
            parts.append(f"{daily} today")
        self._rate_label.setText("Nexus API: " + ", ".join(parts) if parts else "")

    def _on_scan_failed(self, error: str) -> None:
        self._set_busy(False)
        self._status_label.setText(error)
        QMessageBox.warning(self, "Nexus check failed", error)

    def _on_scan_finished(self, entries: list, note: str) -> None:
        self._set_busy(False)
        self._entries = entries
        self._populate(entries)
        self._write_back_versions(entries)

        counts = {}
        for entry in entries:
            counts[entry.status] = counts.get(entry.status, 0) + 1

        parts = [
            (counts.get(ModEntry.UPDATE, 0), "to download"),
            (counts.get(ModEntry.DOWNLOADED, 0), "downloaded, ready to install"),
            (counts.get(ModEntry.DELISTED, 0), "removed from Nexus"),
            (counts.get(ModEntry.HIDDEN, 0), "hidden"),
            (counts.get(ModEntry.IGNORED, 0), "ignored"),
            (counts.get(ModEntry.CURRENT, 0), "up to date"),
        ]
        summary = ", ".join(f"{n} {label}" for n, label in parts if n) + "."
        if self._skipped_count:
            summary += f" {self._skipped_count} mod(s) have no Nexus id and were skipped."
        if self._account:
            summary = f"[{self._account}]  " + summary
        self._status_label.setText(summary + ("  " + note if note else ""))

    def _write_back_versions(self, entries: list) -> None:
        """Tell MO2 about newer versions so its own modlist shows the flag too."""
        if not bool(self._setting("write_back_versions", True)):
            return
        mod_list = self._organizer.modList()
        for entry in entries:
            if entry.status != ModEntry.UPDATE or not entry.latest_version:
                continue
            mod = mod_list.getMod(entry.internal_name)
            if mod is None:
                continue
            try:
                version = mobase.VersionInfo(entry.latest_version)
                if version.isValid():
                    mod.setNewestVersion(version)
            except Exception:
                continue

    # -- tree --------------------------------------------------------------

    def _get_theme(self) -> Theme:
        """Category colours resolved against the tree's real palette.

        Built on first use rather than in __init__: MO2's stylesheet only
        reaches a widget's effective palette once Qt has polished it.
        """
        if self._theme is None:
            self._theme = Theme(self._tree)
        return self._theme

    def _populate(self, entries: list) -> None:
        # Building the tree emits itemChanged for every cell; on a large modlist
        # that would re-walk the whole tree thousands of times.
        self._tree.blockSignals(True)
        try:
            self._populate_tree(entries)
        finally:
            self._tree.blockSignals(False)
        self._on_item_changed()

    def _populate_tree(self, entries: list) -> None:
        self._tree.clear()

        theme = self._get_theme()
        bold = QFont()
        bold.setBold(True)
        muted = QBrush(theme.muted())

        for status, title in _GROUPS:
            members = [e for e in entries if e.status == status]
            if not members:
                continue

            group = QTreeWidgetItem(
                self._tree, [f"{title} ({len(members)})", "", "", "", ""]
            )
            group.setFont(0, bold)
            group.setForeground(0, QBrush(theme.colour(status)))
            group.setFlags(Qt.ItemFlag.ItemIsEnabled)
            group.setExpanded(status not in _COLLAPSED)

            for entry in members:
                item = QTreeWidgetItem(
                    group,
                    [
                        entry.display_name,
                        entry.row_label,
                        entry.installed_version or "-",
                        entry.latest_version or "-",
                        entry.message,
                    ],
                )
                item.setData(0, _ENTRY_ROLE, entry)
                # The name keeps the theme's own text colour so it stays
                # readable; the category shows as a mark beside it.
                if status in _MARKED:
                    item.setIcon(0, theme.dot(status))
                for column in (1, 4):
                    item.setForeground(column, muted)
                if status in _CHECKABLE:
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(0, Qt.CheckState.Unchecked)

        for column in (1, 2, 3):
            self._tree.resizeColumnToContents(column)

    def _on_item_changed(self, *_):
        self._download_btn.setEnabled(bool(self._checked_entries(_DOWNLOADABLE)))
        self._install_btn.setEnabled(bool(self._checked_entries(_INSTALLABLE)))

    def _on_select_all(self, state) -> None:
        checked = (
            Qt.CheckState(state) == Qt.CheckState.Checked
            if not isinstance(state, Qt.CheckState)
            else state == Qt.CheckState.Checked
        )
        for item in self._update_items():
            item.setCheckState(
                0, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
            )

    def _update_items(self, statuses=_CHECKABLE) -> list:
        items = []
        for i in range(self._tree.topLevelItemCount()):
            group = self._tree.topLevelItem(i)
            for j in range(group.childCount()):
                child = group.child(j)
                entry = child.data(0, _ENTRY_ROLE)
                if entry is not None and entry.status in statuses:
                    items.append(child)
        return items

    def _checked_entries(self, statuses=_CHECKABLE) -> list:
        return [
            item.data(0, _ENTRY_ROLE)
            for item in self._update_items(statuses)
            if item.checkState(0) == Qt.CheckState.Checked
        ]

    def _has_checked(self) -> bool:
        return bool(self._checked_entries())

    def _current_entry(self) -> Optional[ModEntry]:
        item = self._tree.currentItem()
        return item.data(0, _ENTRY_ROLE) if item is not None else None

    # -- detail panes ------------------------------------------------------

    def _on_selection_changed(self) -> None:
        entry = self._current_entry()
        self._open_btn.setEnabled(entry is not None)
        if entry is None:
            return

        self._render_details(entry)

        if entry.changelog is None or entry.files is None:
            self._request_details(entry)
        else:
            self._render_changelog(entry)
            self._render_files(entry)

    def _request_details(self, entry: ModEntry) -> None:
        if entry.key in self._loading_details or self._client is None:
            return
        if entry.status == ModEntry.DELISTED:
            self._changelog_view.setHtml(
                "<p><i>This mod is no longer on Nexus, so there is nothing to show.</i></p>"
            )
            self._files_tree.clear()
            return

        self._loading_details.add(entry.key)
        self._changelog_view.setHtml("<p><i>Loading changelog...</i></p>")
        self._files_tree.clear()
        self._file_desc.clear()

        def on_changelog(response, e=entry):
            e.changelog = response.data if response.ok else {}
            if not response.ok and not response.missing:
                e.changelog = {"": [response.error]}
            self._loading_details.discard(e.key)
            if self._current_entry() is e:
                self._render_changelog(e)

        def on_files(response, e=entry):
            e.files = (response.data or {}).get("files", []) if response.ok else []
            if self._current_entry() is e:
                self._render_files(e)

        self._client.changelogs(entry.domain, entry.mod_id, on_changelog)
        self._client.mod_files(entry.domain, entry.mod_id, on_files)

    def _render_details(self, entry: ModEntry) -> None:
        rows = [
            ("MO2 mod", entry.display_name),
            ("Nexus page", entry.nexus_name or "-"),
            ("File line", entry.file_line or "not matched"),
            ("Installed from", entry.installation_file or "-"),
            ("Game", entry.domain),
            ("Mod id", str(entry.mod_id)),
            ("Installed version", entry.installed_version or "-"),
            ("Latest version", entry.latest_version or "-"),
            ("Last updated", _timestamp(entry.latest_file_update) or "-"),
            ("Status", _GROUP_TITLES.get(entry.status, entry.status)),
        ]
        if entry.ignored_version:
            rows.append(("Ignored version", entry.ignored_version))
        if entry.download is not None:
            rows.append(("Already downloaded", entry.download.file_name))

        theme = self._get_theme()
        label = theme.muted(0.35).name()
        accent = theme.colour(entry.status).name()
        body = "".join(
            f"<tr><td style='padding-right:12px;color:{label}'>{html.escape(k)}</td>"
            f"<td>{html.escape(v)}</td></tr>"
            for k, v in rows
        )
        link = html.escape(entry.page_url)
        note = (
            f"<p style='color:{accent}'>{html.escape(entry.message)}</p>"
            if entry.message
            else ""
        )
        self._details_view.setHtml(
            f"<table>{body}</table>{note}<p><a href='{link}'>{link}</a></p>"
        )

    def _render_changelog(self, entry: ModEntry) -> None:
        changelog = entry.changelog or {}
        if not changelog:
            self._changelog_view.setHtml(
                "<p><i>The author has not published a changelog for this mod.</i></p>"
            )
            return

        installed = (entry.installed_version or "").strip().lstrip("vV")
        accent = self._get_theme().colour(ModEntry.UPDATE).name()
        blocks = []
        for version in _sorted_versions(changelog.keys()):
            lines = changelog.get(version) or []
            newer = _is_after(installed, version)
            heading = html.escape(version or "Notes")
            marker = f" <span style='color:{accent}'>(new)</span>" if newer else ""
            items = "".join(f"<li>{html.escape(str(line))}</li>" for line in lines)
            blocks.append(f"<h3>{heading}{marker}</h3><ul>{items}</ul>")

        self._changelog_view.setHtml("".join(blocks))

    def _render_files(self, entry: ModEntry) -> None:
        self._files_tree.clear()
        files = entry.files or []
        if not files:
            self._use_file_btn.setEnabled(False)
            return

        if entry.picked_file_id is None:
            best = _pick_file(files, entry.latest_version, entry.file_line)
            if best is not None:
                entry.picked_file_id = best.get("file_id")

        installed_ids = set(entry.installed_file_ids)
        for info in sorted(files, key=_file_sort_key):
            item = QTreeWidgetItem(
                self._files_tree,
                [
                    str(info.get("name") or ""),
                    str(info.get("version") or ""),
                    str(info.get("category_name") or ""),
                    _size(info.get("size_kb")),
                    _timestamp(info.get("uploaded_timestamp")),
                ],
            )
            item.setData(0, _ENTRY_ROLE, info)
            if info.get("file_id") in installed_ids:
                item.setText(0, "• " + item.text(0))
                item.setToolTip(0, "This is the file currently installed.")
            if info.get("file_id") == entry.picked_file_id:
                item.setText(0, "✓ " + item.text(0))
                self._files_tree.setCurrentItem(item)

        for column in range(1, 5):
            self._files_tree.resizeColumnToContents(column)

    def _on_file_selected(self) -> None:
        item = self._files_tree.currentItem()
        if item is None:
            self._file_desc.clear()
            self._use_file_btn.setEnabled(False)
            return
        info = item.data(0, _ENTRY_ROLE) or {}
        description = str(info.get("description") or "").strip()
        changelog = str(info.get("changelog_html") or "").strip()
        parts = []
        if description:
            parts.append(f"<p>{description}</p>")
        if changelog:
            parts.append(f"<h4>File changelog</h4>{changelog}")
        self._file_desc.setHtml(
            "".join(parts) or "<p><i>No description for this file.</i></p>"
        )
        self._use_file_btn.setEnabled(True)

    def _on_use_file(self) -> None:
        entry = self._current_entry()
        item = self._files_tree.currentItem()
        if entry is None or item is None:
            return
        info = item.data(0, _ENTRY_ROLE) or {}
        entry.picked_file_id = info.get("file_id")
        self._render_files(entry)

    # -- actions -----------------------------------------------------------

    def _open_page(self) -> None:
        entry = self._current_entry()
        if entry is not None:
            QDesktopServices.openUrl(QUrl(entry.page_url))

    def _on_install(self) -> None:
        targets = [
            e for e in self._checked_entries(_INSTALLABLE) if e.download is not None
        ]
        if not targets:
            return

        lines = "\n".join(
            f"  {e.display_name}  ->  {e.download.file_name}" for e in targets
        )
        answer = QMessageBox.question(
            self,
            "Install downloaded archives",
            f"Install {len(targets)} archive(s) already in your downloads folder?\n\n"
            f"{lines}\n\nMO2 runs its normal installer for each one, so any FOMOD "
            "will still ask you the usual questions.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        hide_after = self._hide_downloads_after_install()
        installed, hidden, failed = 0, 0, []
        for entry in targets:
            try:
                result = self._organizer.installMod(
                    entry.download.path, entry.display_name
                )
            except Exception as exc:
                failed.append(f"{entry.display_name}: {exc}")
                continue
            if result is None:
                failed.append(f"{entry.display_name}: installation was cancelled.")
                continue

            installed += 1
            if hide_after:
                error = downloads_index.hide(entry.download)
                if error:
                    failed.append(f"{entry.display_name}: installed, but {error}")
                else:
                    hidden += 1

        if failed:
            QMessageBox.warning(
                self,
                "Some installs did not finish",
                f"Installed {installed}.\n\n" + "\n".join(failed),
            )
        else:
            note = f"Installed {installed} archive(s)."
            if hidden:
                note += f" Hid {hidden} download(s), as MO2's settings ask."
            self._status_label.setText(note)

        if installed:
            self._start_scan(deep=False)

    def _hide_downloads_after_install(self) -> bool:
        """MO2's own 'hide downloads after installation' preference.

        MO2 applies this itself only on the Downloads-tab install path
        (``organizercore.cpp:911``). The archive install that plugins get goes
        through ``installArchive``, which marks the download installed but
        never hides it -- so read the setting and do it here.
        """
        path = os.path.join(self._organizer.basePath(), "ModOrganizer.ini")
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                section = ""
                for line in handle:
                    stripped = line.strip()
                    if stripped.startswith("["):
                        section = stripped.lower()
                        continue
                    if section != "[settings]" or "=" not in stripped:
                        continue
                    key, _, value = stripped.partition("=")
                    if key.strip().lower() == "autohide_downloads":
                        return value.strip().strip('"').lower() == "true"
        except OSError:
            return False
        return False

    def _on_download(self) -> None:
        targets = self._checked_entries(_DOWNLOADABLE)
        if not targets:
            return

        missing = [e for e in targets if e.files is None]
        if missing:
            self._status_label.setText(
                f"Fetching file lists for {len(missing)} mod(s)..."
            )
            self._set_busy(True)
            remaining = {"count": len(missing)}

            def on_files(response, entry=None):
                _, _, mod_id = response.tag
                for candidate in missing:
                    if candidate.mod_id == mod_id:
                        candidate.files = (
                            (response.data or {}).get("files", []) if response.ok else []
                        )
                remaining["count"] -= 1
                if remaining["count"] <= 0:
                    self._set_busy(False)
                    self._start_downloads(targets)

            for entry in missing:
                self._client.mod_files(entry.domain, entry.mod_id, on_files)
            return

        self._start_downloads(targets)

    def _start_downloads(self, targets: list) -> None:
        plan = []
        skipped = []
        for entry in targets:
            info = (
                _file_by_id(entry.files, entry.picked_file_id)
                or entry.latest_file
                or _pick_file(entry.files, entry.latest_version, entry.file_line)
            )
            if info is None:
                skipped.append(entry.display_name)
                continue
            plan.append((entry, info))

        if not plan:
            QMessageBox.information(
                self,
                "Nothing to download",
                "No downloadable files were found for the selected mods.",
            )
            return

        lines = "\n".join(
            f"  {e.display_name}  ->  {info.get('name')} "
            f"({info.get('version') or '?'}, {_size(info.get('size_kb'))})"
            for e, info in plan
        )
        extra = (
            f"\n\nNo file could be chosen for: {', '.join(skipped)}" if skipped else ""
        )
        answer = QMessageBox.question(
            self,
            "Start downloads",
            f"Send {len(plan)} download(s) to MO2?\n\n{lines}{extra}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        downloads = self._organizer.downloadManager()
        started, failed = 0, []
        for entry, info in plan:
            file_id = info.get("file_id")
            try:
                downloads.startDownloadNexusFileForGame(
                    entry.domain, int(entry.mod_id), int(file_id)
                )
                started += 1
            except Exception as exc:
                failed.append(f"{entry.display_name}: {exc}")

        if failed:
            QMessageBox.warning(
                self,
                "Some downloads did not start",
                f"Started {started} download(s).\n\nFailed:\n" + "\n".join(failed)
                + "\n\nIf your Nexus account is not Premium, use 'Open on Nexus' and "
                "click 'Mod Manager Download' instead.",
            )
        else:
            self._status_label.setText(
                f"Sent {started} download(s) to MO2's Downloads tab."
            )

    # -- teardown ----------------------------------------------------------

    def reject(self):
        if self._client is not None:
            self._client.cancel()
        self._cache.save()
        super().reject()


# -- helpers ---------------------------------------------------------------


def _version_key(version: str) -> tuple:
    parts = []
    for chunk in str(version).replace("-", ".").replace("_", ".").split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def _sorted_versions(versions) -> list:
    return sorted(versions, key=lambda v: _version_key(v.lstrip("vV")), reverse=True)


def _is_after(installed: str, version: str) -> bool:
    if not installed:
        return True
    return _version_key(version.lstrip("vV")) > _version_key(installed)


def _file_sort_key(info: dict) -> tuple:
    return (
        _CATEGORY_RANK.get(str(info.get("category_name") or "").upper(), 5),
        -int(info.get("uploaded_timestamp") or 0),
    )


def _file_by_id(files, file_id):
    if not files or file_id is None:
        return None
    for info in files:
        if info.get("file_id") == file_id:
            return info
    return None


def _pick_file(files, latest_version: str, file_line: str = ""):
    """Best guess at the file a user wants.

    When the mod's file line is known, stay inside it -- downloading the page's
    main file for a mod installed from an addon would be wrong.
    """
    if not files:
        return None

    if file_line:
        in_line = [f for f in files if str(f.get("name") or "") == file_line]
        if in_line:
            files = in_line

    usable = [
        f
        for f in files
        if str(f.get("category_name") or "").upper() not in _SUPERSEDED
    ] or list(files)

    exact = [
        f
        for f in usable
        if latest_version
        and str(f.get("version") or "").lstrip("vV") == str(latest_version).lstrip("vV")
    ]
    pool = exact or usable

    primary = [f for f in pool if f.get("is_primary")]
    if primary:
        return sorted(primary, key=_file_sort_key)[0]

    return sorted(pool, key=_file_sort_key)[0]
