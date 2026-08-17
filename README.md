# Update Manager (MO2 plugin)

**Note:** This plugin was built with assistance from an LLM (Claude). The code has been reviewed and the Nexus-facing half tested against the live API, but keep that in mind.

An MO2 tool that checks every Nexus-backed mod in your profile in one pass, tells you which ones have updates and which ones have been **hidden or removed from Nexus**, shows the changelog and file list side by side, and sends downloads straight to MO2's Downloads tab.

## The Problem

MO2 can tell you a mod has an update, but on a large modlist you have to force the check, wait, and then still open the Nexus page in a browser to get the file. And nothing tells you when a mod you rely on has quietly been pulled from Nexus.

## What it does

- **One request per game, not one per mod.** The scan uses Nexus' `mods/updated` feed, which reports every mod in a game changed in the last day/week/month in a single call. Results are cached on disk, so a routine check on a 500-mod list costs a handful of requests instead of 500.
- **Compares file lines, not page versions.** See [Multi-file mod pages](#multi-file-mod-pages) — this is why it catches updates MO2's own check misses.
- **Flags delisted mods.** A mod page that returns 404, or reports `available: false` / a hidden status, is called out separately from ordinary updates.
- **Knows what you already downloaded.** If the newer archive is sitting in MO2's downloads folder, the mod moves to *Downloaded, waiting to be installed* and the button becomes **Install selected** — no wasted second download.
- **Respects MO2's ignored updates.** A version you dismissed with MO2's *Ignore update* goes to a collapsed *Ignored* group instead of nagging. A version newer than the one you ignored comes back.
- **Changelog and file description in the window.** Pick a mod, read what changed and what each file contains, without opening a browser.
- **Download button.** Sends the chosen file to MO2's download queue via `IDownloadManager`, the same path MO2 uses for an `nxm://` link.
- **Writes back to MO2.** Newer versions are recorded on the mod, so MO2's own modlist shows its update flag too. Turn this off in the plugin settings if you'd rather it didn't.

## Requirements

- Mod Organizer 2 **2.5.3** or later (developed against 2.5.3beta12)
- A Nexus account signed in to MO2 (**Settings > Nexus**)
- Windows — credentials are read from the Windows Credential Manager

**Premium vs free accounts:** Nexus only issues direct download links to Premium accounts. On a free account the scan, changelogs and delisting checks all work; use **Open on Nexus** and click *Mod Manager Download* to get the file.

## Installation

Copy the `mo2_update_manager/` folder into your MO2 `plugins/` directory:

```text
<MO2 install>\plugins\mo2_update_manager\
```

Restart MO2. The tool appears under **Tools > Update Manager**.

## Usage

1. Open **Tools > Update Manager**. A quick scan starts automatically.
2. Mods are grouped by outcome (see the table below).
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
| **Ignored in MO2** | You used MO2's *Ignore update* on exactly this version | Nothing |
| **Could not be checked** | The request failed — network, rate limit, or a Nexus error | Rescan later; the reason is in the Notes column |
| **Not checked** | No result and no cached record for this mod | Rescan. If it persists, run a deep scan |
| **Up to date** | Nothing newer in your file line | Nothing |

Columns size themselves to their contents rather than stretching to fill, so long mod names are never squeezed or elided; the list scrolls sideways instead. Columns stay draggable and reorderable, and refit when a collapsed group is opened.

Each category shows as a coloured dot beside the mod name and as the colour of its group heading. Mod names keep the theme's own text colour, so nothing fights the stylesheet for readability.

Those category colours are not fixed values. MO2 applies themes as Qt stylesheets rather than palettes, so there is no "is this dark?" flag to read — but a widget's *effective* palette does pick up the stylesheet's colours once Qt polishes it. The plugin measures the list's real background and then solves each category's lightness until it clears a 4.5:1 contrast ratio against it. Hues stay fixed so a category stays recognisable; only lightness and saturation move. On an unusual mid-tone background it falls back to the most readable value that hue can manage.

### When the page has moved past your file

Say you installed the optional *Collision Mesh Preview* file from the *World Builder* page. That file has only ever been uploaded once, so there is genuinely no update *for it* — but the main World Builder file has moved to 1.0.81. MO2's own check calls that an update, because it compares page versions.

Those mods stay under **Up to date**, where they belong, and get a violet dot plus a note in the Notes column: *"Your file is the newest of its kind. The page itself is now at 1.0.81, so check it if this stops working."*

This was a top-level group once, and that was a mistake. Every other group has an action attached — download it, install it, decide whether to keep it. This one has none: there is no newer file to fetch, only a page worth a glance. Sitting beside real work, it read as a to-do item that could never be completed, and an entry that never clears is one you eventually stop reading — along with the groups next to it. As an annotation on an up-to-date mod, it is there when you go looking, which is exactly when you would wonder why MO2 disagrees.

The condition for the note is deliberately hard to meet. Three things must all hold:

1. The file you installed is **not** the page's primary upload. If it is, the page version tracks it and can never be ahead.
2. Both your file's version and the page's version are **plain dotted numbers**. Anything else — `1.0.0joker`, `1.0.1b`, a date, a build string — means the author numbers that file on its own scheme, and comparing it against the page version is meaningless.
3. The page version is genuinely higher.

Condition 2 is stricter than `mobase.VersionInfo`, whose regex is a prefix match (`versioninfo.cpp:27`) and happily reads `1.0.0joker` as a perfectly good `1.0.0`. That leniency is right for "is there a newer file in this line?" and wrong here, where it manufactures comparisons between unrelated numbering schemes. On a 77-mod test list, the three conditions together take this note from 11 mods to 2 — and both survivors are real optional add-ons sitting several versions behind their page.

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

This plugin compares **file lines** instead — the sequence of uploads sharing a file name. Each MO2 mod is pinned to its own line, so both Window Utils mods get their own row and their own correct answer, and the Fake Lights update is found despite the stale page version.

Pinning works in three steps, best first:

1. **The exact Nexus file id.** MO2 records it in the mod's `meta.ini` under `[installedFiles]` as `1\fileid=…`. Unambiguous when present.
2. **The installed version**, matched against each upload's own version.
3. **The installation archive name.** Nexus file names are a prefix of the download filename; the longest match wins, so *Window Utils Showcase* beats *Window Utils*.

Step 1 is unavailable more often than you would expect — 26 of 543 mods in a real Starfield profile carry no `[installedFiles]` entry, having been installed before MO2 recorded one. Any tie left after steps 2 and 3 is broken toward the more plausible file: still current, then the page's primary upload, then a main file, then newest.

If none match, the plugin falls back to the page version and says so in the Notes column.

Some authors put the version *in* the file name — *World Builder 1.0.0*, *World Builder 1.0.81* — which would make every upload its own one-member line and hide the update entirely. Version tokens are therefore stripped when deriving a line's identity, carefully enough that a *4K Texture Pack* keeps its name.

The **File** column shows the file line whenever it differs from the MO2 mod name. In the Files tab, `•` marks the file you currently have installed and `✓` marks the one that will be downloaded.

A different file at the *same* version is **not** treated as an update. It once was, on the theory that it meant a silent re-upload; across a 543-mod Starfield list that fired four times and was wrong every time — a main file beside a miscellaneous one, an optional 1k texture pack beside the full-size main, an archived copy of the very file already installed. Those are alternatives, not successors.

For the same reason, a file line is only widened past an exact name match when doing so leaves **at most one still-current upload**. Authors who name each release after its version (*World Builder 1.0.0* → *1.0.81*) need the widening; authors who name *variants* that way do not. The tell is several simultaneously-current members:

- *Simply Faster Ladders* offers 125 / 150 / 175 / 200 Percent as four current MAIN files. You install one.
- *Starfield HD Overhaul* hosts eighteen parts on one page, each its own MAIN file at its own version. The page reads 3.14 because part 18 does; part 01 at 3.08 is perfectly current.

Within a line, a successor also has to share the installed file's category — a main file is not the successor of an optional one just because it is newer. That preference is dropped once your file is marked `OLD_VERSION`, since every superseded upload ends up in that category whatever it started as.

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
- **Each queried page costs two requests** — the page itself for availability, plus its file list. The file list is cached, so a page that hasn't changed costs nothing on the next scan.
- **The cache is shared** across MO2 instances on the same install — it lives in `plugins/data/update_manager_cache.json`. Delete that file to force a clean baseline.
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

### Layout

| File | Role |
| --- | --- |
| `plugin.py` | `IPluginTool` entry point and settings |
| `dialog.py` | The window |
| `updater.py` | Scan engine: decides what to ask Nexus, classifies the answers |
| `nexus.py` | Async Nexus v1 client on QtNetwork, with rate-limit tracking |
| `scanner.py` | Reads MO2's modlist, maps game names to Nexus domains, resolves file lines |
| `downloads.py` | Indexes MO2's downloads folder by `(mod id, file id)` |
| `theme.py` | Category colours solved for contrast against the live theme |
| `cache.py` | On-disk record of the last known state of each mod |
| `credentials.py` | Reads MO2's Nexus credentials from the Windows Credential Manager |
| `icon.svg` | Tools-menu icon |
