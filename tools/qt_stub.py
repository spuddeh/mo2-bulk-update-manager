"""Enough of PyQt and mobase to import the plugin's modules outside MO2.

Call ``install()`` before importing anything from ``mo2_update_manager``. Used
by ``test_overrides.py``; ``umd_debug.py`` does not need it, because the Nexus
half of the plugin already runs on the standard library alone.

**What this is good for:** pure functions -- string formatting, version
comparison, file rewriting. Nothing else. Every Qt name resolves to a stub that
accepts any call and returns a zero-valued sentinel, so a widget built against
it neither paints nor complains, and a test that appears to exercise one is
testing nothing. Anything Qt-facing or MO2-facing has to be checked by
installing into an instance and restarting it.

``mobase.VersionInfo`` is the one thing modelled rather than stubbed, because
``scanner.is_newer`` asks it real questions. It is deliberately kept *lenient*,
like MO2's own prefix-matching parser (``versioninfo.cpp:27``), rather than
stricter: a stub wrong in the opposite direction validates behaviour the live
plugin does not have, which has already cost one round trip with a user.
"""

import re
import sys
import types


class _Any(int):
    """Stands in for any Qt enum, signal or handle. Usable as an int."""

    def __new__(cls, *args, **kwargs):
        return super().__new__(cls, 0)

    def __getattr__(self, name):
        return _Any()

    def __call__(self, *args, **kwargs):
        return _Any()


class _Meta(type):
    def __getattr__(cls, name):
        return _Any()


class _Stub(metaclass=_Meta):
    """Stands in for any Qt class, including as a base class."""

    def __init__(self, *args, **kwargs):
        pass

    def __getattr__(self, name):
        return _Any()


def _fake(name):
    module = types.ModuleType(name)
    module.__getattr__ = lambda _attr: _Stub
    sys.modules[name] = module
    return module


_PREFIX = re.compile(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:\.(\d+))?")


class VersionInfo:
    def __init__(self, text="", *rest):
        self._match = _PREFIX.match(str(text).strip())
        self._parts = (
            tuple(int(g or 0) for g in self._match.groups()) if self._match else ()
        )

    def isValid(self):
        return bool(self._match)

    def canonicalString(self):
        return ".".join(str(p) for p in self._parts)

    def _cmp(self, other):
        return (self._parts > other._parts) - (self._parts < other._parts)

    def __gt__(self, other):
        return self._cmp(other) > 0

    def __ge__(self, other):
        return self._cmp(other) >= 0


def install():
    for package in ("PyQt5", "PyQt6"):
        _fake(package)
        for sub in ("QtCore", "QtGui", "QtWidgets", "QtNetwork"):
            _fake(f"{package}.{sub}")

    mobase = types.ModuleType("mobase")
    mobase.VersionInfo = VersionInfo
    mobase.IOrganizer = _Stub
    mobase.IPluginTool = _Stub
    mobase.PluginSetting = _Stub
    mobase.ModState = types.SimpleNamespace(active=1)
    mobase.ReleaseType = types.SimpleNamespace(ALPHA=0)
    sys.modules["mobase"] = mobase
