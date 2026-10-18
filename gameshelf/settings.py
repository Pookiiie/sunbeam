"""Credentials and preferences, stored locally in data/settings.json."""

from __future__ import annotations

import json
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
    "xbl_key": True,        # OpenXBL API key (xbl.io)
    "xbl_gamertag": False,
    "ubi_ticket": True,     # Ubisoft Connect, from the Send to <app> bookmark
    "ubi_session": True,
    "ubi_remember": True,   # Ubisoft's remember-me pass, renewed every sync
    "ubi_user": False,
    "ubi_app": False,       # the Ubisoft site's app id that issued the pass
    "ubi_name": False,
    "ubi_expires": False,
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
    "setup_done": False,       # finished (or skipped) the first-run setup
    "shortcuts_asked": False,  # answered the desktop / Start menu shortcut question
    "shortcuts_made": False,
    "window_rect": False,      # the app window's size and place last time {x, y, w, h, max}   # where the shortcuts are (renamed along with the app)
}

_lock = threading.Lock()


def load() -> dict:
    with _lock:
        if CONFIG_PATH.exists():
            try:
                return json.loads(CONFIG_PATH.read_text("utf-8"))
            except json.JSONDecodeError:
                return {}
        return {}


def get(key: str, default=None):
    value = load().get(key)
    return default if value in (None, "") else value


def update(changes: dict) -> dict:
    with _lock:
        current = {}
        if CONFIG_PATH.exists():
            try:
                current = json.loads(CONFIG_PATH.read_text("utf-8"))
            except json.JSONDecodeError:
                current = {}
        for key, value in changes.items():
            if key not in FIELDS:
                continue
            if isinstance(value, str):
                value = value.strip()
            current[key] = value
        CONFIG_PATH.write_text(json.dumps(current, indent=2), "utf-8")
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
