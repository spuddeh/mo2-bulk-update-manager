import os

try:
    from PyQt5.QtGui import QIcon
except ImportError:
    from PyQt6.QtGui import QIcon

import mobase

from ._version import VERSION
from .dialog import DEFAULT_FILE_CATEGORIES, BulkUpdateManagerDialog
from .log import get_logger, tag

_log = get_logger()

PLUGIN_NAME = "MO2 Bulk Update Manager"

# MO2 ships Qt's SVG image format plugin (dlls/imageformats/qsvg.dll), so QIcon
# renders this directly at whatever size the menu asks for.
ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.svg")


class BulkUpdateManagerPlugin(mobase.IPluginTool):

    def __init__(self):
        super().__init__()
        self._organizer = None
        self._parent = None

    def init(self, organizer: mobase.IOrganizer) -> bool:
        self._organizer = organizer
        return True

    def name(self) -> str:
        return PLUGIN_NAME

    def author(self) -> str:
        return "Spuddeh"

    def description(self) -> str:
        return (
            "Checks every Nexus-backed mod in the profile for updates and for pages "
            "that have been hidden or removed, shows the changelog and file list, and "
            "sends downloads straight to MO2."
        )

    def version(self) -> mobase.VersionInfo:
        major, minor, patch = (int(x) for x in VERSION.split("."))
        return mobase.VersionInfo(major, minor, patch, mobase.ReleaseType.FINAL)

    def displayName(self) -> str:
        return PLUGIN_NAME

    def tooltip(self) -> str:
        return "Check Nexus for mod updates and delisted mods"

    def icon(self) -> QIcon:
        return QIcon(ICON_PATH) if os.path.exists(ICON_PATH) else QIcon()

    def setParentWidget(self, widget):
        self._parent = widget

    def settings(self) -> list:
        return [
            mobase.PluginSetting(
                "api_key",
                "Personal Nexus API key, used only if MO2's own Nexus login cannot be "
                "read (nexusmods.com/users/myaccount?tab=api)",
                "",
            ),
            mobase.PluginSetting(
                "recheck_days",
                "Re-fetch a mod's update chain when the cached copy is older than "
                "this many days, even if Nexus did not report the mod as changed",
                30,
            ),
            mobase.PluginSetting(
                "file_categories",
                "Nexus file categories to list in the Files tab, comma separated. "
                "Choose from MAIN, UPDATE, OPTIONAL, MISCELLANEOUS, OLD_VERSION, "
                "ARCHIVED. The file you have installed and the one queued for "
                "download are always shown",
                DEFAULT_FILE_CATEGORIES,
            ),
            mobase.PluginSetting(
                "write_back_versions",
                "Also record the newest version on the MO2 mod, so the main modlist "
                "shows its own update flag",
                True,
            ),
            mobase.PluginSetting(
                "check_disabled_mods",
                "Check mods that are disabled in the current profile. Turning this "
                "off makes scans faster and shorter on a large modlist",
                True,
            ),
            mobase.PluginSetting(
                "scan_on_open",
                "Scan as soon as the window opens. Turn off to open without "
                "spending Nexus API requests, then press Rescan when you want them",
                True,
            ),
            mobase.PluginSetting(
                "hide_downloads_after_install",
                "Hide a download once this window installs it: 'auto' follows MO2's "
                "own setting, or use 'always' / 'never'",
                "auto",
            ),
            mobase.PluginSetting(
                "show_up_to_date",
                "List mods that are up to date",
                True,
            ),
            mobase.PluginSetting(
                "show_ignored",
                "List mods whose update you dismissed with MO2's 'Ignore update'",
                True,
            ),
        ]

    def display(self):
        game = self._organizer.managedGame()
        _log.info(
            tag(
                f"{PLUGIN_NAME} {VERSION} opening (MO2 {self._organizer.version()}, "
                f"game {game.gameShortName() if game else 'unknown'})"
            )
        )
        dialog = BulkUpdateManagerDialog(self._organizer, PLUGIN_NAME, self._parent)
        dialog.exec()
