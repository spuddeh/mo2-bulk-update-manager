"""Structural checks that import nothing and run anywhere.

    python tools/test_structure.py

Every ``self._something(...)`` call in the plugin has to resolve to a method
that actually exists on its class. Python does not find out until the line
runs, and a scan only reaches most of these on a live modlist -- so a deleted
method ships clean through ``py_compile``, through the offline harness, and
into MO2, where it surfaces as ``AttributeError`` mid-scan.

That is not hypothetical: an edit that replaced a block of ``updater.py`` by
slicing between two method names took ``_decide`` out with it, because
``_decide`` sat between them. Everything still compiled and every behavioural
check still passed, because nothing outside a real scan calls it.
"""

import ast
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(_ROOT, "mo2_bulk_update_manager")

FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print(f"  ok    {name}")
    else:
        print(f"  FAIL  {name}   {detail}")
        FAILURES.append(name)


def self_attrs(node):
    """Names bound as ``self.x = ...`` anywhere in a class body."""
    found = set()
    for sub in ast.walk(node):
        if isinstance(sub, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = sub.targets if isinstance(sub, ast.Assign) else [sub.target]
            for t in targets:
                if (
                    isinstance(t, ast.Attribute)
                    and isinstance(t.value, ast.Name)
                    and t.value.id == "self"
                ):
                    found.add(t.attr)
    return found


print("every self._method() call resolves")
for filename in sorted(os.listdir(PKG)):
    if not filename.endswith(".py"):
        continue
    path = os.path.join(PKG, filename)
    tree = ast.parse(open(path, encoding="utf-8").read())
    for cls in [n for n in ast.walk(tree) if isinstance(cls_ := n, ast.ClassDef)]:
        defined = {m.name for m in cls.body if isinstance(m, ast.FunctionDef)}
        attrs = self_attrs(cls)
        missing = set()
        for sub in ast.walk(cls):
            if not isinstance(sub, ast.Call):
                continue
            fn = sub.func
            if (
                isinstance(fn, ast.Attribute)
                and isinstance(fn.value, ast.Name)
                and fn.value.id == "self"
                and fn.attr.startswith("_")
                and fn.attr not in defined
                and fn.attr not in attrs
            ):
                missing.add(fn.attr)
        check(
            f"{filename}::{cls.name}",
            not missing,
            f"calls undefined {sorted(missing)}" if missing else "",
        )


# Methods and classes the Python bindings gained after MO2 2.5.2, from
# plugin_python/src/mobase/wrappers/basic_classes.cpp at 6623e197 (2.5.2)
# against 0b0f34b2 (2.5.3 betas). On 2.5.2 each one is an AttributeError, and
# three of them shipped before this check existed. IFileTree's glob() and walk()
# are left out: the plugin has no file trees, and the names collide with os.walk.
#
# Most names are unique to the binding they were added to, so any call counts.
# The rest are only post-2.5.2 on one class, so the receiver has to match too:
# `version` is new on IOrganizer but has always existed on IModInterface.
POST_252_METHODS = {
    "startDownloadNexusFileForGame": None,  # IDownloadManager
    "uploader": None,  # IModInterface
    "uploaderUrl": None,  # IModInterface
    "executablesList": None,  # IOrganizer
    "getProfile": None,  # IOrganizer
    "instanceManager": None,  # IOrganizer
    "instanceName": None,  # IOrganizer
    "profileNames": None,  # IOrganizer
    "formVersion": None,  # IPluginList
    "headerVersion": None,  # IPluginList
    "isBlueprintFlagged": None,  # IPluginList
    "version": "organizer",  # IOrganizer; IModInterface.version is old
    "author": "mod",  # IModInterface and IPluginList; IPlugin.author is old
    "description": "pluginList",  # IPluginList; IPlugin.description is old
}
POST_252_CLASSES = {"IExecutable", "IExecutablesList", "IInstanceManager"}


def receiver_text(node) -> str:
    """The receiver of an attribute call as source-ish text, e.g. 'self._organizer'."""
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def receiver_matches(text: str, hint: str) -> bool:
    last = text.rsplit(".", 1)[-1].lstrip("_").lower()
    if hint == "organizer":
        return last.endswith("organizer")
    if hint == "mod":
        return last in ("mod", "imod", "entry_mod") or text.endswith("getMod()")
    if hint == "pluginList":
        return "pluginlist" in text.replace("_", "").lower()
    return False


def guarded_names(func) -> set:
    """Attribute names a function checks with hasattr(..., "name") before use."""
    names = set()
    for sub in ast.walk(func):
        if (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Name)
            and sub.func.id == "hasattr"
            and len(sub.args) == 2
            and isinstance(sub.args[1], ast.Constant)
            and isinstance(sub.args[1].value, str)
        ):
            names.add(sub.args[1].value)
    return names


print()
print("every MO2 call added after 2.5.2 sits behind a hasattr() fallback")
for filename in sorted(os.listdir(PKG)):
    if not filename.endswith(".py"):
        continue
    path = os.path.join(PKG, filename)
    tree = ast.parse(open(path, encoding="utf-8").read())
    unguarded = []
    funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for func in funcs:
        guarded = guarded_names(func)
        for sub in ast.walk(func):
            if isinstance(sub, ast.Attribute) and sub.attr in POST_252_CLASSES:
                if receiver_text(sub.value) == "mobase":
                    unguarded.append(f"mobase.{sub.attr} (line {sub.lineno})")
            if not (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)):
                continue
            name = sub.func.attr
            if name not in POST_252_METHODS or name in guarded:
                continue
            text = receiver_text(sub.func.value)
            if text == "self":
                continue
            hint = POST_252_METHODS[name]
            if hint is not None and not receiver_matches(text, hint):
                continue
            unguarded.append(f"{text}.{name}() (line {sub.lineno})")
    check(
        f"{filename} is 2.5.2-safe",
        not unguarded,
        f"needs a hasattr() fallback: {sorted(set(unguarded))}" if unguarded else "",
    )

print()
print("FAILED: " + ", ".join(FAILURES) if FAILURES else "all checks passed")
sys.exit(1 if FAILURES else 0)
