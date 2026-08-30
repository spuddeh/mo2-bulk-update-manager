# MO2 Bulk Update Manager

**On Nexus: [nexusmods.com/site/mods/2231](https://www.nexusmods.com/site/mods/2231)**

Checks every Nexus mod in your MO2 profile in one pass. Shows you which ones have updates, which
ones have been **hidden or removed from Nexus**, and sends the downloads straight to MO2.

You can install them from the tool as well. Tick the mods you want and hit **Install selected**, and
it downloads and installs them without you touching the Downloads tab.

## Why

MO2 can tell you a mod has an update, but most of the time you have to force the check, wait for it,
then open the Nexus page in a browser to get the file. And nothing makes it clear when a mod has
been pulled from Nexus.

- **It checks in batches.** A scan of a 1071 mod profile costs about 13 Nexus requests.
- **It follows update chains** instead of comparing version numbers, so it finds updates MO2 misses.
- **It flags mods that are gone** from Nexus, and tells a removed page from a hidden one.
- **It knows what you have already downloaded**, so nothing is fetched twice.
- **It keeps your notes**, so you can record why you skipped a mod.

## Install

Copy the `mo2_bulk_update_manager/` folder into your MO2 `plugins/` directory, so you have:

```text
<MO2 install>\plugins\mo2_bulk_update_manager\
```

Restart MO2. It appears under **Tools > MO2 Bulk Update Manager**.

It was built against MO2 **2.5.3beta12**, which is not a public release. You get it from the
**dev-builds** channel on the [Mod Organizer 2 Discord](https://discord.gg/ewUVAqyrQX). It may well
run on **2.5.2**, the current release, but nobody has tried it.

You also need a Nexus account already signed in to MO2, and Windows, because the credentials come
from the Windows Credential Manager. There is no API key to paste in and nothing you have to set up.

To update the plugin, delete the old folder and drop the new one in its place. See
[Installing and upgrading](docs/install.md).

## Using it

1. Open the tool. It starts scanning on its own.
2. Rows are grouped by result. Use the **Filter** box to find one mod in a thousand.
3. Click a mod to read its changelog and file list.
4. Tick what you want, then hit **Download selected** or **Install selected**.
5. Rows update themselves as the downloads finish. No rescan needed.

**Right-click a row** to handle one mod on its own: download it, install it, open its Nexus page,
leave a note, or change MO2's ignore flag. **Right-click in the Files tab** to do the same for one
exact file.

The menu tells you what it is going to do. If you already have the archive it offers **Install
`<file>` from disk**, and the download becomes **Re-download**. That works even on archives MO2 hid
after installing them, so you never have to go digging in the Downloads tab.

## The first scan is the slow one

Nexus has no batch endpoint for update chains, so the first scan has to ask about every mod one at a
time. On a 900 page profile that is roughly 900 API requests, and it will take a while. After that
it is cached, and a normal scan costs about 13 requests.

Nexus allows Premium accounts 2000 requests an hour, and free accounts 100. On a free account the
first scan will hit that limit and you will need to pick it up again over a few hours. Progress is
saved as it goes, so nothing is ever fetched twice. The tool shows how much of your allowance is
left and stops before it runs you out.

## The result groups

| Group | What it means |
| --- | --- |
| **Updates available** | A newer file is on Nexus and you do not have it |
| **Downloaded, waiting to be installed** | The newer archive is already in your downloads folder |
| **Downloading** | Queued with MO2. The row moves on by itself |
| **Superseded on Nexus, your call** | Nexus retired your file and there is no clear replacement. [Why](docs/how-updates-are-detected.md#when-an-update-chain-dead-ends) |
| **No longer on Nexus** | The page is gone |
| **Hidden or unavailable** | The page is there but hidden or under moderation |
| **Ignored in MO2** | You used MO2's *Ignore update* on this exact version |
| **Could not be checked** | The request failed. The reason is in the Notes column |
| **Not checked** | No result and nothing cached. [Why](docs/how-updates-are-detected.md#when-mo2-never-recorded-a-file-id) |
| **Up to date** | Nothing newer in the chain |

## Settings

It works out of the box. If you want to change how it behaves, the settings are in MO2 under
**Settings > Plugins > MO2 Bulk Update Manager**. Every one of them is listed in
[Settings and limits](docs/settings.md).

## Free Nexus accounts

Nexus only hands out direct download links to Premium accounts, so on a free account the downloads
will not queue. Everything else should still work: the scan, the changelogs, the file lists, the
delisting checks, the notes, and installing archives you already have.

**Nobody has run it on a free account yet**, so that is expectation, not experience. See
[Free accounts](docs/free-accounts.md).

## It is built around how I mod

I wrote this for the way I install mods. It assumes you keep a big modlist, that you like to look
over a batch of updates and queue them together, and that your archives live in MO2's downloads
folder. If you install by hand from files kept somewhere else, or grab updates one at a time as you
notice them, it will not do much for you.

That is a starting point, not a rule. **If it does not fit how you work, open an issue.** I am happy
to take suggestions and I would rather hear about a habit I did not think of.

## Documentation

| | |
| --- | --- |
| [Installing and upgrading](docs/install.md) | Requirements, the old folder name, how it reads your Nexus login |
| [How updates are detected](docs/how-updates-are-detected.md) | Update chains, pages with several files, dead ends, and what it does when MO2 has no file id |
| [Settings and limits](docs/settings.md) | Every setting, and the things it deliberately does not do |
| [Ignore overrides and notes](docs/ignore-and-notes.md) | Overruling MO2's *Ignore update*, and recording why you left a mod alone |
| [Free accounts](docs/free-accounts.md) | What changes without Premium, and why. **Untested, read before relying on it** |
| [Releasing](RELEASING.md) | How a release reaches GitHub and Nexus |
| [Development](docs/development.md) | The three offline harnesses and what each cannot see |

## How it was built

This plugin was written with the help of an AI coding assistant. The code has been reviewed and the
Nexus side of it has been tested against the live API, on three MO2 instances across two games. You
should know that before you install it, and decide for yourself.

## Licence

MIT. See [LICENSE](LICENSE).
