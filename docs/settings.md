# Settings and limits

Settings live in MO2 under **Settings > Plugins > MO2 Bulk Update Manager**.

| Setting | Default | What it does |
| --- | --- | --- |
| `api_key` | *(empty)* | Fallback personal API key, used only if MO2's own login cannot be read |
| `file_categories` | `MAIN,UPDATE,OPTIONAL,MISCELLANEOUS` | Categories to list in the Files tab. `OLD_VERSION` and `ARCHIVED` are hidden by default, since a long-running mod can carry sixty superseded uploads. The file you have and the one queued for download are always shown, whatever category they sit in |
| `recheck_days` | `30` | Re-verify cached results older than this during a quick scan, so delistings surface over time |
| `write_back_versions` | `true` | Record the newest version on the MO2 mod so the main modlist shows its update flag |
| `check_disabled_mods` | `true` | Check mods disabled in the current profile. Turning this off shortens both the scan and the list |
| `scan_on_open` | `true` | Scan as soon as the window opens. Turn it off to open without spending API requests, then press **Rescan** |
| `hide_downloads_after_install` | `auto` | Hide a download once this window installs it. `auto` follows MO2's own setting; `always` and `never` override it |
| `show_up_to_date` | `true` | List mods that are up to date |
| `show_ignored` | `true` | List mods whose update you dismissed with MO2's *Ignore update* |

The window's size and position are **not** settings. They are remembered under
`[PluginPersistance]` in `ModOrganizer.ini`. The window opens at a share of your screen's usable
area the first time, floored so it never comes up too small to show its columns, and at whatever you
left it thereafter.

## How it reads your Nexus login

MO2 stores your credentials in the Windows Credential Manager, and the plugin reads them, so there
is nothing to configure:

| Credential | Used as |
| --- | --- |
| `ModOrganizer2_NEXUS_OAUTH_TOKENS` | `Authorization: Bearer ...` (MO2's OAuth login) |
| `ModOrganizer2_APIKEY` | `apikey: ...` (MO2's legacy key, if present) |

If neither is readable or the OAuth token has expired, it falls back to a personal API key you can
paste into `api_key`, from
[nexusmods.com/users/myaccount?tab=api](https://www.nexusmods.com/users/myaccount?tab=api).

**Nothing is written to the credential store, and the token is never logged or displayed.**

## Limits

- **Mods with no Nexus id are skipped**: separators, hand-built mods, xEdit output, Creation Club
  content. The count is reported after each scan.
- **Version comparison is best-effort.** Nexus version strings are free text and some authors use
  dates or build numbers. When two versions cannot be parsed, a difference is reported as an update
  rather than silently ignored.
- **Two MO2 mods from the same Nexus page** each get their own row and their own comparison, and the
  page is queried once.
- **The Files tab is not cached.** File size, description and per-file changelog have no v3
  equivalent, so opening a mod costs two v1 requests. Selecting a row waits for the selection to
  settle before spending them, so arrowing through the list is free. Classification never touches
  them.
- **Installing re-scans immediately, and MO2's `meta.ini` has not caught up.** `[installedFiles]` is
  set in memory when MO2 installs a mod and flushed to disk a moment later, while the plugin reads
  that file directly, because there is no accessor for it. The rescan loses the race. On a page
  hosting a single download it resolves anyway; on a page hosting two, such as *Cyberpunk Ultra
  Plus* and *Ultra Skin* on page 10490, it landed in **Not checked** until the next scan. No timing
  fixes that, so the window remembers the file id it asked MO2 to install and uses it until the disk
  copy agrees.
- **The cache is shared** across MO2 instances on the same install, at
  `plugins/data/bulk_update_manager_cache.json`. Delete it to force a clean baseline.
- **Rate limits** are read from Nexus' own `x-rl-*` headers, including the limit itself rather than
  an assumed tier, and shown in the window. The scan stops early rather than running you out of
  requests MO2 itself needs. See [Free accounts](free-accounts.md) for what the allowance actually
  is.
- **Download progress comes from MO2, not from polling.** `IDownloadManager` exposes
  `onDownloadComplete`, `onDownloadFailed` and `onDownloadPaused`, and the id returned by
  `startDownloadNexusFileForGame` is the one those report. MO2 returns `0` when it declines to queue
  at all, such as a collection link or a file for another game (`downloadmanager.cpp:745`). Those
  handlers cannot be unregistered, so they check that the window is still open before touching
  anything.
- **"Hide downloads after installation" is honoured.** MO2 applies that setting itself only on its
  own Downloads-tab install path (`organizercore.cpp:911`); the archive install that plugins get
  marks the download installed but never hides it. So when the setting is on, the plugin writes the
  same `removed=true` flag MO2 writes (`downloadmanager.cpp:910`), touching that one line and
  nothing else in the meta file.
- **The Nexus v3 endpoints this relies on are badged Experimental.** If the plugin breaks with no
  code change on your side, that is the first place to look, not the last.
