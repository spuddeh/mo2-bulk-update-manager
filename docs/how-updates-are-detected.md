# How updates are detected

The short version: **a Nexus page is not one download**, so this plugin never compares your mod
against the page version. It pins each MO2 mod to the specific upload sequence it came from and
compares only against that.

## Multi-file mod pages

A page can host several unrelated files, each with its own version history, and plenty of authors
never bump the *page* version when they update one of them. Both failures are common:

| Page | Page version | What is really there |
| --- | --- | --- |
| Disable Fake Lights with Path Tracing (16060) | `0.4` | Main file is at **v0.5**. A page-level check says "up to date" |
| Window Utils (26589) | `1.0.3` | Two downloads: *Window Utils* v1.0.0 to v1.0.3, and *Window Utils Showcase* v1.0.0b to v1.0.1b. Both need updates, both share one page |

Nexus' v3 API models a page as a set of **update chains**, one per download, each an ordered list of
that download's uploads. That is what the plugin compares against, which is why both Window Utils
mods get their own row and their own answer.

## What a scan actually asks for

Four kinds of call, none of them per-mod in the normal case:

1. **Status for every page**, batched up to 2000 at a time. This is what flags a page as removed,
   hidden or under moderation.
2. **Installed file ids resolved to their chain**, batched. The v3 `game_scoped_id` *is* the legacy
   file id, which is what MO2 records in a mod's `meta.ini` under `[installedFiles]`, so this is
   exact whenever it is present.
3. **A page's chains**, only for mods where step 2 had nothing to resolve.
4. **Each chain's versions**, cached on disk and re-fetched only when Nexus reports the mod as
   changed or the cached copy is older than `recheck_days`.

**Rescan** asks Nexus what changed in each game since your last scan and checks only those mods,
plus a rotating slice of the oldest cached results so delistings still surface. **Deep scan** checks
every mod individually: slower and far more requests, but the only way to catch a mod pulled from
Nexus long before your last scan.

## Which upload in a chain is the current one

Not the highest `position`. Position records where an upload sits in the chain, and an author who
back-fills old files gets them appended at the end. And not `is_primary`, despite the name.

| Chain | What the obvious field says | What is actually current |
| --- | --- | --- |
| 7237540 | Highest position is an archived *"v1.04 do not download"* at `4.0` | **v1.07**, main, at `3.77` |
| 2764699 | `is_primary` is set on v2.0.17, archived, at `31.0` | **v2.0.21**, main, at `30.9` |

`category == "main"` is the field that holds up, so that decides it, falling back to the newest
upload Nexus has not retired and only then to `is_primary`. Getting this wrong cost twelve false
updates before it was found.

The comparison that follows needs **no version parsing at all**. Your file is an update if Nexus
retired it, or if the chain's current file sits at a higher position. That removes a class of bug
version strings cannot avoid: *MovementAndCameraTweaks* went `v1.41` to `v1.5`, which every semantic
comparison reads as a downgrade because 41 > 5, while the author meant a decimal.

A different upload that Nexus has neither retired nor promoted is **not** an update. It is another
current file on the same chain, which is what `2.1` and `2.1-alternate` are to each other.

## When an update chain dead-ends

Nexus keys a chain on the file's **name**, so an author who names every upload after its version
gets a fresh one-entry chain per release rather than one chain with a history. On a real 1071-mod
profile, **309 mods sit in a chain with exactly one version in it**.

That is harmless until such a file is retired. The chain then holds only the file you already have,
marked `old_version`, and cannot say what replaced it: Nexus recorded the successor as a separate
chain, and `file_updates` on the v1 endpoint does not link them either. Reporting *Up to date* there
is the one answer that is certainly wrong, because Nexus has said outright that the file is
superseded.

The page's own version settles it, and nothing else does. Two real cases that are indistinguishable
from inside the chain:

| Installed | Retired | Page version | Verdict |
| --- | --- | --- | --- |
| Native Interactions Framework 1.1.0 | yes | **1.1.1** | A real update. The author renamed the file, which started a new chain |
| Praetor Suit Flashlight Fix 1.0 | yes | **1.0** | Not an update. The page's only live download is an unrelated opaque-visor patch |

So a dead-ended file costs two extra v1 requests for its page, which was 3 mods out of 1644 across
three real profiles rather than the per-page cost this plugin exists to avoid.

**If the page moved past your version**, it is a real update, and the file is resolved by version
match, never by name.

**If it did not, but something live is still offered**, the row lands in *Superseded on Nexus, your
call* with the candidate named:

> Nexus retired this file. The page now leads with "Praetor Suit Opaque Visor" (1.0), check whether
> that replaces it.

Praetor Suit looks like a clean decline from the numbers and is not: the author renamed the whole
page, dropped every mention of the flashlight fix, and the opaque-visor patch really is its
continuation. Another page in exactly that state would be offering something unrelated, and nothing
in the API separates the two. `is_active`, version numbers and file names all say the same thing in
both cases.

Ticking the row downloads the named file, and the confirmation dialog names it once more first.
**Select all deliberately skips the whole group**, because the reason the group exists is that only
a person can judge it.

The version comparison here is strict on purpose. Both sides must be plain dotted numbers.
`1.0.0joker`, `1.0.1b`, a date or a build string means the author numbers that file on its own
scheme, and comparing it against the page version manufactures a result. `mobase.VersionInfo` is a
prefix match (`versioninfo.cpp:27`) and would happily read `1.0.0joker` as `1.0.0`. That leniency is
right for "is this file newer?" and wrong here.

## When MO2 never recorded a file id

MO2 only started writing `[installedFiles]` at some point, so a few percent of any real profile has
nothing exact to resolve: **26 of 543 mods** in a real Starfield profile. Those fall back to matching
the page's chain names against the installation archive name, then against the MO2 mod name, longest
match first.

The *page* name is deliberately not used. It describes the page rather than any one download, and it
usually reads like the longest chain on it, which made it actively wrong. Page 9643 offers
`LaserSightDots_Enabled` and `LaserSightDots_Enabled_BulletFollowsDot`, and matching on the page
name picked the latter for a mod installed from the former.

A chain chosen this way is cached against the evidence that produced it rather than against the
page, because several MO2 mods can share one page and land on different chains. Keying by page let
whichever was seen last overwrite the other.

Failing all of that, the mod lands in **Not checked** and says so. Reinstalling it from a fresh
Nexus download fixes it permanently, because MO2 writes the file id.

Two Starfield mods in a real profile cannot be placed and it is correct that they cannot.
*SFHotkeys* is installed from an archive matching none of its page's chain names. Guessing would
offer the wrong download, so it does not guess.

## Archives you already have

MO2's downloads folder is indexed by `(mod id, file id)`, which is exactly what a Nexus file record
carries, so the plugin always knows whether the file it is about to fetch is already on disk.

**The right-click menu says so before you click.** With an archive present it offers *Install
`<file>` from disk* and renames the download to *Re-download*; with none it simply says *Download*.
Ticking rows and pressing **Download selected** asks the same question once for the batch.

The test is **whether an archive exists on disk**, not whether MO2 still considers it pending. Those
differ in the case that matters most. MO2 marks a download installed, and if you asked it to, hides
it from the Downloads tab; the archive is still there. Gating on MO2's pending flag would mean
finding the mod, being told the file is already downloaded, closing the window, opening the Downloads
tab, unhiding the archive and finding it again. `installMod` takes a path, so none of that is
necessary.

Only an interrupted download is excluded, because there is no whole archive behind it.

Installing this way runs MO2's normal installer, so any FOMOD still asks its usual questions, and
the window remembers the file id it asked MO2 to install so the rescan afterwards does not lose the
race against MO2's own `meta.ini` write.

## What the columns show

The **File** column shows the chain's name whenever it differs from the MO2 mod name. In the Files
tab, `•` marks the file you currently have and `✓` marks the one that will be downloaded.

Columns size to their contents rather than stretching to fill, so long mod names are never elided;
the list scrolls sideways instead. They refit when a collapsed group opens, when the filter changes
what is on screen, and after every rebuild.

Category colours are solved rather than fixed. MO2 applies themes as Qt stylesheets rather than
palettes, so there is no "is this dark?" flag to read, but a widget's effective palette does pick up
the stylesheet once Qt polishes it. The plugin measures the list's real background and moves each
category's lightness until it clears 4.5:1 against it. Hues stay put so a category stays
recognisable.
