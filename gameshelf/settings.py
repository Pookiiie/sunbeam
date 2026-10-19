"""Credentials and preferences, stored locally in data/settings.json."""

from __future__ import annotations

import json
import os
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
    "hltb_enabled": False,
    "hide_early_access": False,  # keep early access games out of the library and Curator (default: shown)
    "hide_vr": False,            # keep VR games out of the library and Curator (default: hidden)
    "sub_gamepass": False,       # subscribed to XGP (PC Game Pass / Ultimate); unset = yes
    "sub_psplus": False,         # subscribed to PS+ Extra or Premium; unset = yes
    "start_page": False,         # the tab the app opens on: curator (default), library or wishlist
    "curator_layout": False,     # Curator's sections: {"order": [...], "hidden": [...]} (Customize on Curator)
    "curator_shelves": False,    # Your shelves in Curator: cards (default) or rows
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
            "ubi_refreshed_at", "xbl_xuid"}

_lock = threading.Lock()


def load() -> dict:
    with _lock:
        return _read()


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
        # Written to a temporary file first, then swapped in: a crash can't leave half a file.
        tmp = CONFIG_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(current, indent=2), "utf-8")
        os.replace(tmp, CONFIG_PATH)
        return current


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
