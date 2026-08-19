# Installing and upgrading

## Requirements

- Mod Organizer 2 **2.5.3** or later. Developed against 2.5.3beta12.
- A Nexus account signed in to MO2, under **Settings > Nexus**.
- Windows, because credentials come from the Windows Credential Manager.

Nexus Premium is not required, but the free path works differently and has never been tested. Read
[Free accounts](free-accounts.md) first if that is you.

## Installing

Copy the `mo2_bulk_update_manager/` folder into your MO2 `plugins/` directory, so you have:

```text
<MO2 install>\plugins\mo2_bulk_update_manager\
```

Restart MO2. The tool appears under **Tools > MO2 Bulk Update Manager**.

There is nothing to configure. It reads MO2's own Nexus login, so the first scan starts on its own
when you open the window.

If you use several MO2 instances, install it into each one. The scan cache is shared across
instances on the same install, so the second instance starts warm.

## Upgrading

Replace the folder. Settings, notes and ignore overrides are stored on the mods themselves, in each
mod's `meta.ini`, so nothing is lost.

**If you have a folder called `mo2_update_manager`, delete it.** MO2 loads every plugin folder it
finds, so leaving both installs the plugin twice and you will see two entries on the Tools menu.

The plugin was called *Update Manager* during development, until that turned out to be the name of
an [existing MO2 plugin](https://www.nexusmods.com/site/mods/1895). Anything left behind under the
old name, meaning plugin settings, per-mod notes and its scan cache, is **abandoned rather than
migrated**, and that is deliberate.

MO2 keys per-mod plugin settings on a plugin's display name, and `pluginDataPath()` is shared by
every plugin. So `Update%20Manager\...` in a `meta.ini` and `update_manager_cache.json` in
`plugins/data/` belong to that other plugin, not this one. A migration was written and then removed
for exactly that reason: it would have read and cleared another plugin's state. Nothing here reads a
key or a file it did not write.

Nothing is lost in practice, because this was never released under the old name.

## Uninstalling

Delete the folder. To remove what it stored:

- The scan cache is one file, `plugins/data/bulk_update_manager_cache.json`.
- Notes and ignore overrides live in each mod's own `meta.ini`, under `[Plugins]`, prefixed with the
  percent-escaped plugin name. MO2 rewrites those files itself; there is no need to edit them by
  hand unless you want the entries gone.
- Window size and position are one line under `[PluginPersistance]` in `ModOrganizer.ini`.

Nothing is written to the Windows Credential Manager at any point, and your Nexus token is never
logged or displayed.

## If something goes wrong

The plugin writes to MO2's own log, tagged `[BulkUpdateManager]`:

```text
<instance>\logs\mo_interface.log
```

At the default INFO level that covers what each scan found and every download and install it
started. Raise MO2's log level to DEBUG for per-mod detail, which is what shows why a particular mod
landed where it did.

**The Nexus v3 endpoints this relies on are badged Experimental**, meaning Nexus may change or
withdraw them without the deprecation window their stable endpoints get. If the plugin breaks with
no code change on your side, that is the first place to look rather than the last.
