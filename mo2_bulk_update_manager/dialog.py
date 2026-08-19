"""The Bulk Update Manager window."""

from __future__ import annotations

import html
import os
import re
import time
from typing import Optional

try:
    from PyQt5.QtCore import QRect, Qt, QTimer, QUrl
    from PyQt5.QtGui import (
        QBrush,
        QDesktopServices,
        QFont,
        QGuiApplication,
        QIcon,
    )
    from PyQt5.QtWidgets import (
        QAbstractItemView,
        QCheckBox,
        QDialog,
        QHBoxLayout,
        QHeaderView,
        QInputDialog,
        QLabel,
        QLineEdit,
        QMenu,
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
    from PyQt6.QtCore import QRect, Qt, QTimer, QUrl
    from PyQt6.QtGui import (
        QBrush,
        QDesktopServices,
        QFont,
        QGuiApplication,
        QIcon,
    )
    from PyQt6.QtWidgets import (
        QAbstractItemView,
        QCheckBox,
        QDialog,
        QHBoxLayout,
        QHeaderView,
        QInputDialog,
        QLabel,
        QLineEdit,
        QMenu,
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
from .log import get_logger, log_exception, tag
from .nexus import NexusClient
from .scanner import (
    FORCE_SETTING,
    NOTE_SETTING,
    NOTE_VERSION_SETTING,
    ModEntry,
    clear_ignored_version,
    collect_mods,
    is_ignored,
    versions_match,
)
from .theme import Theme
from .updater import UpdateScan, note_download

_ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.svg")

# The window is a five-column list beside a detail pane, so it needs width more
# than height and it needs to stay usable on a laptop. Below this it starts
# eliding mod names, so it is the floor rather than a preference.
_MIN_SIZE = (1040, 640)
# Default size as a share of the screen's *available* area -- what is left once
# the taskbar is taken out. A share rather than a pixel size because the same
# 1600x1000 that is comfortable on 1440p is off-screen on a 1366x768 laptop and
# postage-stamp sized on 4K. Deliberately short of full screen: this is a tool
# window opened over MO2, and it should still read as sitting on top of it.
_DEFAULT_SCREEN_SHARE = (0.72, 0.78)
# Where the user's own size and position are kept. `setPersistent` rather than
# `setPluginSetting`: MO2 only reloads plugin settings it finds in `settings()`
# (`settings.cpp:registerPlugin`), so an undeclared key would be written to
# ModOrganizer.ini and then never read back. `[PluginPersistance]` has no such
# rule, and this does not belong in the settings dialog anyway.
_GEOMETRY_KEY = "window_geometry"

# How long the selection has to sit still before its changelog and file list
# are fetched. Long enough that arrowing through the list costs nothing, short
# enough that clicking a row still feels immediate.
_DETAILS_DELAY_MS = 300
_log = get_logger("ui")

_ENTRY_ROLE = Qt.ItemDataRole.UserRole
# On a group row: (status, title, member count), so the filter can rewrite the
# heading without re-deriving what the group is.
_GROUP_ROLE = Qt.ItemDataRole.UserRole + 1

_GROUPS = (
    (ModEntry.DOWNLOADING, "Downloading"),
    (ModEntry.DOWNLOADED, "Downloaded, waiting to be installed"),
    (ModEntry.UPDATE, "Updates available"),
    (ModEntry.SUPERSEDED, "Superseded on Nexus -- your call"),
    (ModEntry.DELISTED, "No longer on Nexus"),
    (ModEntry.HIDDEN, "Hidden or unavailable"),
    (ModEntry.IGNORED, "Ignored in MO2"),
    (ModEntry.ERROR, "Could not be checked"),
    (ModEntry.UNCHECKED, "Not checked"),
    (ModEntry.CURRENT, "Up to date"),
)

_GROUP_TITLES = dict(_GROUPS)

# Groups whose rows get a checkbox, and what the checked rows are for.
_DOWNLOADABLE = (ModEntry.UPDATE, ModEntry.SUPERSEDED)
_INSTALLABLE = (ModEntry.DOWNLOADED,)
_CHECKABLE = _DOWNLOADABLE + _INSTALLABLE

# What "Select all" reaches. Superseded rows are deliberately left out: the
# whole point of that group is that nobody knows whether the file named on it
# is a successor, so it is a row-by-row decision and never a sweep.
_SWEEPABLE = (ModEntry.UPDATE, ModEntry.DOWNLOADED)

# Groups that start collapsed: nothing here needs the user to act.
_COLLAPSED = (ModEntry.CURRENT, ModEntry.IGNORED)

# Groups whose rows carry a coloured mark; the rest read as ordinary text.
_MARKED = (
    ModEntry.DOWNLOADING,
    ModEntry.DOWNLOADED,
    ModEntry.UPDATE,
    ModEntry.SUPERSEDED,
    ModEntry.DELISTED,
    ModEntry.HIDDEN,
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

# Every category Nexus uses, and which of them are worth listing by default.
_ALL_CATEGORIES = ("MAIN", "UPDATE", "OPTIONAL", "MISCELLANEOUS", "OLD_VERSION", "ARCHIVED")
DEFAULT_FILE_CATEGORIES = "MAIN,UPDATE,OPTIONAL,MISCELLANEOUS"


# Nexus fields named "_html" are not reliably HTML. `changelog_html` on a file
# record is frequently plain text with newline separators, and a QTextBrowser
# collapses those into one paragraph -- which is how a tidy bullet list on the
# website arrives here as a wall of text.
_HTML_TAG = re.compile(r"<\s*(br|p|ul|ol|li|b|i|em|strong|a|div|span|h[1-6])\b", re.I)


def _split_lines(text: str) -> list:
    """Break a value into display lines, whether it is HTML or plain text."""
    text = (text or "").strip()
    if not text:
        return []
    if _HTML_TAG.search(text):
        # Real markup: hand it over intact rather than second-guessing it.
        return [text]
    return [line.strip() for line in text.splitlines() if line.strip()]


def _as_paragraph(text: str) -> str:
    lines = _split_lines(text)
    if not lines:
        return ""
    if len(lines) == 1 and _HTML_TAG.search(lines[0]):
        return lines[0]
    return "<p>" + "<br>".join(html.escape(line) for line in lines) + "</p>"


def _as_list(text: str) -> str:
    lines = _split_lines(text)
    if not lines:
        return ""
    if len(lines) == 1:
        return lines[0] if _HTML_TAG.search(lines[0]) else f"<p>{html.escape(lines[0])}</p>"
    return "<ul>" + "".join(f"<li>{html.escape(line)}</li>" for line in lines) + "</ul>"


def _make_scrollable(tree) -> None:
    """Let a tree's columns size to their content and scroll sideways.

    Stretching the first column to fill the viewport means a long mod name is
    squeezed or elided and there is never anything to scroll to. Sizing every
    column to its content instead, with no stretch on the last one, lets the
    row take the width it actually needs.
    """
    header = tree.header()
    header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    header.setStretchLastSection(False)
    header.setSectionsMovable(True)
    tree.setTextElideMode(Qt.TextElideMode.ElideNone)
    tree.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)


def _autofit(tree) -> None:
    """Size every column to its widest cell, header included."""
    for column in range(tree.columnCount()):
        tree.resizeColumnToContents(column)


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


class BulkUpdateManagerDialog(QDialog):
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
        self._disabled_count = 0
        self._account = ""
        # None until Nexus answers `validate`. False disables the direct
        # download path -- see _queue_downloads.
        self._premium: Optional[bool] = None
        self._downloads: dict = {}
        self._theme: Optional[Theme] = None
        # MO2 download id -> (entry, file record) for downloads this window
        # started, so their rows can move on their own instead of waiting for
        # the user to rescan.
        self._in_flight: dict = {}
        self._refresh_pending = False
        self._closed = False
        # The row whose changelog and file list are wanted once the selection
        # stops moving. See _on_selection_changed.
        self._pending_details: Optional[ModEntry] = None
        self._details_timer = QTimer(self)
        self._details_timer.setSingleShot(True)
        self._details_timer.timeout.connect(self._on_details_due)
        # Lowercased words the mod list is filtered down to; empty shows all.
        self._filter_terms: list = []
        # Mods whose MO2 "ignored" flag this window cleared. MO2 holds its own
        # copy of every meta.ini, so touching one again would hand the stale
        # value back -- see _write_back_versions.
        self._unignored: set = set()
        # mod name -> the Nexus file id this window just installed into it.
        # See _seed_installed_ids.
        self._just_installed: dict = {}

        self.setWindowTitle(f"Bulk Update Manager v{VERSION}")
        if os.path.exists(_ICON_PATH):
            self.setWindowIcon(QIcon(_ICON_PATH))
        self.setMinimumSize(*_MIN_SIZE)
        self._build_ui()
        self._restore_geometry()
        self._watch_downloads()
        if self._flag("scan_on_open", True):
            self._start_scan(deep=False)
        else:
            self._status_label.setText(
                "Ready. Press Rescan to check Nexus, or Deep scan for a full sweep."
            )

    # -- construction ------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        header = QHBoxLayout()
        self._status_label = QLabel("Starting...")
        self._status_label.setWordWrap(True)
        header.addWidget(self._status_label, 1)
        self._rate_label = QLabel("")
        self._rate_label.setToolTip(
            "How much of your Nexus API allowance is left. The allowance depends "
            "on the account: 100 an hour on a free one, 2000 on Premium."
        )
        header.addWidget(self._rate_label, 0)
        layout.addLayout(header)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Filter:"), 0)
        self._filter_box = QLineEdit()
        self._filter_box.setPlaceholderText(
            "Type part of a mod name. Several words all have to match."
        )
        self._filter_box.setClearButtonEnabled(True)
        self._filter_box.setToolTip(
            "Matches the mod name, the file line, the Nexus page name, the note "
            "on the row and any note you wrote yourself.\n"
            "Ticks you have already made survive filtering, so a hidden row that "
            "is ticked still downloads."
        )
        self._filter_box.textChanged.connect(self._on_filter_typed)
        filter_row.addWidget(self._filter_box, 1)
        self._filter_label = QLabel("")
        filter_row.addWidget(self._filter_label, 0)
        layout.addLayout(filter_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(5)
        self._tree.setHeaderLabels(["Mod", "File", "Installed", "Latest", "Notes"])
        self._tree.setRootIsDecorated(True)
        self._tree.setAlternatingRowColors(True)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        _make_scrollable(self._tree)
        self._tree.itemSelectionChanged.connect(self._on_selection_changed)
        self._tree.itemDoubleClicked.connect(lambda *_: self._open_page())
        self._tree.itemChanged.connect(self._on_item_changed)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_context_menu)
        # Column widths only measure visible rows, so a group that starts
        # collapsed needs a refit once its contents appear.
        self._tree.itemExpanded.connect(lambda *_: _autofit(self._tree))
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
        _make_scrollable(self._files_tree)
        self._files_tree.itemSelectionChanged.connect(self._on_file_selected)
        self._files_tree.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self._files_tree.customContextMenuRequested.connect(
            self._on_file_context_menu
        )
        files_layout.addWidget(self._files_tree, 3)
        self._file_desc = QTextBrowser()
        self._file_desc.setOpenExternalLinks(True)
        files_layout.addWidget(self._file_desc, 2)

        files_controls = QHBoxLayout()
        self._show_all_files = QCheckBox("Show every file")
        self._show_all_files.setToolTip(
            "Include categories hidden by the 'file_categories' plugin setting, "
            "such as old versions and archived uploads."
        )
        self._show_all_files.toggled.connect(self._on_show_all_files)
        files_controls.addWidget(self._show_all_files)
        self._files_hidden_label = QLabel("")
        files_controls.addWidget(self._files_hidden_label, 1)
        self._use_file_btn = QPushButton("Download this file instead")
        self._use_file_btn.setEnabled(False)
        self._use_file_btn.clicked.connect(self._on_use_file)
        files_controls.addWidget(self._use_file_btn)
        files_layout.addLayout(files_controls)

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
        self._select_all.setToolTip(
            "Tick every update and every pending install the filter is showing.\n"
            "Superseded rows are left alone -- each one is a judgement call."
        )
        # Tristate so it can show "some" while rows move between groups on
        # their own. `clicked` rather than `stateChanged`, so that programmatic
        # syncing never looks like the user asking for something.
        self._select_all.setTristate(True)
        self._select_all.clicked.connect(self._on_select_all_clicked)
        controls.addWidget(self._select_all)
        controls.addStretch(1)

        self._rescan_btn = QPushButton("Rescan")
        self._rescan_btn.setToolTip("Quick check using Nexus' recently-updated feed.")
        self._rescan_btn.clicked.connect(lambda: self._start_scan(deep=False))
        controls.addWidget(self._rescan_btn)

        self._deep_btn = QPushButton("Deep scan")
        self._deep_btn.setToolTip(
            "Re-read every mod's update chain, not just the ones Nexus reports as "
            "changed. Slower and uses far more of the hourly API budget."
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

    # -- window size -------------------------------------------------------

    def _available_rect(self):
        """The usable area of the screen this window is opening on.

        Prefers the parent's screen, so on a multi-monitor setup the window
        sizes to the monitor MO2 is on rather than to the primary one.
        """
        screen = None
        parent = self.parentWidget()
        if parent is not None:
            screen = parent.screen()
        if screen is None:
            screen = self.screen() or QGuiApplication.primaryScreen()
        return screen.availableGeometry() if screen is not None else None

    def _restore_geometry(self) -> None:
        """Reopen at the size and place the user last left, or a sensible default."""
        rect = self._available_rect()
        saved = self._saved_geometry()
        if saved is not None and rect is not None:
            x, y, width, height = saved
            width = max(width, _MIN_SIZE[0])
            height = max(height, _MIN_SIZE[1])
            # A monitor that has since been unplugged would otherwise put the
            # window somewhere the user cannot reach it.
            visible = rect.intersected(QRect(x, y, width, height))
            if visible.width() >= 120 and visible.height() >= 60:
                self.setGeometry(x, y, width, height)
                return

        if rect is None:
            self.resize(*_MIN_SIZE)
            return
        width, height = fit_to_screen(rect.width(), rect.height())
        self.resize(width, height)
        self.move(
            rect.x() + max(0, (rect.width() - width) // 2),
            rect.y() + max(0, (rect.height() - height) // 2),
        )

    def _saved_geometry(self) -> Optional[tuple]:
        try:
            raw = self._organizer.persistent(self._plugin_name, _GEOMETRY_KEY, "")
        except Exception:
            return None
        return parse_geometry(raw)

    def _save_geometry(self) -> None:
        if self.isMaximized() or self.isMinimized():
            # normalGeometry is what the window returns to, which is the size
            # worth remembering; a maximised window would otherwise be restored
            # as an un-maximised window filling the screen.
            rect = self.normalGeometry()
        else:
            rect = self.geometry()
        value = f"{rect.x()},{rect.y()},{rect.width()},{rect.height()}"
        try:
            self._organizer.setPersistent(self._plugin_name, _GEOMETRY_KEY, value)
        except Exception as exc:
            _log.debug(tag(f"Could not remember the window size: {exc}"))

    # -- scanning ----------------------------------------------------------

    def _setting(self, key, fallback):
        value = self._organizer.pluginSetting(self._plugin_name, key)
        return fallback if value is None else value

    def _flag(self, key, fallback: bool) -> bool:
        """A boolean plugin setting, tolerant of MO2 handing it back as text."""
        value = self._setting(key, fallback)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on")

    def _start_scan(self, deep: bool) -> None:
        auth, note = resolve_auth(str(self._setting("api_key", "")))
        if auth is None:
            _log.error(tag(f"Cannot scan: {note}"))
            self._status_label.setText(note)
            self._set_busy(False)
            QMessageBox.warning(self, "No Nexus credentials", note)
            return

        if self._client is not None:
            self._client.cancel()

        # A rescan builds fresh ModEntry objects, so anything still in flight
        # would be pointing at rows that no longer exist. The rescan re-reads
        # the downloads folder anyway, which is what those rows would have
        # become.
        self._in_flight.clear()

        self._client = NexusClient(auth, str(self._organizer.version()), self)
        self._client.rateLimitChanged.connect(self._on_rate_limit)

        self._entries, skipped, self._disabled_count = collect_mods(
            self._organizer,
            self._plugin_name,
            include_disabled=self._flag("check_disabled_mods", True),
        )
        self._skipped_count = len(skipped)
        self._seed_installed_ids()
        for entry in self._entries:
            if entry.internal_name in self._unignored:
                # MO2 answers `ignoredVersion()` from the copy it read at
                # startup, so a mod un-ignored in this session would come back
                # into the Ignored group on the next Rescan. Disk says
                # otherwise; follow disk.
                entry.ignored_version = ""
        # Cheap and local: knowing what is already downloaded turns a wasted
        # download into a one-click install.
        self._downloads = downloads_index.scan(self._organizer.downloadsPath())
        _log.info(
            tag(
                f"Collected {len(self._entries)} Nexus mod(s); {self._skipped_count} "
                f"skipped without a Nexus id, {self._disabled_count} disabled skipped; "
                f"{len(self._downloads)} archive(s) indexed in the downloads folder"
            )
        )

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

    def _seed_installed_ids(self) -> None:
        """Tell the scan which file this window just installed, ahead of disk.

        Installing triggers a rescan, and `read_installed_file_ids` reads the
        mod's meta.ini off disk -- but MO2 has only set `[installedFiles]` in
        memory at that point and flushes it a moment later. The rescan starts a
        millisecond after `installMod` returns and loses that race, so the mod
        looks like one MO2 never recorded a file id for. On a page hosting one
        chain that resolves anyway; on a page hosting two -- Cyberpunk Ultra
        Plus and Ultra Skin share page 10490 -- there is nothing solid left to
        pick with, and a mod that was just correctly updated lands in "Not
        checked" until the next scan.

        No timing is needed to fix it. This window chose the file id it asked
        MO2 to download and install, so it can simply say so. The record is
        dropped once the disk copy agrees.
        """
        if not self._just_installed:
            return
        for entry in self._entries:
            file_id = self._just_installed.get(entry.internal_name)
            if not file_id:
                continue
            if file_id in entry.installed_file_ids:
                self._just_installed.pop(entry.internal_name, None)
                continue
            # First, because `find_in_chain` takes the first id that matches
            # and a stale entry left on disk would otherwise win.
            entry.installed_file_ids.insert(0, int(file_id))
            _log.info(
                tag(
                    f"MO2 has not written {entry.display_name}'s meta.ini yet; "
                    f"using file {file_id} from this session's install"
                )
            )

    def _on_identified(self, user: dict) -> None:
        name = NexusClient.user_name(user)
        self._premium = NexusClient.is_premium(user)
        tier = "Premium" if self._premium else "Free"
        self._account = f"{name} ({tier})" if name else tier
        if not self._premium:
            # Relabel rather than disable: the page hand-off still works, it
            # just needs a click on Nexus that this window cannot make.
            self._download_btn.setText("Open download pages")
            self._download_btn.setToolTip(
                "Nexus only hands direct download links to Premium accounts, so "
                "this opens each mod's file on Nexus instead. Click 'Mod Manager "
                "Download' there and MO2 picks it up from the browser."
            )
            _log.info(
                tag(
                    "Free Nexus account: downloads open the mod page instead of "
                    "being queued directly."
                )
            )

    def _confirm_deep_scan(self) -> None:
        count = len(self._entries) or len(
            collect_mods(
                self._organizer,
                self._plugin_name,
                include_disabled=self._flag("check_disabled_mods", True),
            )[0]
        )
        budget = ""
        if self._client is not None and self._client.hourly_remaining is not None:
            budget = (
                f"\n\nYou have {self._client.hourly_remaining} Nexus request(s) left "
                "this hour."
            )
        answer = QMessageBox.question(
            self,
            "Deep scan",
            f"A deep scan re-reads the update chain of every mod ({count} in this "
            "profile) instead of only the ones Nexus reports as changed."
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
        client = self._client
        parts = []
        if hourly is not None:
            limit = getattr(client, "hourly_limit", None)
            parts.append(f"{hourly} of {limit} left this hour" if limit else f"{hourly} left this hour")
        if daily is not None:
            limit = getattr(client, "daily_limit", None)
            parts.append(f"{daily} of {limit} today" if limit else f"{daily} today")
        self._rate_label.setText("Nexus API: " + ", ".join(parts) if parts else "")

    def _on_scan_failed(self, error: str) -> None:
        _log.error(tag(f"Scan failed: {error}"))
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
            (counts.get(ModEntry.SUPERSEDED, 0), "superseded, needing a look"),
            (counts.get(ModEntry.DELISTED, 0), "removed from Nexus"),
            (counts.get(ModEntry.HIDDEN, 0), "hidden"),
            (counts.get(ModEntry.IGNORED, 0), "ignored"),
            (counts.get(ModEntry.CURRENT, 0), "up to date"),
        ]
        summary = ", ".join(f"{n} {label}" for n, label in parts if n) + "."
        if self._skipped_count:
            summary += f" {self._skipped_count} mod(s) have no Nexus id and were skipped."
        if self._disabled_count:
            summary += f" {self._disabled_count} disabled mod(s) were skipped."
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
            if entry.internal_name in self._unignored:
                # This mod's meta.ini was edited from under MO2, which still
                # holds the old ignoredVersion in memory. `setNewestVersion`
                # marks the mod changed, and a changed mod gets written back
                # from memory at shutdown (`modinforegular.cpp:68`) -- which
                # would put the ignore flag straight back. One missing update
                # arrow in MO2's own list is the cheaper loss.
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

    # -- live download state -----------------------------------------------

    def _watch_downloads(self) -> None:
        """Follow the downloads this window starts, through MO2's own signals.

        There is no way to unregister these handlers, so every one of them has
        to survive being called after the window is gone -- hence the guard on
        `_closed` before anything touches a widget.
        """
        try:
            manager = self._organizer.downloadManager()
            manager.onDownloadComplete(self._on_download_complete)
            manager.onDownloadFailed(self._on_download_failed)
            manager.onDownloadPaused(self._on_download_paused)
        except Exception:
            # Losing live updates is a downgrade to "press Rescan", not a
            # reason to fail opening the window.
            pass

    def _claim(self, download_id) -> Optional[tuple]:
        if self._closed:
            return None
        try:
            return self._in_flight.get(int(download_id))
        except (TypeError, ValueError):
            return None

    def _on_download_complete(self, download_id) -> None:
        claimed = self._claim(download_id)
        if claimed is None:
            return
        entry, info = claimed
        self._in_flight.pop(int(download_id), None)

        path = ""
        try:
            path = self._organizer.downloadManager().downloadPath(int(download_id))
        except Exception:
            path = ""

        # MO2 writes the meta file immediately before signalling completion
        # (downloadmanager.cpp:1686), so the archive is ready to install.
        record = downloads_index.DownloadInfo(
            entry.mod_id,
            info.get("file_id"),
            os.path.basename(path) if path else str(info.get("file_name") or ""),
            path,
            path + ".meta" if path else "",
            str(info.get("version") or ""),
            downloads_index.READY,
        )
        entry.download = record
        self._downloads[(entry.mod_id, info.get("file_id"))] = record
        entry.status = ModEntry.DOWNLOADED
        entry.message = "Just downloaded, not installed."
        _log.info(tag(f"Download {download_id} finished: {entry.display_name}"))
        self._schedule_refresh()

    def _on_download_failed(self, download_id) -> None:
        claimed = self._claim(download_id)
        if claimed is None:
            return
        entry, _ = claimed
        self._in_flight.pop(int(download_id), None)
        entry.status = ModEntry.UPDATE
        entry.message = "Download failed. Check MO2's Downloads tab."
        _log.warning(tag(f"Download {download_id} failed: {entry.display_name}"))
        self._schedule_refresh()

    def _on_download_paused(self, download_id) -> None:
        claimed = self._claim(download_id)
        if claimed is None:
            return
        entry, _ = claimed
        entry.message = "Paused in MO2's Downloads tab."
        self._schedule_refresh()

    def _schedule_refresh(self) -> None:
        """Coalesce rebuilds: a batch of downloads finishing lands as one."""
        if self._refresh_pending or self._closed:
            return
        self._refresh_pending = True
        QTimer.singleShot(250, self._refresh_rows)

    def _refresh_rows(self) -> None:
        self._refresh_pending = False
        if self._closed:
            return
        try:
            self._populate(self._entries)
        except RuntimeError:
            # The dialog was torn down between the timer and its callback.
            self._closed = True

    def _get_theme(self) -> Theme:
        """Category colours resolved against the tree's real palette.

        Built on first use rather than in __init__: MO2's stylesheet only
        reaches a widget's effective palette once Qt has polished it.
        """
        if self._theme is None:
            self._theme = Theme(self._tree)
        return self._theme

    def _populate(self, entries: list) -> None:
        # A rebuild can happen mid-session now that download rows move on their
        # own, so anything the user has set by hand has to survive it.
        selected = self._current_entry()
        ticked = {id(e) for e in self._checked_entries()}

        # Building the tree emits itemChanged for every cell; on a large modlist
        # that would re-walk the whole tree thousands of times.
        self._tree.blockSignals(True)
        try:
            self._populate_tree(entries)
            self._restore_state(selected, ticked)
        finally:
            self._tree.blockSignals(False)
        self._on_item_changed()

    def _restore_state(self, selected, ticked: set) -> None:
        for item in self._update_items():
            entry = item.data(0, _ENTRY_ROLE)
            if entry is not None and id(entry) in ticked:
                item.setCheckState(0, Qt.CheckState.Checked)

        if selected is None:
            return
        for i in range(self._tree.topLevelItemCount()):
            group = self._tree.topLevelItem(i)
            for j in range(group.childCount()):
                child = group.child(j)
                if child.data(0, _ENTRY_ROLE) is selected:
                    self._tree.setCurrentItem(child)
                    self._tree.scrollToItem(child)
                    return

    def _populate_tree(self, entries: list) -> None:
        self._tree.clear()

        theme = self._get_theme()
        bold = QFont()
        bold.setBold(True)
        muted = QBrush(theme.muted())

        hidden_groups = set()
        if not self._flag("show_up_to_date", True):
            hidden_groups.add(ModEntry.CURRENT)
        if not self._flag("show_ignored", True):
            hidden_groups.add(ModEntry.IGNORED)

        for status, title in _GROUPS:
            if status in hidden_groups:
                continue
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
            group.setData(0, _GROUP_ROLE, (status, title, len(members)))
            if status == ModEntry.IGNORED:
                group.setToolTip(
                    0,
                    "Right-click a mod here to download its update anyway, or to "
                    "clear MO2's ignore flag.",
                )

            for entry in members:
                item = QTreeWidgetItem(
                    group,
                    [
                        entry.display_name,
                        entry.row_label,
                        entry.installed_version or "-",
                        entry.latest_version or "-",
                        _notes_cell(entry),
                    ],
                )
                item.setData(0, _ENTRY_ROLE, entry)
                if entry.note:
                    # The cell is shortened to keep the column narrow, so the
                    # whole note has to be reachable without opening a tab.
                    item.setToolTip(4, entry.note)
                # The name keeps the theme's own text colour so it stays
                # readable; the category shows as a mark beside it.
                if status in _MARKED:
                    item.setIcon(0, theme.dot(status))
                for column in (1, 4):
                    item.setForeground(column, muted)
                if status in _CHECKABLE:
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(0, Qt.CheckState.Unchecked)

        # Filtering only has work to do when a filter is on: every row here was
        # just created, so none of them is hidden and every heading already
        # carries its full count. Skipping it spares a thousand setHidden calls
        # on each of the rebuilds that happen while downloads land.
        if self._filter_terms:
            self._apply_filter()  # ends by refitting the columns itself
        else:
            self._autofit_soon()

    def _autofit_soon(self) -> None:
        """Size the columns now, and again once Qt has laid the tree out.

        `resizeColumnToContents` measures the rows the view can see. A rebuild
        that runs inside another event loop -- a context menu still unwinding,
        a modal dialog closing -- measures a tree that has not been laid out
        yet, and every column comes back at the header's default width. With
        elision off, that quietly clips the Notes column: the note the user
        just wrote is there in the row, just past the right-hand edge of a
        100-pixel column.

        Asking again from the event loop is the half that is reliable. The
        immediate call only avoids a visible flicker in the ordinary case.
        """
        _autofit(self._tree)
        QTimer.singleShot(0, self._refit_columns)

    def _refit_columns(self) -> None:
        if self._closed:
            return
        try:
            _autofit(self._tree)
        except RuntimeError:
            # The window was torn down between the timer and its callback.
            self._closed = True

    # -- filtering ---------------------------------------------------------

    def _on_filter_typed(self, text: str) -> None:
        # Splitting on whitespace and requiring every word makes "cet frame"
        # find "CET Frame Generation" without caring about word order, which is
        # what people type when they half-remember a name.
        self._filter_terms = [word for word in str(text).lower().split() if word]
        self._apply_filter()

    def _apply_filter(self) -> None:
        """Hide rows that do not match, and keep the group headings honest.

        A hidden row is still a row: it keeps its tick, and `_checked_entries`
        still finds it, so filtering never quietly drops something the user
        already chose. What it must not do is let "Select all" reach rows that
        are not on screen -- see `_update_items`.
        """
        terms = self._filter_terms

        # A thousand setHidden calls, each repainting, is visibly slow to type
        # against on a large profile. One repaint at the end is not -- and the
        # expanding this does would otherwise fire itemExpanded, and so a full
        # column refit, on every keystroke.
        self._tree.setUpdatesEnabled(False)
        blocked = self._tree.blockSignals(True)
        try:
            shown, total = self._filter_groups(terms)
        finally:
            self._tree.blockSignals(blocked)
            self._tree.setUpdatesEnabled(True)

        if terms:
            self._filter_label.setText(f"Showing {shown} of {total}")
        else:
            self._filter_label.clear()
        # Filtering opens groups that normally start collapsed, so it can bring
        # rows into view that were never measured -- the widest mod name on the
        # list may well be one of the thousand up-to-date ones.
        self._autofit_soon()
        # The master checkbox describes what is on screen, so it moves with the
        # filter even though no tick changed.
        self._sync_select_all()

    def _filter_groups(self, terms: list) -> tuple:
        shown = total = 0
        for i in range(self._tree.topLevelItemCount()):
            group = self._tree.topLevelItem(i)
            meta = group.data(0, _GROUP_ROLE)
            if meta is None:
                continue
            status, title, count = meta
            total += count

            if not terms:
                for j in range(group.childCount()):
                    group.child(j).setHidden(False)
                group.setHidden(False)
                group.setText(0, f"{title} ({count})")
                group.setExpanded(status not in _COLLAPSED)
                shown += count
                continue

            visible = 0
            for j in range(group.childCount()):
                child = group.child(j)
                match = _matches(child.data(0, _ENTRY_ROLE), terms)
                child.setHidden(not match)
                visible += 1 if match else 0

            shown += visible
            group.setHidden(visible == 0)
            group.setText(0, f"{title} ({visible} of {count})")
            # A match inside a group that starts collapsed -- "Up to date" holds
            # most of a large profile -- would otherwise be invisible.
            if visible:
                group.setExpanded(True)

        return shown, total

    def _on_item_changed(self, *_):
        self._download_btn.setEnabled(bool(self._checked_entries(_DOWNLOADABLE)))
        self._install_btn.setEnabled(bool(self._checked_entries(_INSTALLABLE)))
        self._sync_select_all()

    def _sync_select_all(self) -> None:
        """Make the master checkbox describe the rows rather than lead them.

        Rows move between groups on their own now -- a ticked update becomes an
        untickable Downloading row the moment it is queued -- so "select all"
        would otherwise sit there checked with nothing checked beneath it.
        """
        items = self._update_items(_SWEEPABLE, visible_only=True)
        checked = sum(
            1 for item in items if item.checkState(0) == Qt.CheckState.Checked
        )

        if not items or checked == 0:
            state = Qt.CheckState.Unchecked
        elif checked == len(items):
            state = Qt.CheckState.Checked
        else:
            state = Qt.CheckState.PartiallyChecked

        blocked = self._select_all.blockSignals(True)
        self._select_all.setCheckState(state)
        self._select_all.setEnabled(bool(items))
        self._select_all.blockSignals(blocked)

    def _on_select_all_clicked(self, *_) -> None:
        items = self._update_items(_SWEEPABLE, visible_only=True)
        if not items:
            self._sync_select_all()
            return

        # Decide from the rows, not from whatever state the click left the
        # checkbox in -- a tristate box cycles through "partial" on its own.
        all_checked = all(
            item.checkState(0) == Qt.CheckState.Checked for item in items
        )
        target = Qt.CheckState.Unchecked if all_checked else Qt.CheckState.Checked
        # One update at the end rather than one per row: each itemChanged would
        # otherwise re-walk the whole tree.
        blocked = self._tree.blockSignals(True)
        for item in items:
            item.setCheckState(0, target)
        self._tree.blockSignals(blocked)
        self._on_item_changed()

    def _update_items(self, statuses=_CHECKABLE, visible_only: bool = False) -> list:
        """Tickable rows. ``visible_only`` stops at what the filter is showing.

        The two callers want different things. "Select all" must only reach
        rows on screen, or a filtered list would tick a thousand invisible mods.
        `_checked_entries` must reach every row, or typing in the filter box
        would silently drop ticks the user had already made.
        """
        items = []
        for i in range(self._tree.topLevelItemCount()):
            group = self._tree.topLevelItem(i)
            if visible_only and group.isHidden():
                continue
            for j in range(group.childCount()):
                child = group.child(j)
                if visible_only and child.isHidden():
                    continue
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

    def _current_entry(self) -> Optional[ModEntry]:
        item = self._tree.currentItem()
        return item.data(0, _ENTRY_ROLE) if item is not None else None

    # -- overriding MO2's ignore flag --------------------------------------

    def _on_context_menu(self, pos) -> None:
        item = self._tree.itemAt(pos)
        entry = item.data(0, _ENTRY_ROLE) if item is not None else None
        if entry is None:
            return
        self._tree.setCurrentItem(item)

        menu = QMenu(self._tree)
        menu.setToolTipsVisible(True)
        chosen: dict = {}

        def add(handler, text: str, tip: str = ""):
            action = menu.addAction(text)
            if tip:
                action.setToolTip(tip)
            chosen[action] = handler

        add(self._open_page, "Open on Nexus")

        # One row, through the same code the buttons use, so a single download
        # or install cannot drift from the bulk one. Both are worded for what
        # will actually happen: on a free account nothing is queued, a page is
        # opened -- see _queue_downloads.
        if entry.status in _DOWNLOADABLE:
            if self._premium is False:
                add(
                    lambda: self._download_entries([entry]),
                    "Open this mod's download page",
                    "Your account is not Premium, so this opens the file on "
                    "Nexus for you to click 'Mod Manager Download'.",
                )
            else:
                add(
                    lambda: self._download_entries([entry]),
                    f"Download {entry.latest_version or 'the latest file'}",
                    "Send just this mod to MO2's downloads, without ticking it.",
                )
        elif entry.status in _INSTALLABLE and entry.download is not None:
            add(
                lambda: self._install_entries([entry]),
                f"Install {entry.download.file_name}",
                "Run MO2's installer on the archive already in your downloads "
                "folder.",
            )

        add(
            lambda: self._edit_note(entry),
            "Edit note..." if entry.note else "Add a note...",
            "Why you left this mod the way it is, in your own words. Kept on the "
            "mod itself, so it is still there next time.",
        )

        forced = bool(entry.forced_version) and entry.status != ModEntry.IGNORED
        if entry.status == ModEntry.IGNORED:
            menu.addSeparator()
            add(
                lambda: self._force_update(entry),
                f"Download {entry.latest_version} anyway",
                "Offer this update here without changing anything in MO2.",
            )
        elif forced:
            menu.addSeparator()
            add(
                lambda: self._unforce_update(entry),
                "Respect MO2's ignore flag again",
            )

        ignored_version = (entry.ignored_version or "").strip()
        if ignored_version:
            # MO2's ignore flag names one *specific* version, so a flag left
            # over from an older release is not hiding anything -- Cyberpunk
            # Ultra Plus carries ignoredVersion=6.2.2.0 while its page is on
            # 9.1.5.0, and the row is correctly an update. Offering "clear the
            # ignore flag" there with no qualifier reads as though the update
            # were being suppressed, so say which of the two this is.
            if ignore_is_spent(entry):
                text = f"Clear MO2's stale ignore flag ({ignored_version})"
                tip = (
                    f"This flag names {ignored_version}, which is older than "
                    f"{entry.latest_version or 'the current release'}, so it is "
                    "not hiding this update. Clearing it only tidies up."
                )
            else:
                text = f"Clear MO2's ignore flag ({ignored_version})"
                tip = (
                    "MO2 is hiding this update. Clearing it makes MO2's own "
                    "modlist show it too."
                )
            add(lambda: self._unignore(entry), text, tip)

        handler = chosen.get(menu.exec(self._tree.viewport().mapToGlobal(pos)))
        if handler is None:
            return
        # Run it from the event loop rather than from a slot connected to the
        # action. Every one of these opens a modal dialog and then rebuilds the
        # list, and doing that while the menu's own exec() is still unwinding
        # rebuilds a tree Qt has not finished laying out -- which is how a saved
        # note stayed invisible until the window was reopened.
        QTimer.singleShot(0, handler)

    def _mod_of(self, entry: ModEntry):
        try:
            return self._organizer.modList().getMod(entry.internal_name)
        except Exception:
            return None

    def _remember(self, entry: ModEntry, values: dict) -> Optional[str]:
        """Store plugin settings on a mod, through MO2. Returns an error, or None.

        MO2 keeps these in the mod's own meta.ini and writes them itself, so
        nothing here goes behind its back and the choices survive a restart.
        """
        mod = self._mod_of(entry)
        if mod is None:
            return f"MO2 no longer has a mod called {entry.internal_name}."
        try:
            for key, value in values.items():
                mod.setPluginSetting(self._plugin_name, key, value)
        except Exception as exc:
            log_exception(
                _log, f"Could not store settings on {entry.display_name}", exc
            )
            return f"MO2 refused to store this on the mod: {exc}"

        if entry.internal_name in self._unignored:
            # Storing a setting makes MO2 rewrite the whole meta.ini from
            # memory, and its memory still holds the ignoredVersion this window
            # cleared earlier in the session. Clear it again rather than let a
            # note quietly bring the ignore flag back.
            error = clear_ignored_version(mod.absolutePath())
            if error:
                _log.warning(tag(f"Could not re-clear the ignore flag: {error}"))
        return None

    def _set_force(self, entry: ModEntry, version: str) -> Optional[str]:
        """Record that this version is wanted whatever MO2's ignore flag says."""
        error = self._remember(entry, {FORCE_SETTING: version})
        if error is None:
            entry.forced_version = version
        return error

    def _set_note(self, entry: ModEntry, note: str) -> Optional[str]:
        """Record why the user decided whatever they decided about this mod."""
        error = self._remember(
            entry,
            {NOTE_SETTING: note, NOTE_VERSION_SETTING: entry.latest_version if note else ""},
        )
        if error is None:
            entry.note = note
            entry.note_version = entry.latest_version if note else ""
        return error

    def _edit_note(self, entry: ModEntry) -> None:
        note, ok = QInputDialog.getMultiLineText(
            self,
            "Note",
            f"Why does {entry.display_name} look the way it does?\n"
            "Leave it empty to remove the note.",
            entry.note,
        )
        if not ok:
            return

        note = note.strip()
        error = self._set_note(entry, note)
        if error:
            QMessageBox.warning(self, "Could not save the note", error)
            return

        if note:
            _log.info(
                tag(
                    f"Note on {entry.display_name} (at {entry.latest_version or '?'}): "
                    f"{note}"
                )
            )
            self._status_label.setText(f"Saved a note on {entry.display_name}.")
        else:
            _log.info(tag(f"Removed the note on {entry.display_name}"))
            self._status_label.setText(f"Removed the note on {entry.display_name}.")
        self._populate(self._entries)
        self._render_details(entry)

    def _reclassify_ignore(self, entry: ModEntry) -> None:
        """Re-answer "is this ignored?" for one row, without a rescan.

        The same two branches the scan takes (`UpdateScan._note_download`), so
        a row changed by hand ends up wherever a fresh scan would have put it --
        including the install queue, when the archive is already downloaded.
        """
        entry.download = None
        if is_ignored(entry):
            entry.status = ModEntry.IGNORED
            entry.message = f"Update to {entry.latest_version} ignored in MO2."
            return

        entry.status = ModEntry.UPDATE
        entry.message = ""
        note_download(entry, self._downloads)
        if entry.status == ModEntry.UPDATE and (entry.ignored_version or "").strip():
            entry.message = (
                f"MO2 ignores {entry.ignored_version}; offered here anyway."
            )

    def _force_update(self, entry: ModEntry) -> None:
        error = self._set_force(entry, entry.latest_version)
        if error:
            QMessageBox.warning(self, "Could not override the ignore flag", error)
            return
        _log.info(
            tag(
                f"Offering {entry.display_name} {entry.latest_version} despite MO2's "
                f"ignore flag ({entry.ignored_version})"
            )
        )
        self._reclassify_ignore(entry)
        self._populate(self._entries)
        self._status_label.setText(
            f"{entry.display_name} moved to updates. MO2 still ignores "
            f"{entry.ignored_version}; only this window offers it."
        )

    def _unforce_update(self, entry: ModEntry) -> None:
        error = self._set_force(entry, "")
        if error:
            QMessageBox.warning(self, "Could not clear the override", error)
            return
        _log.info(tag(f"Respecting MO2's ignore flag again for {entry.display_name}"))
        self._reclassify_ignore(entry)
        self._populate(self._entries)
        self._status_label.setText(
            f"{entry.display_name} follows MO2's ignore flag again."
        )

    def _unignore(self, entry: ModEntry) -> None:
        mod = self._mod_of(entry)
        path = mod.absolutePath() if mod is not None else ""
        # A flag naming an older version is not suppressing this update; the
        # menu says so and the confirmation has to agree, or the advice below
        # sends the user to an override that would override nothing.
        if ignore_is_spent(entry):
            why = (
                f"That flag names {entry.ignored_version}, which is older than "
                f"{entry.latest_version or 'the current release'}, so it is not "
                "hiding anything -- this update is already being offered here. "
                "Clearing it only tidies up."
            )
        else:
            why = (
                "Use 'Download ... anyway' instead if you only want the update "
                "offered here and would rather leave MO2 alone."
            )
        answer = QMessageBox.question(
            self,
            "Clear MO2's ignore flag",
            f"Clear the ignored version ({entry.ignored_version}) on "
            f"{entry.display_name}?\n\n"
            "MO2 offers no way for a plugin to set this, so the flag is cleared "
            "in the mod's meta.ini directly. MO2 keeps its own copy of that file "
            "in memory, so its modlist will only agree once you restart it.\n\n"
            f"{why}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        # Make MO2 flush this mod's meta *before* editing it, by storing a
        # plugin setting on it: `setPluginSetting` writes the whole file from
        # memory and then clears the mod's changed flag
        # (`modinforegular.cpp:1009`). That does two things. It drops any
        # override -- with the ignore flag gone there is nothing left to
        # override -- and it leaves the mod unchanged as far as MO2 is
        # concerned, so the write below is not undone at shutdown. Doing this
        # after the edit instead would restore the flag immediately.
        self._set_force(entry, "")

        error = clear_ignored_version(path)
        if error:
            _log.warning(tag(f"Could not un-ignore {entry.display_name}: {error}"))
            QMessageBox.warning(
                self,
                "Could not clear the ignore flag",
                f"{entry.display_name}: {error}",
            )
            return

        _log.info(
            tag(
                f"Cleared ignoredVersion={entry.ignored_version} in "
                f"{entry.display_name}'s meta.ini"
            )
        )
        self._unignored.add(entry.internal_name)
        entry.ignored_version = ""
        self._reclassify_ignore(entry)
        self._populate(self._entries)
        self._status_label.setText(
            f"Cleared MO2's ignore flag on {entry.display_name}. Restart MO2 for "
            "its own modlist to agree."
        )

    # -- detail panes ------------------------------------------------------

    def _on_selection_changed(self) -> None:
        entry = self._current_entry()
        self._open_btn.setEnabled(entry is not None)
        if entry is None:
            return

        self._render_details(entry)

        if entry.changelog is None or entry.files is None:
            # Two requests -- changelog and file list -- for every row the
            # selection lands on. Holding the down arrow through a thousand-row
            # list would fire them for every row it passes, and on a free Nexus
            # account the whole hourly allowance is a hundred. Wait for the
            # selection to settle, so only the row actually being read costs
            # anything.
            self._pending_details = entry
            self._details_timer.start(_DETAILS_DELAY_MS)
        else:
            self._pending_details = None
            self._details_timer.stop()
            self._render_changelog(entry)
            self._render_files(entry)

    def _on_details_due(self) -> None:
        entry, self._pending_details = self._pending_details, None
        # The row may have been left behind while the timer ran, or the whole
        # list rebuilt under it by a finished download.
        if entry is None or self._current_entry() is not entry:
            return
        if entry.changelog is None or entry.files is None:
            self._request_details(entry)

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
            # Say whether the flag is still doing anything, so this pane and the
            # context menu tell the same story about a leftover ignore.
            spent = ignore_is_spent(entry)
            rows.append(
                (
                    "Ignored version",
                    entry.ignored_version
                    + (" (older than the current release; not hiding it)" if spent else ""),
                )
            )
        if entry.forced_version:
            rows.append(("Offered anyway", entry.forced_version))
        if entry.note:
            rows.append(("Your note" + _note_age(entry), entry.note))
        if entry.download is not None:
            rows.append(("Already downloaded", entry.download.file_name))

        theme = self._get_theme()
        label = theme.muted(0.35).name()
        accent = theme.colour(entry.status).name()
        body = "".join(
            f"<tr><td style='padding-right:12px;color:{label}'>{html.escape(k)}</td>"
            # A note is free text and can be several lines; everything else here
            # is a single value, so escaping then restoring the breaks is safe.
            f"<td>{html.escape(v).replace(chr(10), '<br>')}</td></tr>"
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
            # An entry can itself be a multi-line block rather than one bullet.
            entries = [part for line in lines for part in _split_lines(str(line))]
            items = "".join(
                f"<li>{part if _HTML_TAG.search(part) else html.escape(part)}</li>"
                for part in entries
            )
            blocks.append(f"<h3>{heading}{marker}</h3><ul>{items}</ul>")

        self._changelog_view.setHtml("".join(blocks))

    def _visible_categories(self) -> set:
        raw = str(self._setting("file_categories", DEFAULT_FILE_CATEGORIES))
        chosen = {part.strip().upper() for part in raw.split(",") if part.strip()}
        # An empty or unrecognisable setting should not blank the list.
        return chosen or set(_ALL_CATEGORIES)

    def _on_show_all_files(self) -> None:
        entry = self._current_entry()
        if entry is not None and entry.files is not None:
            self._render_files(entry)

    def _render_files(self, entry: ModEntry) -> None:
        self._files_tree.clear()
        self._files_hidden_label.clear()
        files = entry.files or []
        if not files:
            self._use_file_btn.setEnabled(False)
            return

        if entry.picked_file_id is None:
            best = _pick_file(files, entry.latest_version, entry.file_line)
            if best is not None:
                entry.picked_file_id = best.get("file_id")

        installed_ids = set(entry.installed_file_ids)
        if not self._show_all_files.isChecked():
            allowed = self._visible_categories()
            keep = set(installed_ids)
            keep.add(entry.picked_file_id)
            visible = [
                info
                for info in files
                if str(info.get("category_name") or "").upper() in allowed
                # Never hide the file you have or the one about to download,
                # whatever category they happen to sit in.
                or info.get("file_id") in keep
            ]
            if len(visible) < len(files):
                self._files_hidden_label.setText(
                    f"{len(files) - len(visible)} file(s) hidden by category."
                )
            files = visible

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

        _autofit(self._files_tree)

    def _on_file_selected(self) -> None:
        item = self._files_tree.currentItem()
        if item is None:
            self._file_desc.clear()
            self._use_file_btn.setEnabled(False)
            return
        info = item.data(0, _ENTRY_ROLE) or {}
        parts = []
        description = _as_paragraph(str(info.get("description") or ""))
        if description:
            parts.append(description)
        changelog = _as_list(str(info.get("changelog_html") or ""))
        if changelog:
            parts.append(f"<h4>File changelog</h4>{changelog}")
        self._file_desc.setHtml(
            "".join(parts) or "<p><i>No description for this file.</i></p>"
        )
        self._use_file_btn.setEnabled(True)

    def _on_file_context_menu(self, pos) -> None:
        """Right-click a row in the Files tab: download that exact file.

        The button beside the list only *marks* a file as the one to use, so
        the download still comes from the mod list. Downloading straight from
        here skips that round trip -- and passes the file record itself to
        `_queue_downloads`, so an old or optional file the picker would never
        have chosen is the one that gets queued.
        """
        item = self._files_tree.itemAt(pos)
        entry = self._current_entry()
        if item is None or entry is None:
            return
        info = item.data(0, _ENTRY_ROLE) or {}
        if not info.get("file_id"):
            return
        self._files_tree.setCurrentItem(item)

        menu = QMenu(self._files_tree)
        menu.setToolTipsVisible(True)
        chosen: dict = {}

        def add(handler, text: str, tip: str = ""):
            action = menu.addAction(text)
            if tip:
                action.setToolTip(tip)
            chosen[action] = handler

        name = str(info.get("name") or "this file")
        if self._premium is False:
            add(
                lambda: self._open_download_pages([(entry, info)]),
                "Open this file's page on Nexus",
                "Your account is not Premium, so this opens the file on Nexus "
                "for you to click 'Mod Manager Download'.",
            )
        else:
            add(
                lambda: self._queue_downloads([(entry, info)]),
                f"Download {name}",
                "Send this exact file to MO2's downloads, whatever the mod list "
                "would otherwise have picked.",
            )
        if info.get("file_id") != entry.picked_file_id:
            add(
                self._on_use_file,
                "Use this file for the update",
                "Mark it as the file the mod list downloads when this row is "
                "ticked, without downloading anything now.",
            )

        handler = chosen.get(menu.exec(self._files_tree.viewport().mapToGlobal(pos)))
        if handler is None:
            return
        # Same reason as the mod list's menu: these open a modal dialog and
        # then repaint, which must not happen inside the menu's own exec().
        QTimer.singleShot(0, handler)

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
        self._install_entries(self._checked_entries(_INSTALLABLE))

    def _install_entries(self, entries: list) -> None:
        """Install these rows' already-downloaded archives.

        Split out of `_on_install` so the context menu can install one row
        through exactly the same confirmation, hide-after-install rule and
        `_just_installed` bookkeeping as the button does. A single row that
        skipped any of that would be a second, quietly different install path.
        """
        targets = [e for e in entries if e.download is not None]
        if not targets:
            return

        lines = "\n".join(
            f"  {e.display_name}  ->  {e.download.file_name}" + _note_block(e)
            for e in targets
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
                log_exception(_log, f"Install failed for {entry.display_name}", exc)
                failed.append(f"{entry.display_name}: {exc}")
                continue
            if result is None:
                _log.info(tag(f"Install cancelled for {entry.display_name}"))
                failed.append(f"{entry.display_name}: installation was cancelled.")
                continue

            installed += 1
            # Remembered because the rescan below reads meta.ini off disk and
            # MO2 has not written this file id there yet. See _seed_installed_ids.
            # Keyed on the mod MO2 says it created, not on the row's own name:
            # its installer lets the user rename, and the rescan will find it
            # under whatever name it now carries.
            if entry.download.file_id:
                try:
                    name = result.name() or entry.internal_name
                except Exception:
                    name = entry.internal_name
                self._just_installed[name] = int(entry.download.file_id)
            _log.info(
                tag(f"Installed {entry.display_name} from {entry.download.file_name}")
            )
            if hide_after:
                error = downloads_index.hide(entry.download)
                if error:
                    _log.warning(tag(f"Could not hide download: {error}"))
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

    def _open_download_pages(self, plan: list) -> None:
        """Free-account path: open each file's Nexus page in the browser.

        Deep-links the Files tab and the file itself, so the user lands on the
        row with the 'Mod Manager Download' button rather than on the
        description. Clicking it hands MO2 an ``nxm://`` link carrying the key
        and expiry that a free account's download needs -- the same route MO2's
        own "Query info" uses, and the only one it accepts without Premium.
        """
        lines = "\n".join(
            f"  {e.display_name}  ->  {info.get('name')} "
            f"({info.get('version') or '?'}, {_size(info.get('size_kb'))})"
            + _note_block(e)
            for e, info in plan
        )
        answer = QMessageBox.question(
            self,
            "Open download pages",
            f"Your Nexus account is not Premium, so Nexus will not hand this "
            f"window a download link.\n\nOpen {len(plan)} mod page(s) in your "
            f"browser instead?\n\n{lines}\n\nOn each page, click 'Mod Manager "
            "Download'. MO2 catches the link and this window follows the "
            "download from there, exactly as it would for a Premium account.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        for entry, info in plan:
            url = entry.page_url
            file_id = info.get("file_id")
            if file_id:
                url = f"{url}?tab=files&file_id={int(file_id)}"
            _log.info(
                tag(f"Opening {url} for {entry.display_name} (free account)")
            )
            QDesktopServices.openUrl(QUrl(url))

        self._status_label.setText(
            f"Opened {len(plan)} page(s). Click 'Mod Manager Download' on each; "
            "this window picks the download up from MO2."
        )

    def _hide_downloads_after_install(self) -> bool:
        """Whether to hide a download once this window installs it.

        MO2 applies its own preference only on the Downloads-tab install path
        (``organizercore.cpp:911``). The archive install that plugins get goes
        through ``installArchive``, which marks the download installed but
        never hides it -- so decide here, following MO2 unless the plugin
        setting overrides it.
        """
        choice = str(self._setting("hide_downloads_after_install", "auto")).lower()
        if choice == "always":
            return True
        if choice == "never":
            return False

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
        self._download_entries(self._checked_entries(_DOWNLOADABLE))

    def _download_entries(self, targets: list) -> None:
        """Download the newest wanted file for each of these rows.

        Split out of `_on_download` for the context menu, which hands in one
        row. The file-list fetch below is the reason a row cannot just call
        `_start_downloads`: a mod the user never selected has no `files` yet.
        """
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

        self._queue_downloads(plan, skipped)

    def _queue_downloads(self, plan: list, skipped: list = ()) -> None:
        """Confirm and hand a list of (entry, file record) pairs to MO2.

        Takes the pairs rather than the rows so the Files tab can queue one
        *named* file -- picking a file there and downloading it are the same
        action, and routing that through `_start_downloads` would re-derive the
        choice from `picked_file_id` and hand back whatever it preferred.
        """
        skipped = list(skipped)
        if not plan:
            QMessageBox.information(
                self,
                "Nothing to download",
                "No downloadable files were found for the selected mods.",
            )
            return

        # Nexus hands direct download links to Premium accounts only. MO2 asks
        # for one anyway and, when the account is free and no nxm:// key came
        # with the request, logs a warning and returns without starting or
        # failing anything (`nexusinterface.cpp:955`) -- the reserved download
        # sits pending forever and this window is never told. So on a free
        # account do not queue at all: send the user to the page, which is the
        # only way MO2 ever gets a usable key.
        if self._premium is False:
            self._open_download_pages(plan)
            return

        # The note earns its keep here more than anywhere else: a mod ignored
        # for a reason comes back into Updates the moment the author ships
        # anything newer, and this is the last screen before it downloads.
        lines = "\n".join(
            f"  {e.display_name}  ->  {info.get('name')} "
            f"({info.get('version') or '?'}, {_size(info.get('size_kb'))})"
            + _note_block(e)
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

        manager = self._organizer.downloadManager()
        started, failed = 0, []
        for entry, info in plan:
            file_id = info.get("file_id")
            try:
                download_id = manager.startDownloadNexusFileForGame(
                    entry.domain, int(entry.mod_id), int(file_id)
                )
            except Exception as exc:
                log_exception(_log, f"Download failed to start for {entry.display_name}", exc)
                failed.append(f"{entry.display_name}: {exc}")
                continue

            # MO2 returns 0 when the download never got queued -- a collection
            # link, or a file for a different game (downloadmanager.cpp:745).
            if not download_id:
                _log.warning(
                    tag(
                        f"MO2 declined to queue {entry.domain}/{entry.mod_id} file "
                        f"{file_id} for {entry.display_name}"
                    )
                )
                failed.append(f"{entry.display_name}: MO2 did not queue the download.")
                continue

            _log.info(
                tag(
                    f"Queued download {download_id}: {entry.display_name} -> "
                    f"{info.get('name')} ({entry.domain}/{entry.mod_id} file {file_id})"
                )
            )
            started += 1
            entry.status = ModEntry.DOWNLOADING
            entry.message = "Queued in MO2's Downloads tab."
            self._in_flight[int(download_id)] = (entry, info)

        self._populate(self._entries)

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
                f"Sent {started} download(s) to MO2. This window follows them from here."
            )

    # -- teardown ----------------------------------------------------------

    def reject(self):
        # MO2's download handlers cannot be unregistered, so mark the window
        # gone and let them return early rather than touch dead widgets.
        self._closed = True
        self._save_geometry()
        self._in_flight.clear()
        if self._client is not None:
            self._client.cancel()
        self._cache.save()
        super().reject()


# -- helpers ---------------------------------------------------------------


# A note the user wrote, marked so it is not read as something the scan said.
_NOTE_MARK = "✎"
# Notes are free text and the columns size to their widest cell, so one essay
# would widen the Notes column for all thousand rows. The full text is on the
# row's tooltip and in the Details pane.
_NOTE_LIMIT = 70


def ignore_is_spent(entry) -> bool:
    """True when a mod carries an ignore flag that is no longer hiding anything.

    MO2's flag names one *specific* version, so `is_ignored` stops honouring it
    the moment the page moves past it -- Cyberpunk Ultra Plus sits at
    ``ignoredVersion=6.2.2.0`` with the page on 9.1.5.0 and is correctly listed
    as an update. Offering "clear the ignore flag" on that row with no
    qualifier reads as though the update were being suppressed, which is the
    bug this answers.

    A flag the user has overridden with 'Download ... anyway' is *not* spent:
    it would still apply if the override were dropped, which is exactly what
    'Respect MO2's ignore flag again' does.
    """
    if not (entry.ignored_version or "").strip():
        return False
    if (entry.forced_version or "").strip():
        return False
    return entry.status != ModEntry.IGNORED


def fit_to_screen(available_width: int, available_height: int) -> tuple:
    """The default window size for a screen with this much usable room.

    A share of the screen rather than a pixel size: 1600x1000 is comfortable at
    1440p, off-screen on a 1366x768 laptop and postage-stamp sized on 4K. The
    minimum still wins on a screen too small for it -- a window that cannot
    show its columns is worse than one that overhangs, and Qt lets the user
    move that.
    """
    wide, tall = _DEFAULT_SCREEN_SHARE
    width = max(_MIN_SIZE[0], int(available_width * wide))
    height = max(_MIN_SIZE[1], int(available_height * tall))
    return (
        min(width, max(available_width, _MIN_SIZE[0])),
        min(height, max(available_height, _MIN_SIZE[1])),
    )


def parse_geometry(raw) -> Optional[tuple]:
    """``"x,y,w,h"`` back into four ints, or None if it is not that."""
    parts = str(raw or "").split(",")
    if len(parts) != 4:
        return None
    try:
        return tuple(int(part.strip()) for part in parts)
    except ValueError:
        return None


def _note_age(entry) -> str:
    """" on <version>", when the note was written about something else.

    Saying which release a note was written about matters most exactly when it
    no longer matches: "needs another mod I don't want" was true of 2.0 and
    says nothing about the 2.1 that has since replaced it.
    """
    if not entry.note_version or not entry.latest_version:
        return ""
    if versions_match(entry.note_version, entry.latest_version):
        return ""
    return f" on {entry.note_version}"


def _note_label(entry) -> str:
    """The user's note as one line, marked, and dated when it has gone stale."""
    if not entry.note:
        return ""

    text = " ".join(entry.note.split())
    if len(text) > _NOTE_LIMIT:
        text = text[: _NOTE_LIMIT - 1].rstrip() + "…"

    return f"{_NOTE_MARK}{_note_age(entry)} {text}"


def _note_block(entry) -> str:
    """The user's note, indented under its mod, for a confirmation dialog.

    Not shortened here: a dialog listing a handful of mods has room, and this
    is the last chance to read the reason before acting against it.
    """
    if not entry.note:
        return ""
    lines = [line.strip() for line in entry.note.splitlines() if line.strip()]
    if not lines:
        return ""
    head = f"\n      {_NOTE_MARK}{_note_age(entry)} {lines[0]}"
    return head + "".join(f"\n        {line}" for line in lines[1:])


def _notes_cell(entry) -> str:
    """The Notes column: what the user wrote, then what the scan found.

    The user's words come first because the scan's are boilerplate -- every row
    in the Ignored group says the same sentence -- and a column you skim is one
    where the distinctive part has to be at the left edge.
    """
    return "   ".join(part for part in (_note_label(entry), entry.message) if part)


def _matches(entry, terms: list) -> bool:
    """Whether a row answers to every word typed in the filter box.

    Searches everything on the row that carries words -- the mod name, the file
    line, the Nexus page name, the scan's own note and the user's. Versions are
    deliberately left out: typing "1.2" to find a mod would otherwise match
    every row that happens to sit on that version.
    """
    if entry is None:
        return False
    haystack = " ".join(
        (
            entry.display_name,
            entry.row_label,
            entry.message,
            entry.nexus_name,
            entry.note,
        )
    ).lower()
    return all(term in haystack for term in terms)


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
    """Best guess at the file to offer.

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
