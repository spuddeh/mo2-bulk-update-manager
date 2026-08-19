# Development

The plugin is an `IPluginTool`. It imports `mobase` and PyQt from MO2's own embedded interpreter,
neither of which exists on a normal Python install, so **the only complete test is a restart and
rescan inside MO2**. Three harnesses exist to keep that loop short. Each is useful for one thing and
blind to the rest, and saying which is the point.

## `tools/umd_debug.py`

Drives the Nexus API from a shell, on the standard library alone.

```bash
python tools/umd_debug.py creds                    # which credentials were found
python tools/umd_debug.py validate                 # confirm they work
python tools/umd_debug.py updated starfield 1w     # the bulk update feed
python tools/umd_debug.py mod starfield 8868       # version / status / availability
python tools/umd_debug.py files starfield 8868     # file list and categories
python tools/umd_debug.py changelog starfield 8868 # changelog
```

Credentials are never printed, only their source and length.

**What it is for:** confirming what the API actually returns before writing code against a field
name. Nexus field names mislead often enough that this has paid for itself repeatedly.
`changelog_html` is usually plain text. `is_primary` is frequently set on an archived file.
`file_updates` came back empty for the exact mod it should have described.

**Blind spot:** it never touches MO2, the window, or classification.

## `tools/test_overrides.py`

The window's pure logic: the ignore override, note formatting, the filter's matching, window sizing,
reuse of an archive already on disk, and the one place this plugin rewrites a file MO2 owns.

```bash
python tools/test_overrides.py
```

Behaviour is pinned to real mods rather than invented ones, because the interesting cases are all
ones the obvious implementation gets wrong. *Native Interactions Framework* and *Praetor Suit
Flashlight Fix* for dead-ended chains, *Cyberpunk Ultra Plus* for a spent ignore flag.

It leans on `tools/qt_stub.py`, which fakes just enough of PyQt and `mobase` for the plugin's modules
to import. **Every Qt name resolves to a stub that accepts any call and returns zero**, so a test
that appears to exercise a widget is testing nothing. Good for pure functions and nothing else.

The one thing modelled rather than stubbed is `mobase.VersionInfo`, because `scanner.is_newer` asks
it real questions. It is kept deliberately *lenient*, matching MO2's own prefix-matching parser
(`versioninfo.cpp:27`). **A stub must be wrong in the same direction as the real thing:** a stricter
one once validated behaviour the live plugin did not have, and cost a round trip with a user.

## `tools/test_structure.py`

Asserts that every `self._method()` call resolves to a method that exists on its class. Pure AST,
imports nothing, runs anywhere.

```bash
python tools/test_structure.py
```

That sounds redundant until you meet the failure it exists for. An edit replaced a block of
`updater.py` by slicing between two method names and took `_decide` out with it, because `_decide`
sat between them. `py_compile` passed. Every behavioural check passed. It deployed to three MO2
instances and died on the first scan, because nothing outside a live scan calls `_decide`.

**`py_compile` proves a file parses, not that a method still exists.** A harness that calls the
changed function directly proves less than it looks like it does, because it reaches the code it was
written against and nothing around it.

## What none of them reach

Anything that paints, and anything that actually calls MO2: `installMod`, `setPluginSetting`,
`downloadManager()`, theme colours, the tree widget, geometry restore. Those are checked by
installing into an instance and restarting it, and there is no substitute.

## Ground rules

- **MO2's source is the documentation.** Verify every `mobase` call against the MO2 source before
  relying on it. The Python bindings in `plugin_python/src/mobase/wrappers/basic_classes.cpp` decide
  what exists at all, and several things MO2 has in C++ are not exposed, including a setter for
  `ignoredVersion`.
- **A mod's `meta.ini` belongs to MO2, which answers from memory.** Reading it races MO2's write,
  writing it gets overwritten, and `clearPluginSettings` saves without setting the flag `saveMeta`
  checks. Three separate bugs came from this.
- **Never read or delete anything filed under a name this plugin does not currently use.** *Update
  Manager* is somebody else's plugin. See [Installing and upgrading](install.md).
- **Before deleting an unused symbol, ask what it was for.** `page_ahead_of` was removed on correct
  evidence that nothing called it, and turned out to be the fix for a real bug two commits later.

## Module layout

| File | Role |
| --- | --- |
| `plugin.py` | `IPluginTool` entry point and settings |
| `dialog.py` | The window |
| `updater.py` | Scan engine: decides what to ask Nexus, classifies the answers |
| `nexus.py` | Async Nexus client on QtNetwork, with rate-limit tracking |
| `scanner.py` | Reads MO2's modlist, maps game names to Nexus domains, places mods in update chains, clears MO2's ignore flag |
| `downloads.py` | Indexes MO2's downloads folder by `(mod id, file id)` |
| `theme.py` | Category colours solved for contrast against the live theme |
| `cache.py` | On-disk record of the last known state of each mod |
| `credentials.py` | Reads MO2's Nexus credentials from the Windows Credential Manager |
