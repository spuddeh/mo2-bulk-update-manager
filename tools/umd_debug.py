"""Standalone harness for the Nexus-facing half of the plugin.

Runs outside MO2 with plain stdlib, so you can confirm credentials and API
behaviour without restarting MO2 every time.

    python tools/umd_debug.py creds
    python tools/umd_debug.py validate
    python tools/umd_debug.py updated <domain> [1d|1w|1m]
    python tools/umd_debug.py mod <domain> <mod_id>
    python tools/umd_debug.py files <domain> <mod_id>
    python tools/umd_debug.py changelog <domain> <mod_id>

Credentials are never printed, only their source and length.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

import importlib.util
import types

# Load credentials.py under a stand-in package rather than importing the real
# one: the real __init__ pulls in plugin.py, which needs mobase and PyQt from
# MO2's embedded interpreter. The stand-in still has to be a package, because
# the module uses relative imports.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PKG_DIR = os.path.join(_ROOT, "mo2_update_manager")

_pkg = types.ModuleType("_umd")
_pkg.__path__ = [_PKG_DIR]
sys.modules["_umd"] = _pkg


def _load(name):
    spec = importlib.util.spec_from_file_location(
        f"_umd.{name}", os.path.join(_PKG_DIR, f"{name}.py")
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"_umd.{name}"] = module
    spec.loader.exec_module(module)
    return module


_load("log")
resolve_auth = _load("credentials").resolve_auth

API_BASE = "https://api.nexusmods.com/v1"


def _auth():
    auth, note = resolve_auth(os.environ.get("NEXUS_API_KEY", ""))
    if note:
        print(f"note: {note}")
    if auth is None:
        sys.exit(1)
    return auth


def _get(path: str, absolute: str = ""):
    auth = _auth()
    request = urllib.request.Request(absolute or f"{API_BASE}/{path}")
    request.add_header("Accept", "application/json")
    request.add_header("Application-Name", "MO2")
    request.add_header("Application-Version", "2.5.3")
    request.add_header("Protocol-Version", "1.0.0")
    request.add_header("User-Agent", "MO2-UpdateManager-debug")
    auth.apply_to(lambda name, value: request.add_header(
        name.decode("ascii"), value.decode("utf-8")
    ))

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            for header in ("x-rl-hourly-remaining", "x-rl-daily-remaining"):
                value = response.headers.get(header)
                if value:
                    print(f"{header}: {value}")
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code}: {exc.reason}")
        body = exc.read().decode("utf-8", "replace")
        print(body[:500])
        sys.exit(2)


def main(argv: list[str]) -> None:
    if not argv:
        print(__doc__)
        return

    command, rest = argv[0], argv[1:]

    if command == "creds":
        auth, note = resolve_auth(os.environ.get("NEXUS_API_KEY", ""))
        if note:
            print(f"note: {note}")
        if auth is None:
            print("No credentials found.")
            return
        print(f"scheme: {auth.scheme}")
        print(f"source: {auth.source}")
        print(f"token length: {len(auth.token)}")
        return

    if command == "validate":
        auth, _ = resolve_auth(os.environ.get("NEXUS_API_KEY", ""))
        if auth is not None and auth.scheme == "oauth":
            # v1/users/validate is API-key only; OAuth identifies via the
            # accounts service instead.
            data = _get("", absolute="https://users.nexusmods.com/oauth/userinfo")
        else:
            data = _get("users/validate.json")
        keys = (
            "user_id",
            "sub",
            "name",
            "is_premium",
            "membership_roles",
            "group_id",
        )
        print(json.dumps({k: v for k, v in data.items() if k in keys}, indent=2))
        return

    if command == "updated":
        domain = rest[0]
        period = rest[1] if len(rest) > 1 else "1w"
        data = _get(f"games/{domain}/mods/updated.json?period={period}")
        print(f"{len(data)} mod(s) updated in the last {period}")
        for row in data[:10]:
            print(f"  {row['mod_id']}  {row.get('latest_file_update')}")
        return

    if command == "mod":
        data = _get(f"games/{rest[0]}/mods/{rest[1]}.json")
        print(
            json.dumps(
                {
                    k: data.get(k)
                    for k in ("name", "version", "status", "available", "updated_timestamp")
                },
                indent=2,
            )
        )
        return

    if command == "files":
        data = _get(f"games/{rest[0]}/mods/{rest[1]}/files.json")
        for info in data.get("files", []):
            print(
                f"  [{info.get('category_name')}] {info.get('name')} "
                f"v{info.get('version')} file_id={info.get('file_id')} "
                f"primary={info.get('is_primary')}"
            )
        return

    if command == "changelog":
        data = _get(f"games/{rest[0]}/mods/{rest[1]}/changelogs.json")
        for version, lines in data.items():
            print(f"  {version}")
            for line in lines:
                print(f"      {line}")
        return

    print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
