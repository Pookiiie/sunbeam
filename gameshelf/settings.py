"""Credentials and preferences, stored locally in data/settings.json.

Sign-ins, keys and account names are encrypted in the file with Windows' own data
protection (DPAPI), tied to your Windows account on this PC: the file is useless
anywhere else, to another Windows user, or to anything reading it without running
as you here. Copied to another PC, the sign-ins can't be read and need doing again.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import threading

from .paths import CONFIG_PATH

# key -> is it a secret (masked when sent to the browser)?
FIELDS = {
    "steam_api_key": True,
    "steam_id": False,
    "psn_npsso": True,
    "epic_code": True,      # one-time sign-in code, swapped for epic_refresh
    "epic_refresh": True,
    "epic_account": False,
    "epic_name": False,
    "gog_code": True,       # GOG: one-time sign-in code (or the address carrying it), swapped for gog_refresh
    "gog_refresh": True,
    "gog_user": False,
    "xbl_key": True,        # OpenXBL API key (xbl.io)
    "xbl_gamertag": False,
    "ubi_ticket": True,     # Ubisoft Connect, from the Send to <app> bookmark
    "ubi_session": True,
    "ubi_remember": True,   # Ubisoft's remember-me pass, renewed every sync
    "ubi_user": False,
    "ubi_app": False,       # the Ubisoft site's app id that issued the pass
    "ubi_name": False,
    "ubi_expires": False,
    "ubi_refreshed_at": False,  # when Ubisoft last answered a sync by itself (no bookmark)
    "bnet_games": True,     # what account.battle.net/api/games-and-subs (and classic-games) showed
    "xbl_xuid": False,      # your Xbox user id, so a sync needn't ask for it each time
    "igdb_client_id": False,
    "igdb_client_secret": True,
    "sgdb_api_key": True,
    "opencritic_key": True,
    "nin_link": True,       # Nintendo: the "Select this account" link pasted once, swapped for nin_session
    "nin_session": True,    # Nintendo Account sign-in (lasts about two years)
    "nin_pending": True,    # Nintendo: the one-time check for a sign-in in progress
    "nin_name": False,      # Nintendo Account nickname, for "Signed in as"
    "oc_auto": False,            # read OpenCritic scores by itself each day while the app is open (default: on)
    "hltb_enabled": False,
    "hide_early_access": False,  # keep early access games out of the library and Curator (default: shown)
    "hide_vr": False,            # keep VR games out of the library and Curator (default: hidden)
    "sub_gamepass": False,       # subscribed to XGP (PC Game Pass / Ultimate); unset = yes
    "sub_psplus": False,         # subscribed to PS+ Extra or Premium; unset = yes
    "start_page": False,         # the tab the app opens on: curator (default), library or wishlist
    "curator_layout": False,     # Curator's sections: {"order": [...], "hidden": [...]} (Customize on Curator)
    "curator_shelves": False,    # Your shelves in Curator: cards (default) or rows
    "curator_previews": False,   # previews when pointing at a cover in Curator (default: on)
    "update_channel": False,     # stable (default) or beta: test builds of new features
    "setup_done": False,       # finished (or skipped) the first-run setup
    "shortcuts_asked": False,  # answered the desktop / Start menu shortcut question
    "shortcuts_made": False,   # where the shortcuts are (renamed along with the app)
    "window_rect": False,      # the app window's size and place last time {x, y, w, h, max}
}

# Kept by the app itself, never changed from the page (POST /api/settings ignores them).
INTERNAL = {"shortcuts_made", "shortcuts_asked", "window_rect", "epic_refresh", "epic_account", "epic_name",
            "gog_refresh", "gog_user",
            "ubi_ticket", "ubi_session", "ubi_remember", "ubi_user", "ubi_app", "ubi_name", "ubi_expires",
            "ubi_refreshed_at", "xbl_xuid", "nin_session", "nin_pending", "nin_name"}

# Encrypted in the file: every secret, plus the account names and ids that say whose they are.
ENCRYPTED = {k for k, secret in FIELDS.items() if secret} | {
    "steam_id", "epic_account", "epic_name", "gog_user", "xbl_gamertag", "xbl_xuid", "ubi_user", "ubi_name",
    "ubi_app", "igdb_client_id", "nin_name"}
PREFIX = "dpapi:"
_ENTROPY = b"Sunbeam settings: sign-ins and keys"
_plain_cache: dict[str, str] = {}  # ciphertext -> text, so reading settings stays quick


# ------------------------------------------------------------------ encryption (Windows DPAPI)

def _dpapi():
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    for fn in (crypt32.CryptProtectData, crypt32.CryptUnprotectData):
        fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p,
                       ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        fn.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    def run(fn, data: bytes) -> bytes:
        buf, ent = ctypes.create_string_buffer(data, len(data)), ctypes.create_string_buffer(_ENTROPY, len(_ENTROPY))
        blob_in = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        blob_ent = Blob(len(_ENTROPY), ctypes.cast(ent, ctypes.POINTER(ctypes.c_char)))
        out = Blob()
        # 1 = CRYPTPROTECT_UI_FORBIDDEN: never show a prompt
        if not fn(ctypes.byref(blob_in), None, ctypes.byref(blob_ent), None, None, 1, ctypes.byref(out)):
            raise OSError(ctypes.GetLastError(), "Windows couldn't encrypt or decrypt a setting")
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))

    return (lambda b: run(crypt32.CryptProtectData, b)), (lambda b: run(crypt32.CryptUnprotectData, b))


try:
    _CRYPT = _dpapi()
except Exception:  # never stop the app over this
    _CRYPT = None


def available() -> bool:
    return _CRYPT is not None


def _protect(text: str) -> str:
    if not _CRYPT or not text or text.startswith(PREFIX):
        return text
    stored = PREFIX + base64.b64encode(_CRYPT[0](text.encode("utf-8"))).decode("ascii")
    _plain_cache[stored] = text
    return stored


def _unprotect(stored):
    """The text of an encrypted setting; "" when it can't be read (another PC or Windows user)."""
    if not isinstance(stored, str) or not stored.startswith(PREFIX):
        return stored
    if stored in _plain_cache:
        return _plain_cache[stored]
    if not _CRYPT:
        return ""
    try:
        text = _CRYPT[1](base64.b64decode(stored[len(PREFIX):])).decode("utf-8")
    except (OSError, ValueError):
        return ""
    if len(_plain_cache) > 200:
        _plain_cache.clear()
    _plain_cache[stored] = text
    return text

_lock = threading.Lock()


def load() -> dict:
    with _lock:
        return {k: (_unprotect(v) if k in ENCRYPTED else v) for k, v in _read().items()}


def _read() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def get(key: str, default=None):
    value = load().get(key)
    return default if value in (None, "") else value


def update(changes: dict) -> dict:
    with _lock:
        current = _read()
        if not current and CONFIG_PATH.exists() and CONFIG_PATH.stat().st_size > 2:
            # The file is there but couldn't be read: don't write over every saved key with just this change.
            backup = CONFIG_PATH.with_suffix(".unreadable.json")
            try:
                CONFIG_PATH.replace(backup)
            except OSError:
                raise RuntimeError(f"{CONFIG_PATH.name} couldn't be read, so it wasn't changed.") from None
        for key, value in changes.items():
            if key not in FIELDS:
                continue
            if isinstance(value, str):
                value = value.strip()
            current[key] = value
        # Every sign-in, key and account name goes into the file encrypted (older plain ones too).
        for key in ENCRYPTED:
            if isinstance(current.get(key), str):
                current[key] = _protect(current[key])
        # Written to a temporary file first, then swapped in: a crash can't leave half a file.
        tmp = CONFIG_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(current, indent=2), "utf-8")
        os.replace(tmp, CONFIG_PATH)
        return current


def encrypt_existing() -> int:
    """Encrypt sign-ins saved in plain text by older versions. Returns how many were."""
    if not _CRYPT:
        return 0
    with _lock:
        plain = [k for k, v in _read().items() if k in ENCRYPTED and isinstance(v, str) and v and not v.startswith(PREFIX)]
    if plain:
        update({})  # update() encrypts everything it writes
    return len(plain)


def unreadable() -> list[str]:
    """Saved sign-ins this Windows account can't decrypt (the folder came from another PC or user)."""
    with _lock:
        raw = _read()
    return [k for k, v in raw.items() if k in ENCRYPTED and isinstance(v, str) and v.startswith(PREFIX)
            and not _unprotect(v)]


def public_view() -> dict:
    """Settings safe to show in the UI: secrets become a masked hint."""
    data = load()
    out = {}
    for key, secret in FIELDS.items():
        value = data.get(key)
        if secret:
            out[key] = {"set": bool(value), "hint": ("•••• " + value[-4:]) if value and len(value) > 8 else ""}
            if key == "bnet_games" and value:
                out[key]["hint"] = "your games list"
        else:
            out[key] = value if value is not None else ""
    if out.get("hltb_enabled") == "":
        out["hltb_enabled"] = True
    return out
