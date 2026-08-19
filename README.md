# MO2 Bulk Update Manager

Checks every Nexus-backed mod in your MO2 profile in one pass. Tells you which have updates, which
have been **hidden or removed from Nexus**, shows the changelog and file list side by side, and
sends downloads straight to MO2's Downloads tab.

> **Alpha**, and built with an LLM (Claude). Run by one person, on three MO2 instances, on Cyberpunk
> 2077 and Starfield only. The code has been reviewed and the Nexus-facing half tested against the
> live API.

## Why

MO2 can tell you a mod has an update, but on a large modlist you have to force the check, wait, and
then still open the Nexus page in a browser to get the file. Nothing tells you when a mod you rely
on has quietly been pulled from Nexus.

- **Batched, not one request per mod.** A routine check on a 1071-mod, 908-page profile costs about
  13 Nexus requests.
- **Compares update chains, not page versions**, so it catches updates MO2's own check misses.
- **Flags delisted mods**, which MO2 does not report at all.
- **Knows what you already downloaded**, so nothing is fetched twice.
- **Notes and ignore overrides**, because "why did I skip this one?" is the question you cannot
  answer six months later.

## Install

Copy the `mo2_bulk_update_manager/` folder into your MO2 `plugins/` directory, so you have:

```text
<MO2 install>\plugins\mo2_bulk_update_manager\
```

Restart MO2. It appears under **Tools > MO2 Bulk Update Manager**.

**Requires** MO2 **2.5.3** or later, a Nexus account signed in to MO2, and Windows (credentials come
from the Windows Credential Manager). Nothing else to set up.

> **Upgrading from a folder called `mo2_update_manager`?** Delete it, or MO2 loads the plugin twice.
> See [Installing and upgrading](docs/install.md).

## Using it

1. Open the tool. A quick scan starts automatically.
2. Rows are grouped by outcome. Type in the **Filter** box to narrow a thousand mods to one.
3. Click a mod to read its changelog and file list.
4. Tick what you want, then **Download selected** or **Install selected**.
5. Rows follow their downloads on their own, with no rescan.

**Right-click a row** to act on that one mod without ticking anything: download it, install it, open
its Nexus page, write a note, or deal with MO2's ignore flag. **Right-click in the Files tab** to
download that exact file. Anything already sitting in your downloads folder is offered for install
rather than fetched a second time.

| Group | What it means |
| --- | --- |
| **Updates available** | A newer file exists on Nexus and you do not have it |
| **Downloaded, waiting to be installed** | The newer archive is already in your downloads folder |
| **Downloading** | Queued with MO2. The row moves on by itself |
| **Superseded on Nexus, your call** | Nexus retired your file and no successor can be determined. [Why](docs/how-updates-are-detected.md#when-an-update-chain-dead-ends) |
| **No longer on Nexus** | The page 404s or reports a removed status |
| **Hidden or unavailable** | The page exists but is hidden or under moderation |
| **Ignored in MO2** | You used MO2's *Ignore update* on exactly this version |
| **Could not be checked** | The request failed. The reason is in the Notes column |
| **Not checked** | No result and no cached record. [Why](docs/how-updates-are-detected.md#when-mo2-never-recorded-a-file-id) |
| **Up to date** | Nothing newer in your update chain |

## Documentation

| | |
| --- | --- |
| [Installing and upgrading](docs/install.md) | Requirements, the old folder name, how it reads your Nexus login |
| [Free accounts](docs/free-accounts.md) | What changes without Nexus Premium, and why. **Untested, read before relying on it** |
| [How updates are detected](docs/how-updates-are-detected.md) | Update chains, multi-file pages, dead ends, and what the plugin does when MO2 has no file id |
| [Ignore overrides and notes](docs/ignore-and-notes.md) | Overruling MO2's *Ignore update*, and recording why you left a mod alone |
| [Settings and limits](docs/settings.md) | Every setting, and the things this deliberately does not do |
| [Development](docs/development.md) | The three offline harnesses and what each cannot see |

## Is this built for you?

It was written to fit **one person's habits**, and those are baked into the defaults. It assumes you
keep a large modlist, that MO2 has the file id for most of your mods, that you want to review a
batch of updates and queue them together, and that your archives live in MO2's own downloads folder.
If you install by hand from files kept elsewhere, or update one mod at a time as you notice it, most
of this will not help you.

**It has never been tested without a Nexus Premium account.** See [Free accounts](docs/free-accounts.md).

## Licence

MIT. See [LICENSE](LICENSE).
