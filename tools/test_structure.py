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

print()
print("FAILED: " + ", ".join(FAILURES) if FAILURES else "all checks passed")
sys.exit(1 if FAILURES else 0)
