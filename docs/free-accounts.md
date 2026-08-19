# Free accounts

**This path has never been run by anyone.** The author holds lifetime Nexus Premium, and so does the
only other person who has used the plugin. Everything below was written by reading MO2's source
rather than by using it. Treat it as design intent that needs a first user. Reports very welcome.

## What still works

The scan, the changelogs, the file lists, the delisting checks, notes, ignore overrides, filtering
and installing archives you already have. None of that touches a download link.

## What cannot work, and why

Nexus issues direct download links to **Premium accounts only**. This is not a rate limit or a
slower queue, it is a hard refusal, and MO2 handles it in a way that is easy to get wrong.

`IDownloadManager.startDownloadNexusFileForGame` looks like it either queues a download or does not.
It does neither. It is the front of an async chain, and the tier check sits at the **back** of it:

```text
startDownloadNexusFileForGame(game, modID, fileID)
  -> startDownloadNexusFile   builds nxm://{game}/mods/{id}/files/{fid}   (downloadmanager.cpp:2068)
  -> addNXMDownload           reserves a DownloadID, appends a PendingDownload,
                              RETURNS IT, then requests file info async    (downloadmanager.cpp:736)
  -> nxmFileInfoAvailable     -> requestDownloadURL(..., key = "")         (downloadmanager.cpp:1968)
  -> NexusInterface           tier check happens HERE                      (nexusinterface.cpp:939)
```

At the last step:

- **Premium** builds `.../download_link` and proceeds.
- **Not Premium, but `nexusKey` and `nexusExpires` are set and the download user matches**, builds
  `.../download_link?key=...&expires=...` and proceeds. Those values only ever come from an
  `nxm://` handoff, which only the website's *Mod Manager Download* button produces.
- **Otherwise** MO2 logs `"Aborting download: Either you clicked on a premium-only link and your
  account is not premium..."` and returns.

That `return` is the whole problem. It happens before any request is issued, so there is no reply,
no failure callback, and nothing removes the pending entry. The download id was handed back to the
plugin several steps earlier and is an ordinary-looking non-zero integer.

**A plugin that trusts it shows a row stuck at *Downloading* forever.** MO2's documented failure
signal is a return of `0`, used when it declines a collection link or a file for another game
(`downloadmanager.cpp:745`). Checking for `0` is correct and does not cover this case at all.

## What this plugin does instead

It reads your account tier from the `validate` response when it starts, and on a free account it
never queues a download. **Download selected** becomes **Open download pages**, and each chosen file
opens in your browser at its own row on the Files tab:

```text
https://www.nexusmods.com/<game>/mods/<id>?tab=files&file_id=<file>
```

Click **Mod Manager Download** there. MO2 catches the `nxm://` link, and this window follows the
download from that point exactly as it would for a Premium account. The right-click menu on a row
and in the Files tab changes the same way.

## The API budget is the real constraint

| | Hourly | Daily |
| --- | --- | --- |
| Free (Nexus documented) | 100 | 2500 |
| Premium (measured 2026-08-20) | 2000 | 20000 |

A routine scan costs about 13 requests, so day-to-day use is comfortable on either.

**The first scan is not.** With an empty cache, every mod needs its update chain fetched: roughly
900 requests on a 900-page profile. There is no batch endpoint that would fix this. `POST
/v3/mod-files/batch` and `POST /v3/mod-files/versions/batch` were both probed on 2026-08-20 and
return 404; only `mods/batch` and `mod-file-versions/batch` exist, and neither carries chain
contents.

So on a free account, expect the first scan to throttle and resume across several hours. After that
the disk cache carries it and quick scans are cheap again. The window shows what is left of your
allowance, reading the limit from Nexus' own `x-rl-*` headers rather than assuming a tier, and stops
early rather than running you out of requests MO2 itself needs.

Two ways to make the first run cheaper:

- Turn off `check_disabled_mods` if your profile carries a lot of disabled mods you have no
  intention of updating.
- Leave `scan_on_open` on and simply reopen the window each hour. Progress is cached as it goes, so
  nothing already fetched is fetched again.
