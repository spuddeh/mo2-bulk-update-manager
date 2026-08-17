"""Nexus credential discovery.

MO2 stores its Nexus credentials in the Windows Credential Manager under
``ModOrganizer2_<key>`` (see ``settingsutilities.cpp:208`` in the MO2 source).
Two entries matter:

``ModOrganizer2_NEXUS_OAUTH_TOKENS``
    A UTF-16LE JSON blob: ``access_token``, ``refresh_token``, ``scope``,
    ``token_type``, ``expires_at`` (ISO-8601 with milliseconds, UTC).

``ModOrganizer2_APIKEY``
    The legacy personal API key, stored as plain UTF-16LE text.

Both authenticate against ``api.nexusmods.com/v1`` -- OAuth as a bearer token,
the legacy key via the ``apikey`` header (``nxmaccessmanager.cpp:201``).
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
from datetime import datetime, timedelta, timezone
from typing import NamedTuple, Optional

from .log import get_logger, tag

_log = get_logger("credentials")

CRED_TYPE_GENERIC = 1

OAUTH_CREDENTIAL = "ModOrganizer2_NEXUS_OAUTH_TOKENS"
APIKEY_CREDENTIAL = "ModOrganizer2_APIKEY"

# MO2 treats a token as expired 5 minutes early; match that so we fall back
# before Nexus starts returning 401s.
_EXPIRY_SKEW = timedelta(minutes=5)


class _CREDENTIAL(ctypes.Structure):
    _fields_ = [
        ("Flags", wt.DWORD),
        ("Type", wt.DWORD),
        ("TargetName", wt.LPWSTR),
        ("Comment", wt.LPWSTR),
        ("LastWritten", wt.FILETIME),
        ("CredentialBlobSize", wt.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
        ("Persist", wt.DWORD),
        ("AttributeCount", wt.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wt.LPWSTR),
        ("UserName", wt.LPWSTR),
    ]


_advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
_advapi32.CredReadW.argtypes = [
    wt.LPCWSTR,
    wt.DWORD,
    wt.DWORD,
    ctypes.POINTER(ctypes.POINTER(_CREDENTIAL)),
]
_advapi32.CredReadW.restype = wt.BOOL
_advapi32.CredFree.argtypes = [ctypes.c_void_p]
_advapi32.CredFree.restype = None


class Auth(NamedTuple):
    """A usable set of Nexus credentials."""

    scheme: str  # "oauth" or "apikey"
    token: str
    source: str  # human-readable, shown in the UI

    def apply_to(self, set_header) -> None:
        """Call ``set_header(name: bytes, value: bytes)`` with the auth header."""
        if self.scheme == "oauth":
            set_header(b"Authorization", b"Bearer " + self.token.encode("utf-8"))
        else:
            set_header(b"apikey", self.token.encode("utf-8"))


def read_credential(target: str) -> Optional[str]:
    """Read a generic Windows credential, or None if absent/unreadable."""
    ptr = ctypes.POINTER(_CREDENTIAL)()
    if not _advapi32.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(ptr)):
        return None

    try:
        cred = ptr.contents
        size = int(cred.CredentialBlobSize)
        if size <= 0 or not cred.CredentialBlob:
            return None
        raw = ctypes.string_at(cred.CredentialBlob, size)
        # MO2 writes the value as a wchar_t buffer, so decode as UTF-16LE.
        return raw.decode("utf-16-le", errors="replace").rstrip("\x00")
    finally:
        _advapi32.CredFree(ptr)


def _parse_expiry(value: str) -> Optional[datetime]:
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def read_oauth_token() -> tuple[Optional[str], bool]:
    """Return ``(access_token, expired)``. Token is None when nothing is stored."""
    raw = read_credential(OAUTH_CREDENTIAL)
    if not raw:
        return None, False

    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None, False

    token = data.get("access_token") or ""
    if not token:
        return None, False

    expires_at = _parse_expiry(data.get("expires_at") or "")
    if expires_at is None:
        # No usable expiry: assume it is worth a try and let a 401 decide.
        return token, False

    return token, datetime.now(timezone.utc) + _EXPIRY_SKEW >= expires_at


def resolve_auth(manual_key: str = "") -> tuple[Optional[Auth], str]:
    """Pick the best available credentials.

    Preference order: a live MO2 OAuth token, MO2's legacy API key, the key the
    user pasted into plugin settings, then an expired OAuth token as a last
    resort. Returns ``(auth, note)`` where ``note`` explains a fallback or, when
    ``auth`` is None, why nothing worked.
    """
    manual_key = (manual_key or "").strip()

    try:
        token, expired = read_oauth_token()
    except OSError as exc:  # credential manager unavailable
        token, expired = None, False
        note_prefix = f"Could not read the Windows credential store ({exc}). "
    else:
        note_prefix = ""

    if token and not expired:
        _log.info(tag("Using MO2's stored Nexus OAuth token"))
        return Auth("oauth", token, "MO2 Nexus login (OAuth)"), note_prefix

    legacy = read_credential(APIKEY_CREDENTIAL)
    if legacy:
        _log.info(
            tag(
                "Using MO2's stored Nexus API key (OAuth token "
                f"{'expired' if token else 'absent'})"
            )
        )
        note = note_prefix
        if token and expired:
            note += "MO2's OAuth token has expired; using its stored API key instead. "
        return Auth("apikey", legacy.strip(), "MO2 stored API key"), note

    if manual_key:
        _log.info(tag("Using the API key from plugin settings"))
        note = note_prefix
        if token and expired:
            note += "MO2's OAuth token has expired; using the API key from plugin settings. "
        return Auth("apikey", manual_key, "API key from plugin settings"), note

    if token:
        _log.warning(tag("Only an expired OAuth token is available; trying it anyway"))
        return (
            Auth("oauth", token, "MO2 Nexus login (OAuth, expired)"),
            note_prefix
            + "MO2's OAuth token looks expired. If checks fail, open any Nexus page in "
            "MO2 to refresh it, or paste a personal API key in the plugin settings.",
        )

    _log.error(tag("No Nexus credentials found in the Windows Credential Manager"))
    return None, (
        note_prefix
        + "No Nexus credentials found. Log in to Nexus from MO2 (Settings > Nexus), or "
        "paste a personal API key into this plugin's settings "
        "(nexusmods.com/users/myaccount?tab=api)."
    )
