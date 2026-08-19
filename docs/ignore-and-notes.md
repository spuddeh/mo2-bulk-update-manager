# Ignore overrides and notes

## Overruling MO2's ignore flag

MO2's *Ignore update* dismisses **one specific version**, and MO2's own interface gives you no way to
take it back once that version has scrolled out of view. Right-clicking a row here gives two ways
out, and they are not the same thing.

Before either, consider **Add a note**. An ignore you can explain in six months is worth more than
one you can undo.

### "Download `<version>` anyway"

Offers the update in this window and changes nothing in MO2. The mod moves out of *Ignored in MO2*
into *Updates available*, or straight into the install queue if the archive is already in your
downloads folder, with a note saying MO2 still ignores it.

**This is the one to use.** It is recorded as a plugin setting on the mod, which MO2 keeps in that
mod's own `meta.ini` and writes itself, so it survives a restart and nothing goes behind MO2's back.
It is scoped to the version it was granted for, exactly as MO2 scopes the dismissal it overrides: if
the author later ships something newer, that is a new decision. Right-click again to hand the
decision back to MO2.

### "Clear MO2's ignore flag"

Genuinely un-ignores the mod, for MO2 as well as for this window, with a caveat the confirmation
dialog states outright.

`IModInterface` exposes `ignoredVersion()` and no setter; the Python bindings stop at the getter
(`basic_classes.cpp:253`). So the flag can only be reached through the mod's `meta.ini`, which this
plugin already reads for its installed file ids. It rewrites that one line and leaves the rest of
the file byte for byte alone, for the same reason `downloads.hide` does: a mod's meta carries the
entire Nexus description as one escaped value, and round-tripping that through an INI parser is a
large risk for no gain.

**The caveat: MO2 holds its own copy.** `ModInfoRegular` reads `meta.ini` once and writes it back
from memory whenever the mod is marked changed, including at shutdown (`modinforegular.cpp:68`). So
MO2's own modlist keeps showing the mod as ignored until you restart it, however the file reads.

Two things keep MO2 from simply undoing the write:

1. **The mod is flushed first.** Storing a plugin setting on a mod makes MO2 write the whole
   `meta.ini` from memory and then clear that mod's changed flag (`modinforegular.cpp:1009`). Doing
   that immediately *before* the edit leaves the mod unchanged as far as MO2 is concerned, so
   nothing is written over the top of it at shutdown.
2. **This window stops dirtying it.** Writing newer versions back to MO2 is skipped for anything
   un-ignored this session, because `setNewestVersion` marks the mod changed and would hand the
   stale flag straight back.

Anything else that edits that mod in the same session, such as renaming it, recategorising it, or
MO2's own Nexus check, will still restore the flag. That is the honest limit of a setter MO2 does
not expose.

### A flag naming an older version is not hiding anything

Because MO2 dismisses one specific version, a flag left over from an earlier release stops applying
the moment the page moves past it, and the mod correctly appears under *Updates available*.

*Cyberpunk Ultra Plus* is a real example: `ignoredVersion=6.2.2.0` against a page on 9.1.5.0. The
update is not being suppressed and never was.

The menu says which of the two you are looking at rather than using the same wording for both:

| State | Menu item |
| --- | --- |
| Flag names an older version, update is showing | *Clear MO2's stale ignore flag (6.2.2.0)* |
| Flag names the current release, update is hidden | *Clear MO2's ignore flag (9.1.5.0)* |

A flag you have overridden with *Download ... anyway* counts as active, not stale, because
*Respect MO2's ignore flag again* would bring it straight back.

## Notes: why you left it that way

An ignored update is a decision, and the reason for it lives in your head for about a week. *Winds
of Cydonia* is a real example: the author's 2.0 requires a second mod the user does not want, the old
version still works, and the changelog says as much. Six months later all that survives is a mod
sitting in a collapsed group with no explanation, and the obvious move, taking the update, is the
wrong one.

Right-click any mod and choose **Add a note**. What you type comes back in three places:

- **On the row**, in the Notes column after whatever the scan found, marked `✎` so it is never
  confused with something the plugin said. Long notes are shortened to keep the column from
  stretching across a thousand rows; the whole thing is on the row's tooltip and in the **Details**
  tab.
- **In the filter.** Searching `cet` finds every mod you noted as needing Cyber Engine Tweaks,
  whatever they are called.
- **In the confirmation dialog**, indented under the mod, when you tick it for download or install.

The last one is where it earns its keep. MO2's ignore flag covers one version, so the day the author
ships 2.1 the mod reappears under *Updates available* like any other, and the last screen before it
downloads is the one that reminds you why you said no.

A note records the latest version at the time you wrote it. Once the mod moves past that, the note
shows as **✎ on 2.0** rather than plain `✎`, because *"needs another mod I don't want"* was a
statement about 2.0 and says nothing about the 2.1 that replaced it. Re-saving the note re-dates it.

Notes are stored as plugin settings on the mod, which MO2 keeps in that mod's own `meta.ini` under
`[Plugins]` and writes itself. Same mechanism as the ignore override, and for the same reason. MO2's
own *Notes* field is not used, because the bindings expose `notes()` with no setter.

If you go looking for one in a `meta.ini`, MO2 percent-escapes the plugin name:

```ini
[Plugins]
MO2%20Bulk%20Update%20Manager\note=Staying on v1.0 as it does not require Heart of Cydonia
MO2%20Bulk%20Update%20Manager\note_version=1.3
```

## Finding one mod in a thousand

The **Filter** box takes words, not a pattern. Every word has to appear somewhere in the row: the
mod name, the chain name, the Nexus page name, or your note. So `cet frame` finds *CET Frame
Generation* whichever order you remember the words in.

Versions are deliberately not searched. Typing `1.2` to find a mod would otherwise match every row
that happens to sit on 1.2.

While a filter is active:

- Group headings count what is showing: **Up to date (3 of 1067)**. A group with no matches
  disappears rather than sitting there empty.
- Groups that normally start collapsed open up, because a match hidden inside *Up to date* is not a
  match you can see.
- **Select all** only reaches rows on screen. Ticking a thousand invisible mods is never what anyone
  meant by it.
- **Ticks you have already made survive.** A row you ticked and then filtered out still downloads,
  and still appears by name in the confirmation dialog. Filtering is a way to look at the list, not
  a way to change what you asked for.
