# Releasing

Publishing a GitHub Release builds the zip, attaches it, and uploads it to
[the Nexus page](https://www.nexusmods.com/site/mods/2231). `.github/workflows/release.yml` does
the work.

**A push cannot cut a release.** The only triggers are `release: published` and a manual
`workflow_dispatch`, so pushing to `main` is always safe.

## One-time setup

| Where | Name | Value |
| --- | --- | --- |
| Settings > Secrets and variables > Actions > **Secrets** | `NEXUSMODS_API_KEY` | A personal API key from [nexusmods.com/users/myaccount?tab=api](https://www.nexusmods.com/users/myaccount?tab=api) |
| Settings > Secrets and variables > Actions > **Variables** | `NEXUS_FILE_ID` | The **Group ID** from the mod page, Files tab > API Info |

The file id is a variable rather than a secret because it is public, it authorises nothing on its
own, and masking it in the log would only make a wrong id harder to diagnose. Do not use the
`file_id` from the v1 API: that is a different id space wearing the same name, and the wrong value
looks entirely plausible.

## Cutting a release

1. Bump `VERSION` in `mo2_bulk_update_manager/_version.py`. The workflow fails if the tag disagrees.
2. Run `python tools/test_structure.py` and `python tools/test_overrides.py`.
3. Commit, then tag `v<version>` and push the tag.
4. Publish a GitHub Release on that tag, with a body in the shape below.

The body feeds two Nexus fields, split by one marker:

```text
A one-line summary of this file, 255 characters at most.
<!-- nexus-description-end -->
One changelog entry per line, no bullet marker.
```

With no marker the whole body becomes the changelog and the file description is left unset.

Both fields are read on the mod page, not in a repo, so keep them shorter than a commit message.
The summary is one sentence naming what changed for a user. Each changelog line is one change,
stated once; the reasoning behind it belongs in the commit.

**Write changelog lines without a leading `-`.** Nexus renders each line as a quote and supplies
its own marker, so a dash arrives as a second one.

**The changelog endpoint appends, it does not replace.** Re-publishing a release fires the event
again and posts the entry a second time; fix that on the page by hand. A `workflow_dispatch` never
posts a changelog, for the same reason.

## What is still manual

- **The mod page description.** It lives in `nexus_description.bbc`, which is gitignored. Nothing
  in the pipeline touches the page description.
- **Screenshots.**
- **The first upload to a new mod page**, because a file id does not exist until a page has one
  file on it. That has been done: 0.15.2, 2026-08-30.
