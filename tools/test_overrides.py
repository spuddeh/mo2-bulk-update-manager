"""Offline checks for the ignore overrides and the per-mod note.

    python tools/test_overrides.py

No MO2, no Qt, no network -- ``qt_stub`` fakes just enough of both to import
the plugin, and says what that is and is not good for. Everything checked here
is a pure function: string formatting, version comparison, and the one place
this plugin writes a file MO2 owns.

Anything that paints, or that actually calls MO2, can only be checked by
installing into an instance and restarting it. Adding a check here first is
still usually faster than a round trip through MO2.
"""

import os
import re
import shutil
import sys
import tempfile

_TOOLS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_TOOLS)
sys.path.insert(0, _TOOLS)

import qt_stub  # noqa: E402

qt_stub.install()

sys.path.insert(0, _ROOT)

from mo2_bulk_update_manager import dialog  # noqa: E402
from mo2_bulk_update_manager.scanner import (  # noqa: E402
    clear_ignored_version,
    is_ignored,
    page_ahead_of,
    read_overrides,
)

FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print(f"  ok    {name}")
    else:
        print(f"  FAIL  {name}   {detail!r}")
        FAILURES.append(name)


class FakeEntry:
    def __init__(
        self,
        ignored="",
        latest="",
        forced="",
        note="",
        note_version="",
        message="",
        name="Winds of Cydonia",
        row_label="",
        nexus_name="",
        status="update",
    ):
        self.status = status
        self.ignored_version = ignored
        self.latest_version = latest
        self.forced_version = forced
        self.note = note
        self.note_version = note_version
        self.message = message
        self.display_name = name
        self.row_label = row_label
        self.nexus_name = nexus_name


# -- clear_ignored_version -------------------------------------------------

REAL = (
    b"[General]\r\n"
    b"gameName=cyberpunk2077\r\n"
    b"modid=8766\r\n"
    b"version=1.0.0.0\r\n"
    b"newestVersion=1.2.1.0\r\n"
    b'category="13,"\r\n'
    b"installationFile=1_Max Muscle - UV-8766-1-0.7z\r\n"
    b"ignoredVersion=1.2.1.0\r\n"
    b'nexusDescription="[font=Verdana]caf\xc3\xa9 \\n<br />[url=x]y[/url]\xef\xbb\xbf"\r\n'
    b"\r\n"
    b"[installedFiles]\r\n"
    b"1\\modid=8766\r\n"
    b"1\\fileid=123456\r\n"
    b"size=2\r\n"
    b"\r\n"
    b"[Plugins]\r\n"
    b"Update Manager\\note=needs CET 2.0\r\n"
)


def write_meta(directory, body):
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, "meta.ini")
    with open(path, "wb") as handle:
        handle.write(body)
    return path


print("clear_ignored_version")
root = tempfile.mkdtemp(prefix="umd-overrides-")
try:
    mod = os.path.join(root, "Some Mod")
    path = write_meta(mod, REAL)
    error = clear_ignored_version(mod)
    with open(path, "rb") as handle:
        after = handle.read()

    check("returns no error", error is None, error or "")
    check(
        "only that one line changed",
        after == REAL.replace(b"ignoredVersion=1.2.1.0\r\n", b"ignoredVersion=\r\n"),
    )
    check("CRLF preserved", after.count(b"\r\n") == REAL.count(b"\r\n"))
    check("no LF-only lines introduced", not re.search(rb"[^\r]\n", after))
    check("description blob intact", b"caf\xc3\xa9" in after and b"\xef\xbb\xbf" in after)
    check("plugin note survives", b"Update Manager\\note=needs CET 2.0" in after)
    check("no temp file left", not os.path.exists(path + ".umd-tmp"))

    again = clear_ignored_version(mod)
    with open(path, "rb") as handle:
        check("idempotent", again is None and handle.read() == after)

    plain = os.path.join(root, "Plain Mod")
    plain_body = b"[General]\r\nmodid=1\r\nversion=1.0\r\n"
    plain_path = write_meta(plain, plain_body)
    check("absent key is not an error", clear_ignored_version(plain) is None)
    with open(plain_path, "rb") as handle:
        check("absent key leaves file untouched", handle.read() == plain_body)

    lf = os.path.join(root, "LF Mod")
    lf_path = write_meta(lf, b"[General]\nignoredVersion=2.0\nmodid=5\n")
    clear_ignored_version(lf)
    with open(lf_path, "rb") as handle:
        check(
            "LF file stays LF",
            handle.read() == b"[General]\nignoredVersion=\nmodid=5\n",
        )

    other = os.path.join(root, "Other Section")
    other_body = b"[General]\r\nmodid=1\r\n\r\n[Plugins]\r\nignoredVersion=9.9\r\n"
    other_path = write_meta(other, other_body)
    clear_ignored_version(other)
    with open(other_path, "rb") as handle:
        check("only [General] is touched", handle.read() == other_body)

    check("missing folder is an error", clear_ignored_version("") is not None)
    check(
        "missing file is an error",
        clear_ignored_version(os.path.join(root, "nope")) is not None,
    )
finally:
    shutil.rmtree(root, ignore_errors=True)


# -- is_ignored ------------------------------------------------------------

print("is_ignored")
check("ignored when versions match", is_ignored(FakeEntry("1.2.1.0", "1.2.1")))
check("not ignored when newer arrived", not is_ignored(FakeEntry("1.2.1", "1.3.0")))
check("not ignored with no flag", not is_ignored(FakeEntry("", "1.3.0")))
check("forced beats the flag", not is_ignored(FakeEntry("1.2.1.0", "1.2.1", "1.2.1")))
check(
    "force is scoped to its own version",
    is_ignored(FakeEntry("1.4.0", "1.4.0", "1.2.1")),
    "a stale force must not swallow a later ignore",
)
check(
    "force tolerates MO2's four-segment padding",
    not is_ignored(FakeEntry("1.2.1.0", "1.2.1", "1.2.1.0")),
)


# -- the note --------------------------------------------------------------

print("_note_age")
check("silent with no note version", dialog._note_age(FakeEntry(latest="2.1")) == "")
check(
    "silent when nothing is known to be newer",
    dialog._note_age(FakeEntry(note_version="2.0")) == "",
)
check(
    "silent while the note still describes the latest",
    dialog._note_age(FakeEntry(latest="2.0", note_version="2.0")) == "",
)
check(
    "silent across MO2's four-segment padding",
    dialog._note_age(FakeEntry(latest="2.0", note_version="2.0.0.0")) == "",
)
check(
    "names the version once it has been overtaken",
    dialog._note_age(FakeEntry(latest="2.1", note_version="2.0")) == " on 2.0",
)

print("_note_label")
check("nothing without a note", dialog._note_label(FakeEntry()) == "")
check(
    "marks the user's own words",
    dialog._note_label(FakeEntry(note="needs CET 2.0")) == "\u270e needs CET 2.0",
)
check(
    "flattens a multi-line note onto the row",
    dialog._note_label(FakeEntry(note="needs CET 2.0\n\n1.9 still works."))
    == "\u270e needs CET 2.0 1.9 still works.",
)
long_note = "word " * 60
labelled = dialog._note_label(FakeEntry(note=long_note))
check(
    "shortens a long note to keep the column narrow",
    len(labelled) <= dialog._NOTE_LIMIT + 2 and labelled.endswith("\u2026"),
    labelled,
)
check(
    "dates a stale note on the row",
    dialog._note_label(FakeEntry(note="needs CET", latest="2.1", note_version="2.0"))
    == "\u270e on 2.0 needs CET",
)

print("_notes_cell")
check("empty for a quiet row", dialog._notes_cell(FakeEntry()) == "")
check(
    "the scan's message alone",
    dialog._notes_cell(FakeEntry(message="Ignored in MO2.")) == "Ignored in MO2.",
)
check(
    "the user's note alone",
    dialog._notes_cell(FakeEntry(note="needs CET")) == "\u270e needs CET",
)
check(
    "the user's words lead, the scan's boilerplate follows",
    dialog._notes_cell(FakeEntry(message="Ignored in MO2.", note="needs CET"))
    == "\u270e needs CET   Ignored in MO2.",
)

print("_note_block")
check("nothing without a note", dialog._note_block(FakeEntry()) == "")
check(
    "indented under its mod",
    dialog._note_block(FakeEntry(note="needs CET 2.0"))
    == "\n      \u270e needs CET 2.0",
)
check(
    "keeps every line, unlike the row",
    dialog._note_block(FakeEntry(note="needs CET 2.0\n1.9 still works."))
    == "\n      \u270e needs CET 2.0\n        1.9 still works.",
)
check(
    "dates a stale note in the dialog too",
    dialog._note_block(FakeEntry(note="needs CET", latest="2.1", note_version="2.0"))
    == "\n      \u270e on 2.0 needs CET",
)

print("_matches")
noted = FakeEntry(name="Winds of Cydonia", note="needs CET 2.0", latest="2.1")
check("finds a mod by its name", dialog._matches(noted, ["cydonia"]))
check("finds a mod by what you wrote about it", dialog._matches(noted, ["cet"]))
check("every word has to match", not dialog._matches(noted, ["cet", "starfield"]))
check("word order does not matter", dialog._matches(noted, ["cet", "winds"]))
check("versions are not searched", not dialog._matches(noted, ["2.1"]))
check("nothing matches a missing row", not dialog._matches(None, ["cet"]))

# -- per-mod settings ------------------------------------------------------


class FakeMod:
    """A mod carrying plugin settings under a given plugin name."""

    def __init__(self, groups, explode=False):
        self.groups = {k: dict(v) for k, v in groups.items()}
        self._explode = explode

    def pluginSettings(self, plugin_name):
        if self._explode:
            raise RuntimeError("MO2 said no")
        return dict(self.groups.get(plugin_name, {}))


NOW = "MO2 Bulk Update Manager"
OTHER = "Update Manager"

print("read_overrides")
check(
    "reads settings under this plugin's own name",
    read_overrides(FakeMod({NOW: {"note": "mine"}}), NOW) == {"note": "mine"},
)
check(
    "never reads a name belonging to some other plugin",
    read_overrides(FakeMod({OTHER: {"note": "theirs"}}), NOW) == {},
    "'Update Manager' is a real MO2 plugin; its per-mod data is not ours to read",
)
check("nothing stored", read_overrides(FakeMod({}), NOW) == {})
check("no plugin name to ask under", read_overrides(FakeMod({OTHER: {"a": "b"}}), "") == {})
check("MO2 refusing is not fatal", read_overrides(FakeMod({}, explode=True), NOW) == {})


# -- retired files whose update chain dead-ends -----------------------------
#
# Nexus keys an update chain on the file's *name*, so an author who names each
# upload after its version gets a fresh one-entry chain per release. When such
# a file is retired, its chain holds nothing newer and cannot say what replaced
# it. The page version is the only thing that can. Both cases below are real.

print("page_ahead_of")
check(
    "the page moved on: Native Interactions 1.1.0 -> page 1.1.1",
    page_ahead_of("1.1.0", "1.1.1"),
    "chain 7817598 dead-ends; the successor is only visible as the page version",
)
check(
    "the page did not move: Praetor Suit Flashlight Fix, retired, page still 1.0",
    not page_ahead_of("1.0", "1.0"),
    "its page's only live download is an unrelated opaque-visor patch",
)
check("MO2's four-segment padding is not an update", not page_ahead_of("1.1.0.0", "1.1"))
check("a shorter page version still compares", page_ahead_of("1.1", "1.1.1"))
check("a lower page version is not an update", not page_ahead_of("2.0", "1.9"))
check(
    "declines when the file numbers itself its own way",
    not page_ahead_of("1.0.0joker", "1.0.1"),
    "comparing unrelated numbering schemes is what made this noisy before",
)
check("declines a dated page version", not page_ahead_of("1.0", "2026-08-17"))
check("declines when either side is empty", not page_ahead_of("", "1.1") and not page_ahead_of("1.0", ""))
check("a leading v is tolerated", page_ahead_of("v1.1.0", "v1.1.1"))


# -- reusing an archive that is already on disk -----------------------------
#
# Picking an older or optional upload in the Files tab and downloading it asks
# for an exact file id the scan never looked up, so `note_download` cannot have
# checked it. MO2's downloads folder is indexed by (mod id, file id), which is
# exactly what the picked file record carries.

print("partition_by_local_copy")

from mo2_bulk_update_manager.downloads import (  # noqa: E402
    DownloadInfo,
    INCOMPLETE,
    INSTALLED,
    READY,
    installable,
)


def local(mod_id, file_id, name="archive.zip", state=READY, hidden=False):
    return DownloadInfo(mod_id, file_id, name, "C:/dl/" + name, "", "", state, hidden)


E = FakeEntry(name="Ultra Plus")
E.mod_id = 10490
FILE = {"file_id": 156665, "name": "Cyberpunk Ultra Plus v9.1.4"}


def split(index):
    return dialog.partition_by_local_copy([(E, FILE)], index)


found, remaining = split({(10490, 156665): local(10490, 156665)})
check("an archive waiting to be installed is offered", len(found) == 1 and not remaining)
check("and it carries the record found on disk", found and found[0][2].file_id == 156665)

found, remaining = split(
    {(10490, 156665): local(10490, 156665, state=INSTALLED, hidden=True)}
)
check(
    "an archive MO2 installed and then hid is still installable",
    len(found) == 1 and not remaining,
    "hiding after install is the normal workflow; gating on `usable` sent the "
    "user to the Downloads tab to unhide it by hand",
)

found, remaining = split({(10490, 156665): local(10490, 156665, state=INCOMPLETE)})
check(
    "an interrupted download is not an archive",
    not found and len(remaining) == 1,
    "there is no whole file behind it",
)

found, remaining = split({(10490, 999999): local(10490, 999999)})
check(
    "a different file on the same page is not this file",
    not found and len(remaining) == 1,
    "the index is keyed on (mod id, file id); the mod id alone would collide",
)

found, remaining = split({})
check("nothing downloaded yet", not found and len(remaining) == 1)

found, remaining = dialog.partition_by_local_copy(
    [(E, {"name": "no id"})], {(10490, 156665): local(10490, 156665)}
)
check("a file record with no id cannot match anything", not found and len(remaining) == 1)

check(
    "installable() and usable disagree exactly where it matters",
    installable({(1, 2): local(1, 2, state=INSTALLED)}, 1, 2) is not None
    and not local(1, 2, state=INSTALLED).usable,
)


# -- a leftover ignore flag --------------------------------------------------
#
# Real case: Cyberpunk Ultra Plus carries ignoredVersion=6.2.2.0 while its page
# is on 9.1.5.0, so `is_ignored` correctly declines and the row is an update --
# but the context menu still offered "clear the ignore flag", which reads as
# though the update were being hidden.

print("ignore_is_spent")
check(
    "Ultra Plus: flag from 6.2.2.0, page on 9.1.5.0, row is an update",
    dialog.ignore_is_spent(
        FakeEntry(ignored="6.2.2.0", latest="9.1.5.0", status="update")
    ),
    "the flag is not hiding this update and the menu has to say so",
)
check(
    "a flag naming the current release is doing its job",
    not dialog.ignore_is_spent(
        FakeEntry(ignored="9.1.5.0", latest="9.1.5.0", status="ignored")
    ),
)
check(
    "an overridden flag is not spent",
    not dialog.ignore_is_spent(
        FakeEntry(ignored="9.1.5.0", latest="9.1.5.0", forced="9.1.5.0", status="update")
    ),
    "'Respect MO2's ignore flag again' would bring it straight back",
)
check("no flag at all", not dialog.ignore_is_spent(FakeEntry(latest="9.1.5.0")))
check(
    "whitespace is not a flag",
    not dialog.ignore_is_spent(FakeEntry(ignored="   ", latest="9.1.5.0")),
)


# -- default window size -----------------------------------------------------
#
# A share of the usable screen, not a pixel size. The minimum is a floor the
# share may not go under, and the screen is a ceiling it may not go over.

print("fit_to_screen")
check(
    "1440p: comfortably under full screen",
    dialog.fit_to_screen(2560, 1400) == (1843, 1092),
    dialog.fit_to_screen(2560, 1400),
)
check(
    "4K: scales up rather than staying at the old 1040x640",
    dialog.fit_to_screen(3840, 2120)[0] > 2500,
    dialog.fit_to_screen(3840, 2120),
)
check(
    "1366x768 laptop: the share would go under the minimum, so the minimum wins",
    dialog.fit_to_screen(1366, 728) == (1040, 640),
    dialog.fit_to_screen(1366, 728),
)
check(
    "a screen smaller than the minimum never yields a smaller window",
    dialog.fit_to_screen(800, 600) == (1040, 640),
    dialog.fit_to_screen(800, 600),
)
check(
    "never wider than the screen it is on",
    all(
        dialog.fit_to_screen(w, h)[0] <= max(w, 1040)
        and dialog.fit_to_screen(w, h)[1] <= max(h, 640)
        for w, h in ((1920, 1040), (2560, 1400), (3840, 2120), (1366, 728))
    ),
)

print("parse_geometry")
check("round trips", dialog.parse_geometry("100,50,1600,1000") == (100, 50, 1600, 1000))
check("a negative x on a left-hand monitor survives",
      dialog.parse_geometry("-1920,0,1600,1000") == (-1920, 0, 1600, 1000))
check("nothing saved yet", dialog.parse_geometry("") is None)
check("MO2 handing back None", dialog.parse_geometry(None) is None)
check("garbage", dialog.parse_geometry("wide,tall,x,y") is None)
check("wrong shape", dialog.parse_geometry("100,50,1600") is None)


print()
print("FAILED: " + ", ".join(FAILURES) if FAILURES else "all checks passed")
sys.exit(1 if FAILURES else 0)
