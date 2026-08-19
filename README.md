# MO2 Bulk Update Manager

An MO2 tool that checks every Nexus-backed mod in your profile in one pass. It tells you which mods
have updates, which have been **hidden or removed from Nexus**, shows the changelog and file list
side by side, and sends downloads straight to MO2's Downloads tab.

> **Built with an LLM (Claude).** The code has been reviewed and the Nexus-facing half tested
> against the live API, but keep it in mind.
>
> **Alpha.** Run by one person, on three MO2 instances, on Cyberpunk 2077 and Starfield only.

## Before you install: is this built for you?

This plugin was written to fit **one person's habits**, and those habits are baked into its
defaults. It is worth a minute to check they are yours.

It assumes you keep a large modlist, that MO2 has the file id for most of your mods (it writes one
whenever you install from a Nexus download), that you want to review a batch of updates and queue
them together, and that your archives live in MO2's own downloads folder where the plugin can find
them. If you install by hand from files you keep elsewhere, or if you update one mod at a time as
you notice it, most of what this does will not help you.

**It has never been tested without a Nexus Premium account.** The author has lifetime Premium, and
so does the only other person who has run it. The free-account path was written by reading MO2's
source rather than by using it, so treat [Free accounts](#free-accounts) as a design intent that
needs a first user, not as a tested feature. Reports very welcome.

## The problem

MO2 can tell you a mod has an update, but on a large modlist you have to force the check, wait, and
then still open the Nexus page in a browser to get the file. And nothing tells you when a mod you
rely on has quietly been pulled from Nexus.

## What it does

**Scans in batches, not per mod.** Page status for a whole profile goes out in one request, and
update chains are cached on disk. A routine check on a 1071-mod, 908-page profile settles in about
13 requests.

**Compares update chains, not page versions.** This is why it catches updates MO2's own check
misses. See [Multi-file mod pages](#multi-file-mod-pages).

**Flags delisted mods.** A page that 404s, or reports a hidden or removed status, is called out
separately from ordinary updates.

**Knows what you already downloaded.** If the newer archive is already in MO2's downloads folder the
mod moves to *Downloaded, waiting to be installed*, so nothing is fetched twice.

**Respects MO2's ignored updates, and lets you overrule them.** See
[Overriding MO2's ignore flag](#overriding-mo2s-ignore-flag).

**Filters a thousand rows down to one.** Type part of a name. Group headings show *"Up to date (3 of
1067)"*, empty groups drop out, and ticks you already made survive.

**Keeps notes in your own words.** Record *why* a mod looks the way it does, and get it back on the
day you go to download it anyway. See [Why you left it that way](#why-you-left-it-that-way).

**Writes back to MO2.** Newer versions are recorded on the mod so MO2's own modlist shows its update
flag too. Turn it off in the settings if you would rather it did not.

## Requirements

- Mod Organizer 2 **2.5.3** or later (developed against 2.5.3beta12)
- A Nexus account signed in to MO2 (**Settings > Nexus**)
- Windows, because credentials come from the Windows Credential Manager

The Nexus v3 endpoints this relies on are badged **Experimental**. If it breaks with no code change
on your side, that is the first place to look.

## Installation

Copy the `mo2_bulk_update_manager/` folder into your MO2 `plugins/` directory, so you have:

```text
<MO2 install>\plugins\mo2_bulk_update_manager\
```

Restart MO2. The tool appears under **Tools > MO2 Bulk Update Manager**.

**Upgrading from a folder called `mo2_update_manager`?** Delete it. MO2 loads every plugin folder it
finds, so leaving both installs the plugin twice. *Update Manager* turned out to be the name of an
[existing MO2 plugin](https://www.nexusmods.com/site/mods/1895), and this one was renamed before
release. Anything left behind under the old name is abandoned rather than migrated, deliberately:
that name belongs to somebody else's plugin, and reading or deleting data filed under it would mean
touching another plugin's state. Nothing is lost, because this was never released under that name.

## Free accounts

Everything except downloading works the same on a free account: the scan, the changelogs, the file
lists and the delisting checks.

**Downloading cannot work the same, and the reason is worth stating plainly.** Nexus issues direct
download links to Premium accounts only. MO2 asks for one regardless, and when the account is free
and no `nxm://` key came with the request, it logs a warning and returns without either starting or
failing the download (`nexusinterface.cpp:955`). The download id it already handed back looks
perfectly valid, so a plugin that trusts it leaves a row stuck at *Downloading* forever.

So on a free account this plugin does not queue downloads at all. It reads your account tier when it
starts, relabels **Download selected** to **Open download pages**, and opens each chosen file's
Nexus page in your browser instead:

```text
https://www.nexusmods.com/<game>/mods/<id>?tab=files&file_id=<file>
```

Click **Mod Manager Download** there. MO2 catches the link, and this window follows the download
from that point exactly as it would for a Premium account. The right-click menu on a row and in the
Files tab changes the same way.

**Budget matters much more on a free account.** Nexus allows 100 requests an hour and 2500 a day for
free accounts, against 2000 and 20000 for Premium (measured 2026-08-20). A routine scan costs about
13, so day-to-day use is fine. **The first scan is not:** with an empty cache every mod needs its
update chain fetched, roughly 900 requests on a 900-page profile, and there is no batch endpoint for
it. Expect the first scan to throttle and resume across several hours. After that the cache carries
it. The window shows what is left of your allowance and stops early rather than locking you out of
MO2's own Nexus features.

## Usage

1. Open **Tools > MO2 Bulk Update Manager**. A quick scan starts automatically.
2. Mods are grouped by outcome. Type in the **Filter** box to narrow the list.
3. Click a mod to read its changelog and file list. `✓` marks the file that will be downloaded, `•`
   the one you have. Old and archived uploads are hidden; **Show every file** brings them back.
4. Tick what you want, then **Download selected** or **Install selected**.
5. Rows follow their downloads on their own: *Updates available*, *Downloading*, *Downloaded,
   waiting to be installed*, with no rescan. Ticks and the selected row survive each rebuild.

**Right-click any row** to act on that one mod without ticking anything: download it, install it if
the archive is already there, open its Nexus page, write a note, or deal with MO2's ignore flag.
**Right-click a row in the Files tab** to download that exact file, whichever one the picker would
otherwise have chosen.

### The groups

| Group | What it means | What to do |
| --- | --- | --- |
| **Downloading** | This window queued the file and it has not finished | Wait, the row moves on by itself |
| **Downloaded, waiting to be installed** | A newer file is already in your downloads folder | Tick it, then **Install selected** |
| **Updates available** | A newer file exists on Nexus and you do not have it | Tick it, then **Download selected** |
| **Superseded on Nexus, your call** | Nexus retired your file and no successor can be determined | Read the Notes column, then decide. **Select all** never touches these |
| **No longer on Nexus** | The page 404s or reports a removed status | Decide whether to keep the mod |
| **Hidden or unavailable** | The page exists but is hidden or under moderation | Usually temporary, check back |
| **Ignored in MO2** | You used MO2's *Ignore update* on exactly this version | Nothing, or right-click to take it anyway |
| **Could not be checked** | The request failed: network, rate limit, or a Nexus error | Rescan later, the reason is in Notes |
| **Not checked** | No result and no cached record | Rescan. If it persists, run a deep scan |
| **Up to date** | Nothing newer in your update chain | Nothing |

Category colours are solved rather than fixed. MO2 applies themes as Qt stylesheets rather than
palettes, so there is no "is this dark?" flag to read, but a widget's effective palette does pick up
the stylesheet once Qt polishes it. The plugin measures the list's real background and moves each
category's lightness until it clears 4.5:1 against it. Hues stay put so a category stays
recognisable.

### Finding one mod in a thousand

The **Filter** box takes words, not a pattern. Every word has to appear somewhere in the row (mod
name, chain name, Nexus page name, or your note), so `cet frame` finds *CET Frame Generation*
whichever order you remember the words in. Versions are deliberately not searched: typing `1.2`
would otherwise match every row sitting on 1.2.

While a filter is active, group headings count what is showing, empty groups disappear, normally
collapsed groups open up, and **Select all** only reaches rows on screen. Ticks you already made
survive: filtering is a way to look at the list, not a way to change what you asked for.

### Overriding MO2's ignore flag

MO2's *Ignore update* dismisses one specific version, and MO2's own interface gives you no way to
take it back once that version is out of view. Right-click gives you two ways out, and they are not
the same thing. Before either, consider **Add a note**: an ignore you can explain in six months is
worth more than one you can undo.

**"Download `<version>` anyway"** offers the update here and changes nothing in MO2. It is recorded
as a plugin setting on the mod, which MO2 keeps in that mod's own `meta.ini` and writes itself, so
it survives a restart with nothing going behind MO2's back. It is scoped to the version it was
granted for, exactly as MO2 scopes the dismissal it overrides. **This is the one to use.**

**"Clear MO2's ignore flag"** genuinely un-ignores the mod for MO2 as well. `IModInterface` exposes
`ignoredVersion()` and no setter (`basic_classes.cpp:253`), so the flag can only be reached through
`meta.ini`. The plugin rewrites that one line and leaves the rest byte for byte alone, because a
mod's meta carries the whole Nexus description as one escaped value.

The caveat, which the confirmation dialog also states: **MO2 holds its own copy.** It reads
`meta.ini` once and writes it back from memory whenever the mod is marked changed, including at
shutdown (`modinforegular.cpp:68`), so MO2's modlist keeps showing the mod as ignored until you
restart it. The plugin flushes the mod first and then stops dirtying it, which keeps MO2 from
undoing the write, but anything else that touches that mod in the same session will restore the
flag. That is the honest limit of a setter MO2 does not expose.

**A flag naming an older version is not hiding anything.** Because MO2 dismisses one specific
version, a flag left over from an earlier release stops applying the moment the page moves past it,
and the mod correctly appears under *Updates available*. The menu says which of the two you are
looking at rather than offering identical wording for both.

### Why you left it that way

An ignored update is a decision, and the reason lives in your head for about a week. *Winds of
Cydonia* is a real example: 2.0 requires a second mod the user does not want, the old version still
works, and six months later all that survives is a mod in a collapsed group with no explanation,
where the obvious move is the wrong one.

Right-click, **Add a note**. What you type comes back in three places: on the row (marked `✎`, so it
is never confused with something the plugin said), in the filter, and **in the confirmation dialog**
when you tick that mod for download. The last one is the point. MO2's flag covers one version, so
the day the author ships 2.1 the mod reappears like any other, and the last screen before it
downloads is the one that reminds you why you said no.

A note records the version it was written about. Once the mod moves past that it shows as **✎ on
2.0** rather than plain `✎`, because *"needs another mod I don't want"* was a statement about 2.0.
Re-saving re-dates it.

Notes are plugin settings on the mod, stored by MO2 in `meta.ini` under `[Plugins]`. MO2's own
*Notes* field is not used because the bindings expose `notes()` with no setter.

### Quick scan vs deep scan

**Rescan** asks Nexus what changed in each game since your last scan, checks only those mods, and
re-verifies a rotating slice of the oldest cached results so delistings still surface.

**Deep scan** queries every mod individually. Slower, and far more requests, but it is the only way
to catch a mod pulled from Nexus long before your last scan. Run it occasionally, or after a break.

## Multi-file mod pages

A Nexus page is not one download. It can host several unrelated files, each with its own version
history, and plenty of authors never bump the *page* version when they update one of them.

| Page | Page version | What is really there |
| --- | --- | --- |
| Disable Fake Lights with Path Tracing (16060) | `0.4` | Main file is at **v0.5**. A page-level check says "up to date" |
| Window Utils (26589) | `1.0.3` | Two downloads: *Window Utils* v1.0.0 to v1.0.3, and *Window Utils Showcase* v1.0.0b to v1.0.1b. Both need updates, both share one page |

Nexus v3 models a page as a set of **update chains**, one per download, each an ordered list of that
download's uploads. Each MO2 mod is pinned to one chain and compared only against that, which is why
both Window Utils mods get their own row and their own answer.

### Which upload in a chain is the current one

Not the highest `position`: an author who back-fills old files gets them appended at the end. And
not `is_primary`, despite the name.

| Chain | What the obvious field says | What is actually current |
| --- | --- | --- |
| 7237540 | Highest position is an archived *"v1.04 do not download"* at `4.0` | **v1.07**, main, at `3.77` |
| 2764699 | `is_primary` is set on v2.0.17, archived, at `31.0` | **v2.0.21**, main, at `30.9` |

`category == "main"` is the field that holds up, so that decides it. Getting this wrong cost twelve
false updates before it was found.

The comparison that follows needs no version parsing at all: your file is an update if Nexus retired
it, or if the chain's current file sits at a higher position. That fixes a class of bug that version
strings cannot. *MovementAndCameraTweaks* went `v1.41` to `v1.5`, which every semantic comparison
reads as a downgrade because 41 > 5, while the author meant a decimal.

### When an update chain dead-ends

Nexus keys a chain on the file's **name**, so an author who names every upload after its version
gets a fresh one-entry chain per release. On a real 1071-mod profile, 309 mods sit in a chain with
exactly one version in it.

That is fine until such a file is retired. The chain then holds only the file you already have,
marked `old_version`, and cannot say what replaced it. Reported as *Up to date*, which is the one
answer that is certainly wrong: Nexus has said outright that the file is superseded.

The page's own version settles it, and nothing else does. Two real cases, indistinguishable from
inside the chain:

| Installed | Retired | Page version | Verdict |
| --- | --- | --- | --- |
| Native Interactions Framework 1.1.0 | yes | **1.1.1** | A real update. The author renamed the file, starting a new chain |
| Praetor Suit Flashlight Fix 1.0 | yes | **1.0** | Not an update. The page's only live download is an unrelated opaque-visor patch |

So a dead-ended file costs two extra requests for its page, which was 3 mods out of 1644 across
three real profiles. If the page moved past your version, it is a real update and the file is
resolved by version match, never by name. If it did not but something live is still offered, the row
lands in **Superseded on Nexus, your call** with the candidate named:

> Nexus retired this file. The page now leads with "Praetor Suit Opaque Visor" (1.0), check whether
> that replaces it.

Praetor Suit looks like a clean decline from the numbers and is not: the author renamed the whole
page, and the opaque-visor patch really is the continuation. Another page in that exact state would
be offering something unrelated, and nothing in the API separates the two. **Select all** skips the
whole group for that reason. It is a row-by-row decision, never a sweep.

### When MO2 never recorded a file id

MO2 only started writing `[installedFiles]` at some point, so a few percent of any real profile has
nothing exact to resolve: 26 of 543 mods in a real Starfield profile. Those fall back to matching
the page's chain names against the archive name, then the MO2 mod name, longest match first.

The *page* name is deliberately not used. It describes the page rather than any one download and
usually reads like the longest chain on it, which made it actively wrong: page 9643 offers
`LaserSightDots_Enabled` and `LaserSightDots_Enabled_BulletFollowsDot`, and matching on the page
name picked the latter for a mod installed from the former.

Failing all of that the mod lands in **Not checked** and says so. Reinstalling it from Nexus fixes
it permanently, because MO2 writes the file id.

## How it authenticates

MO2 stores your Nexus credentials in the Windows Credential Manager, and the plugin reads them, so
there is nothing to set up:

| Credential | Used as |
| --- | --- |
| `ModOrganizer2_NEXUS_OAUTH_TOKENS` | `Authorization: Bearer …` (MO2's OAuth login) |
| `ModOrganizer2_APIKEY` | `apikey: …` (MO2's legacy key, if present) |

If neither is readable or the OAuth token has expired, it falls back to a personal API key you can
paste into the settings, from
[nexusmods.com/users/myaccount?tab=api](https://www.nexusmods.com/users/myaccount?tab=api).

Nothing is written to the credential store, and the token is never logged or displayed.

## Settings

| Setting | Default | What it does |
| --- | --- | --- |
| `api_key` | *(empty)* | Fallback personal API key, used only if MO2's own login cannot be read |
| `file_categories` | `MAIN,UPDATE,OPTIONAL,MISCELLANEOUS` | Categories to list in the Files tab. `OLD_VERSION` and `ARCHIVED` are hidden by default, since a long-running mod can carry sixty superseded uploads |
| `recheck_days` | `30` | Re-verify cached results older than this during a quick scan, so delistings surface |
| `write_back_versions` | `true` | Record the newest version on the MO2 mod so the main modlist shows its update flag |
| `check_disabled_mods` | `true` | Check mods disabled in the current profile |
| `scan_on_open` | `true` | Scan as soon as the window opens |
| `hide_downloads_after_install` | `auto` | Hide a download once this window installs it. `auto` follows MO2's own setting |
| `show_up_to_date` | `true` | List mods that are up to date |
| `show_ignored` | `true` | List mods whose update you dismissed with MO2's *Ignore update* |

The window's size and position are remembered separately, under `[PluginPersistance]` in
`ModOrganizer.ini`, and are not settings you need to touch. It opens at a share of your screen the
first time, and at whatever you left it thereafter.

## Notes and limits

- **Mods with no Nexus id are skipped**: separators, hand-built mods, xEdit output, Creation Club
  content. The count is shown after each scan.
- **Version comparison is best-effort.** Nexus version strings are free text and some authors use
  dates or build numbers. When two versions cannot be parsed, a difference is reported as an update
  rather than silently ignored.
- **Two MO2 mods from the same Nexus page** each get their own row and their own comparison, and the
  page is queried once.
- **The Files tab is not cached.** File size, description and per-file changelog have no v3
  equivalent, so opening a mod costs two requests. Selecting a row waits for the selection to settle
  before spending them, so arrowing through the list is free. Classification never touches them.
- **Installing re-scans immediately, and MO2's `meta.ini` has not caught up.** `[installedFiles]` is
  set in memory when MO2 installs a mod and flushed to disk a moment later, while the plugin reads
  that file directly. The rescan loses the race, so the window remembers the file id it asked MO2 to
  install and uses it until the disk copy agrees.
- **The cache is shared** across MO2 instances on the same install, at
  `plugins/data/bulk_update_manager_cache.json`. Delete it to force a clean baseline.
- **Rate limits** are read from Nexus' own `x-rl-*` headers, including the limit itself, and shown
  in the window. The scan stops early rather than running you out of requests.
- **Download progress comes from MO2, not from polling.** `IDownloadManager` exposes
  `onDownloadComplete`, `onDownloadFailed` and `onDownloadPaused`, and the id returned by
  `startDownloadNexusFileForGame` is the one those report. MO2 returns `0` when it declines to queue
  at all, such as a collection link or a file for another game (`downloadmanager.cpp:745`).
- **"Hide downloads after installation" is honoured.** MO2 applies that setting itself only on its
  own Downloads-tab install path (`organizercore.cpp:911`), so when the setting is on the plugin
  writes the same `removed=true` flag MO2 writes (`downloadmanager.cpp:910`), touching that one line
  and nothing else.

## Development

Three harnesses run outside MO2 and shorten the restart loop:

```bash
python tools/umd_debug.py validate   # drives the live Nexus API, stdlib only
python tools/test_overrides.py       # the window's pure logic, via a Qt stub
python tools/test_structure.py       # every self._method() call resolves
```

Each is blind to something, and `test_structure.py` exists because of a specific failure that
shipped green to three instances. Anything that paints, or that actually calls MO2, can still only
be checked by installing into an instance and restarting it.

## Licence

MIT. See [LICENSE](LICENSE).
