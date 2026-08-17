# MO2 Bulk Update Manager

**Note:** This plugin was built with assistance from an LLM (Claude). The code has been reviewed and the Nexus-facing half tested against the live API, but keep that in mind.

An MO2 tool that checks every Nexus-backed mod in your profile in one pass, tells you which ones have updates and which ones have been **hidden or removed from Nexus**, shows the changelog and file list side by side, and sends downloads straight to MO2's Downloads tab.

## The Problem

MO2 can tell you a mod has an update, but on a large modlist you have to force the check, wait, and then still open the Nexus page in a browser to get the file. And nothing tells you when a mod you rely on has quietly been pulled from Nexus.

## What it does

- **One request per game, not one per mod.** The scan uses Nexus' `mods/updated` feed, which reports every mod in a game changed in the last day/week/month in a single call. Results are cached on disk, so a routine check on a 500-mod list costs a handful of requests instead of 500.
- **Compares update chains, not page versions.** See [Multi-file mod pages](#multi-file-mod-pages) — this is why it catches updates MO2's own check misses.
- **Flags delisted mods.** A mod page that returns 404, or reports `available: false` / a hidden status, is called out separately from ordinary updates.
- **Knows what you already downloaded.** If the newer archive is sitting in MO2's downloads folder, the mod moves to *Downloaded, waiting to be installed* and the button becomes **Install selected** — no wasted second download.
- **Respects MO2's ignored updates — and lets you overrule them.** A version you dismissed with MO2's *Ignore update* goes to a collapsed *Ignored* group instead of nagging, and a version newer than the one you ignored comes back. Right-click a row to take the update anyway, or to clear MO2's flag outright. See [Overriding MO2's ignore flag](#overriding-mo2s-ignore-flag).
- **A filter box.** Type part of a name to cut a thousand-mod list down to the one you are looking for. Group headings show *"Up to date (3 of 1067)"* while filtering, empty groups drop out, and ticks you have already made survive.
- **Notes in your own words.** Right-click any mod to record *why* it looks the way it does — "2.0 needs a mod I don't want, 1.9 still works". The note shows on the row, is searchable, and is repeated in the confirmation dialog the day you go to download that mod anyway. See [Why you left it that way](#why-you-left-it-that-way).
- **Changelog and file description in the window.** Pick a mod, read what changed and what each file contains, without opening a browser.
- **Download button.** Sends the chosen file to MO2's download queue via `IDownloadManager`, the same path MO2 uses for an `nxm://` link.
- **Writes back to MO2.** Newer versions are recorded on the mod, so MO2's own modlist shows its update flag too. Turn this off in the plugin settings if you'd rather it didn't.

## Requirements

- Mod Organizer 2 **2.5.3** or later (developed against 2.5.3beta12)
- A Nexus account signed in to MO2 (**Settings > Nexus**)
- Windows — credentials are read from the Windows Credential Manager

**Premium vs free accounts:** Nexus only issues direct download links to Premium accounts. On a free account the scan, changelogs and delisting checks all work; use **Open on Nexus** and click *Mod Manager Download* to get the file.

## Installation

Copy the `mo2_bulk_update_manager/` folder into your MO2 `plugins/` directory:

```text
<MO2 install>\plugins\mo2_bulk_update_manager\
```

Restart MO2. The tool appears under **Tools > MO2 Bulk Update Manager**.

**Upgrading from a folder called `mo2_update_manager`?** Delete it. MO2 loads every plugin folder it finds, so leaving both installs the plugin twice. The tool was called *Update Manager* until 2026-08-18, when the name turned out to collide with an unrelated MO2 plugin.

That display name is also the key MO2 files plugin settings under — including the per-mod notes and ignore overrides this plugin writes into each mod's `meta.ini` — so the old name is still read where the new one has nothing yet, and the on-disk scan cache is adopted rather than rebuilt. Nothing is deleted on the strength of a rename. The plugin's own settings under **Settings > Plugins** do reset to their defaults, because MO2 only exposes settings for a plugin that is currently loaded; re-set them there if you had changed any.

## Usage

1. Open **Tools > MO2 Bulk Update Manager**. A quick scan starts automatically.
2. Mods are grouped by outcome (see the table below). Type in the **Filter** box to narrow the list to one mod.
3. Click a mod to read its changelog and file list. The file that will be downloaded is marked ✓, the one you have installed is marked •; pick a different one with **Download this file instead**. Old and archived uploads are hidden — the World Builder page drops from twelve files to three — and **Show every file** brings them back for that session. The file you have and the one queued for download are never hidden, whatever category they sit in.
4. Tick what you want, then **Download selected** or **Install selected**.
5. Downloads land in MO2's Downloads tab, and the rows follow them: **Updates available** → **Downloading** → **Downloaded, waiting to be installed**, with no rescan. Ticks and the selected row survive each rebuild, so you can queue a batch and install it as it arrives.

### The groups

| Group | What it means | What to do |
| --- | --- | --- |
| **Downloading** | This window queued the file with MO2 and it has not finished yet | Wait — the row moves on by itself |
| **Downloaded, waiting to be installed** | A newer file for this mod is already in your downloads folder | Tick it and hit **Install selected** |
| **Updates available** | A newer file exists on Nexus and you don't have it | Tick it and hit **Download selected** |
| **No longer on Nexus** | The page 404s or reports a removed status | Decide whether to keep the mod |
| **Hidden or unavailable** | The page exists but is hidden or under moderation | Usually temporary; check back |
| **Ignored in MO2** | You used MO2's *Ignore update* on exactly this version | Nothing — or right-click to take it anyway, or to note why you didn't |
| **Could not be checked** | The request failed — network, rate limit, or a Nexus error | Rescan later; the reason is in the Notes column |
| **Not checked** | No result and no cached record for this mod | Rescan. If it persists, run a deep scan |
| **Up to date** | Nothing newer in your update chain | Nothing |

Columns size themselves to their contents rather than stretching to fill, so long mod names are never squeezed or elided; the list scrolls sideways instead. Columns stay draggable and reorderable, and refit when a collapsed group is opened, when the filter changes what is on screen, and after every rebuild.

That last one has to be asked for twice. `resizeColumnToContents` measures the rows the view can *see*, so a rebuild that runs inside another event loop — a context menu still unwinding, a modal dialog closing — measures a tree Qt has not laid out yet, and every column comes back at the header's default width. With elision off that clips silently rather than showing an ellipsis: a note saved from the right-click menu was in its row all along, just past the right-hand edge of a 100-pixel column. So the refit runs immediately *and* again from the event loop, and the right-click actions are dispatched after the menu has closed rather than from inside it.

Each category shows as a coloured dot beside the mod name and as the colour of its group heading. Mod names keep the theme's own text colour, so nothing fights the stylesheet for readability.

Those category colours are not fixed values. MO2 applies themes as Qt stylesheets rather than palettes, so there is no "is this dark?" flag to read — but a widget's *effective* palette does pick up the stylesheet's colours once Qt polishes it. The plugin measures the list's real background and then solves each category's lightness until it clears a 4.5:1 contrast ratio against it. Hues stay fixed so a category stays recognisable; only lightness and saturation move. On an unusual mid-tone background it falls back to the most readable value that hue can manage.

### Finding one mod in a thousand

The **Filter** box above the list takes words, not a pattern. Every word has to appear somewhere in the row — the mod name, the chain name, the Nexus page name, or the note — so `cet frame` finds *CET Frame Generation* without your having to remember which order the words came in. Versions are deliberately not searched: typing `1.2` to find a mod would otherwise match every row that happens to sit on 1.2.

While a filter is active:

- Group headings count what is showing: **Up to date (3 of 1067)**. A group with no matches disappears rather than sitting there empty.
- Groups that normally start collapsed open up, because a match hidden inside *Up to date* is not a match you can see.
- **Select all** only reaches rows on screen. Ticking every one of a thousand invisible mods is never what anyone meant by it.
- Ticks you have already made **survive**. A row you ticked and then filtered out still downloads, and still appears by name in the confirmation dialog — filtering is a way to look at the list, not a way to change what you asked for.

### Overriding MO2's ignore flag

MO2's *Ignore update* dismisses one specific version, and there is no way to take it back from MO2's own interface once the version is gone from view. Right-clicking a row here gives you two ways out, and they are not the same thing:

Before either, consider **Add a note…** — see [Why you left it that way](#why-you-left-it-that-way). An ignore you can explain in six months is worth more than one you can undo.

**"Download `<version>` anyway"** offers the update in this window and changes nothing in MO2. The mod moves out of *Ignored in MO2* and into *Updates available* — or straight into the install queue, if the archive is already in your downloads folder — with a note saying MO2 still ignores it. This is the one to use.

It is recorded as a plugin setting *on the mod*, which MO2 stores in that mod's own `meta.ini` and writes itself, so it survives a restart and nothing goes behind MO2's back. It is scoped to the version it was granted for, exactly as MO2 scopes the dismissal it overrides: if the author later ships something newer, that is a new decision, and MO2's flag is stale anyway by then. Right-click again to give the decision back to MO2.

**"Clear MO2's ignore flag for this mod"** genuinely un-ignores the mod, for MO2 as well as for this window — with a caveat the confirmation dialog states outright.

`IModInterface` exposes `ignoredVersion()` and no setter; the Python bindings stop at the getter (`basic_classes.cpp:253`). So the flag can only be reached through the mod's `meta.ini`, which this plugin already reads for its installed file ids. It rewrites that one line and leaves the rest of the file byte-for-byte alone, for the same reason `downloads.hide` does — a mod's meta carries the entire Nexus description as one escaped value, and round-tripping that through an INI parser is a large risk for no gain.

The caveat: **MO2 holds its own copy.** `ModInfoRegular` reads `meta.ini` once at startup and writes it back from memory whenever the mod is marked changed, including at shutdown (`modinforegular.cpp:68`). So MO2's own modlist keeps showing the mod as ignored until you restart it, however the file reads.

Two things keep MO2 from simply undoing the write:

1. **The mod is flushed first.** Storing a plugin setting on a mod makes MO2 write the whole `meta.ini` from memory and then clear that mod's changed flag (`modinforegular.cpp:1009`). Doing that immediately *before* the edit leaves the mod unchanged as far as MO2 is concerned, so nothing gets written over the top of it at shutdown.
2. **This window stops dirtying it.** Writing newer versions back to MO2 is skipped for anything un-ignored this session, because `setNewestVersion` marks the mod changed and would hand the stale flag straight back.

Something else editing that mod in the same session — renaming it, recategorising it, MO2's own Nexus check — will still restore the flag. That is the honest limit of a setter MO2 does not expose.

### Why you left it that way

An ignored update is a decision, and the reason for it lives in your head for about a week. *Winds of Cydonia* is a real example: the author's 2.0 requires a second mod the user doesn't want, the old version still works, and the changelog says as much. Six months later all that survives is a mod sitting in a collapsed group with no explanation, and the obvious move — take the update — is the wrong one.

Right-click any mod and choose **Add a note…**. Whatever you type comes back:

- **On the row**, in the Notes column after whatever the scan found, marked `✎` so it is never confused with something the plugin said. Long notes are shortened to keep the column from stretching across a thousand rows; the whole thing is on the row's tooltip and in the **Details** tab.
- **In the filter.** Searching `cet` finds every mod you noted as needing Cyber Engine Tweaks, whatever they are called.
- **In the confirmation dialog**, indented under the mod, when you tick it for download or install. This is where it earns its keep: MO2's ignore flag covers one version, so the day the author ships 2.1 the mod reappears under *Updates available* like any other — and the last screen before it downloads is the one that reminds you why you said no last time.

A note records the latest version at the time you wrote it. Once the mod moves past that, the note is shown as **✎ on 2.0** rather than plain `✎`, because *"needs another mod I don't want"* was a statement about 2.0 and says nothing about the 2.1 that replaced it. Re-saving the note re-dates it.

Notes are stored as plugin settings on the mod, which MO2 keeps in that mod's own `meta.ini` under `[Plugins]` and writes itself — the same mechanism as the ignore override, and for the same reason: nothing goes behind MO2's back and the note survives a restart. MO2's own *Notes* field is not used because the bindings expose `notes()` with no setter.

If you go looking for one in a `meta.ini`, MO2 percent-escapes the plugin name, so it reads:

```ini
[Plugins]
Update%20Manager\note=Staying on v1.0 as it does not require Heart of Cydonia
Update%20Manager\note_version=1.3
```

### Quick scan vs deep scan

**Rescan** (quick) asks Nexus what changed in each game since your last scan, checks only those mods, and re-verifies a rotating slice of the oldest cached results so delistings still surface over time.

**Deep scan** queries every mod individually. Slower and far more API requests, but it is the only way to catch a mod that was pulled from Nexus long before your last scan. Run it occasionally, or after a long break.

## Multi-file mod pages

A Nexus page is not one download. It can host several unrelated files, each with its own version history — a main file, an addon, a showcase pack — and plenty of authors never bump the *page* version when they update one of them.

Comparing an installed mod against the page version therefore gets two things wrong, and both are common:

| Page | Page version | What is really there |
| --- | --- | --- |
| Disable Fake Lights with Path Tracing (16060) | `0.4` | Main file is at **v0.5**. Page version never bumped, so a page-level check says "up to date". |
| Window Utils (26589) | `1.0.3` | Two separate downloads: *Window Utils* v1.0.0→v1.0.3 and *Window Utils Showcase* v1.0.0b→v1.0.1b. Both installed, both need updates, but they share one page. |

Nexus' v3 API answers this directly. It models a page as a set of **update chains** — one per download, each an ordered list of that download's uploads — so the sequence no longer has to be inferred from file names and version strings. Each MO2 mod is pinned to one chain and compared only against that, which is why both Window Utils mods get their own row and their own correct answer, and why the Fake Lights update is found despite the stale page version.

A scan resolves that in four calls, none of them per-mod:

1. **Status for every page**, batched — this is what flags a page as removed, hidden or under moderation.
2. **Installed file ids to their chain**, batched. The v3 `game_scoped_id` *is* the legacy file id, which is the one MO2 records in the mod's `meta.ini` under `[installedFiles]`, so this is exact when it is there.
3. **The page's chains**, only for mods where it is not. See below.
4. **Each chain's versions**, cached on disk and re-fetched only when Nexus says the mod changed or the copy is older than `recheck_days`.

### Which upload in a chain is the current one

Not the highest `position`. Position records where an upload sits in the chain, and an author who back-fills old files gets them appended at the end.

Neither is `is_primary`, despite the name. In four of five chains examined it was set on an *archived* upload holding the highest whole-numbered position, while the file people actually want sat just below it at a fractional one.

| Chain | What the obvious field says | What is actually current |
| --- | --- | --- |
| 7237540 | Highest position is an archived *"v1.04 do not download"* at `4.0` | **v1.07**, main, at `3.77` |
| 2764699 | `is_primary` is set on v2.0.17, archived, at `31.0` | **v2.0.21**, main, at `30.9` |

`category == "main"` is the one field that holds up, so that decides it — highest-positioned main file, falling back to the newest upload Nexus has not retired, and only then to `is_primary`. Getting this wrong cost twelve false updates before it was found.

The comparison that follows needs no version parsing at all: your file is an update if Nexus has retired it, or if the chain's current file sits at a higher position. That quietly fixes a class of bug that version strings cannot — *MovementAndCameraTweaks* went `v1.41` → `v1.5`, which every semantic comparison reads as a downgrade because 41 > 5, while the author meant it as a decimal. A different upload that Nexus has neither retired nor promoted is **not** an update; it is another current file on the same chain, which is what `2.1` and `2.1-alternate` are to each other.

### When MO2 never recorded a file id

MO2 only started writing `[installedFiles]` at some point, so a few percent of any real profile has nothing exact to resolve — 26 of 543 mods in a real Starfield profile. Those fall back to matching the page's chain names against the installation archive name, then the MO2 mod name, longest match first.

The *page* name is deliberately not used. It describes the page rather than any one download, so it cannot discriminate between that page's chains — and it usually reads like the longest of them, which made it actively wrong. Page 9643 offers `LaserSightDots_Enabled` and `LaserSightDots_Enabled_BulletFollowsDot`; matching on the page name picked the latter for a mod installed from the former.

A chain chosen this way is cached, keyed on the evidence that produced it rather than on the page, because several MO2 mods can share one page and land on different chains. Keying by page let whichever was seen last overwrite the other.

Failing all of that, the mod lands in **Not checked** and says so: MO2 has no record of which file it came from and the page has several candidates. Reinstalling it from Nexus fixes it permanently, because MO2 writes the file id.

### What the columns show

The **File** column shows the chain's name whenever it differs from the MO2 mod name. In the Files tab, `•` marks the file you currently have installed and `✓` marks the one that will be downloaded.

## How it authenticates

MO2 stores your Nexus credentials in the Windows Credential Manager. The plugin reads them so there is nothing to set up:

| Credential | Used as |
| --- | --- |
| `ModOrganizer2_NEXUS_OAUTH_TOKENS` | `Authorization: Bearer …` (MO2's OAuth login) |
| `ModOrganizer2_APIKEY` | `apikey: …` (MO2's legacy key, if present) |

If neither is readable or the OAuth token has expired, the plugin falls back to a personal API key you can paste into its settings (from [nexusmods.com/users/myaccount?tab=api](https://www.nexusmods.com/users/myaccount?tab=api)).

Nothing is written to the credential store, and the token is never logged or displayed.

## Settings

| Setting | Default | What it does |
| --- | --- | --- |
| `api_key` | *(empty)* | Fallback personal API key, used only if MO2's own login can't be read |
| `file_categories` | `MAIN,UPDATE,OPTIONAL,MISCELLANEOUS` | Nexus file categories to list in the Files tab. `OLD_VERSION` and `ARCHIVED` are hidden by default — a long-running mod can carry sixty superseded uploads |
| `recheck_days` | `30` | During a quick scan, re-verify cached results older than this many days so delistings surface |
| `write_back_versions` | `true` | Record the newest version on the MO2 mod so the main modlist shows its update flag |
| `check_disabled_mods` | `true` | Check mods disabled in the current profile. Turning this off shortens both the scan and the list — a typical profile carries a lot of disabled mods you have no intention of updating |
| `scan_on_open` | `true` | Scan as soon as the window opens. Turn off to open without spending API requests, then press **Rescan** when you want them |
| `hide_downloads_after_install` | `auto` | Hide a download once this window installs it. `auto` follows MO2's own setting; `always` and `never` override it |
| `show_up_to_date` | `true` | List mods that are up to date |
| `show_ignored` | `true` | List mods whose update you dismissed with MO2's *Ignore update* |

## Notes and limits

- **Mods with no Nexus id are skipped** — separators, hand-built mods, xEdit/CK output folders, Creation Club content. The count is shown after each scan.
- **Version comparison is best-effort.** Nexus version strings are free text; some authors use dates or build numbers. When two versions can't be parsed, a difference is reported as an update rather than silently ignored.
- **Two MO2 mods from the same Nexus page** each get their own row and their own comparison, but the page is only queried once.
- **Scanning is batched, not per-mod.** Page status and installed-file lookups go out in batch requests; only update chains are fetched individually, and those are cached, so a chain that has not changed costs nothing on the next scan. A 1071-mod profile with 908 pages settles in around six requests.
- **The Files tab is v1 and is not cached.** File size, description and per-file changelog have no v3 equivalent, so opening a mod costs two requests each time. Classification never touches them.
- **A page that has moved past your file is no longer flagged.** Comparing your file against the *page* version needed a page version, and v3 chains do not carry one. It used to be an annotation on an up-to-date row; the case it caught — you have the newest of an optional file while the page's main file has moved on — is real but no longer detected.
- **Installing something re-scans immediately, and MO2's meta.ini has not caught up.** `[installedFiles]` is set in memory when MO2 installs a mod and flushed to disk a moment later, while the plugin reads that file directly — there is no accessor for it. The rescan starts a millisecond after `installMod` returns and loses the race, so a mod that was just correctly updated looks like one MO2 never recorded a file id for. On a page hosting a single download it resolves anyway; on a page hosting two — *Cyberpunk Ultra Plus* and *Ultra Skin* share page 10490 — it landed in **Not checked** until the next scan. No timing fixes that, so the window instead remembers the file id it asked MO2 to install and uses it until the disk copy agrees.
- **The cache is shared** across MO2 instances on the same install — it lives in `plugins/data/bulk_update_manager_cache.json`. Delete that file to force a clean baseline.
- **Rate limits** are read from Nexus' own `x-rl-*` response headers and shown in the window. The scan stops early rather than running you out of requests.
- **Download progress comes from MO2, not from polling.** `IDownloadManager` exposes `onDownloadComplete` / `onDownloadFailed` / `onDownloadPaused`, and the id returned by `startDownloadNexusFileForGame` is the same one those callbacks report. Note that MO2 returns `0` when it declines to queue a download at all — a collection link, or a file for a different game (`downloadmanager.cpp:745`). Those handlers cannot be unregistered, so they check that the window is still open before touching anything.
- **"Hide downloads after installation" is honoured.** MO2 applies that setting itself only on its own Downloads-tab install path (`organizercore.cpp:911`); the archive install that plugins get marks the download installed but never hides it. So when the setting is on, the plugin writes the same `removed=true` flag MO2 writes (`downloadmanager.cpp:910`), touching that one line and nothing else in the meta file.

## Development

`tools/umd_debug.py` exercises the Nexus half of the plugin outside MO2, using only the standard library:

```bash
python tools/umd_debug.py creds                    # which credentials were found
python tools/umd_debug.py validate                 # confirm they work
python tools/umd_debug.py updated starfield 1w     # the bulk update feed
python tools/umd_debug.py mod starfield 8868       # version / status / availability
python tools/umd_debug.py files starfield 8868     # file list and categories
python tools/umd_debug.py changelog starfield 8868 # changelog
```

Credentials are never printed, only their source and length.

`tools/test_overrides.py` checks the parts that can be checked without MO2 — the ignore override, the note formatting, the filter's matching, and the one place this plugin rewrites a file MO2 owns:

```bash
python tools/test_overrides.py
```

It leans on `tools/qt_stub.py`, which fakes just enough of PyQt and `mobase` for the plugin's modules to import. That is good for pure functions and nothing else: every Qt name resolves to a stub that accepts any call and returns zero, so a test that appears to exercise a widget is testing nothing. Anything that paints, or that actually calls MO2, still has to be checked by installing into an instance and restarting it.

The one thing modelled rather than stubbed is `mobase.VersionInfo`, because `scanner.is_newer` asks it real questions. It is kept deliberately *lenient*, matching MO2's own prefix-matching parser (`versioninfo.cpp:27`) — a stricter stub once validated behaviour the live plugin did not have, and cost a round trip with a user.

### Layout

| File | Role |
| --- | --- |
| `plugin.py` | `IPluginTool` entry point and settings |
| `dialog.py` | The window |
| `updater.py` | Scan engine: decides what to ask Nexus, classifies the answers |
| `nexus.py` | Async Nexus v1 client on QtNetwork, with rate-limit tracking |
| `scanner.py` | Reads MO2's modlist, maps game names to Nexus domains, places mods in update chains, clears MO2's ignore flag |
| `downloads.py` | Indexes MO2's downloads folder by `(mod id, file id)` |
| `theme.py` | Category colours solved for contrast against the live theme |
| `cache.py` | On-disk record of the last known state of each mod |
| `credentials.py` | Reads MO2's Nexus credentials from the Windows Credential Manager |
| `icon.svg` | Tools-menu icon |
| `tools/umd_debug.py` | Drives the Nexus API from a shell, on the stdlib alone |
| `tools/test_overrides.py` | Offline checks for the pure logic behind the window |
| `tools/qt_stub.py` | Fakes PyQt and `mobase` so those checks can import the plugin |
