"""Battle.net (Blizzard and Activision games on the Battle.net app).

Blizzard has no public "my games" API. Like GOG Galaxy's Battle.net plugin
(github.com/bartok765/galaxy_blizzard_plugin, MIT licence), the app reads the
list behind Blizzard's account site:

- https://account.battle.net/api/games-and-subs  {"gameAccounts": [{"titleId": …, "gameAccountStatus": …}]}
- https://account.battle.net/api/classic-games   {"classicGames": [{"localizedGameName": …}]}

The app has no browser of its own to sign in with, so you open those pages
while signed in and paste what they show into Settings (kept in
data/settings.json). Paste again when you get a new Blizzard game.

Last played comes from the Battle.net app's own file on this PC
(%APPDATA%\\Battle.net\\Battle.net.config, "Games" › <game> › "LastPlayed"); games
launched there are added even if they're missing from the pasted list. Blizzard
doesn't record playtime anywhere it shares, so hours stay unknown.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .. import settings

# A titleId is the game's Battle.net product code packed into an integer
# (21298 = "S2", 5272175 = "Pro", 1146311730 = "DST2"), so new games still
# decode to a code. Names for the codes Battle.net uses (the Galaxy plugin's list
# plus newer games).
CODES = {
    "S1": "StarCraft: Remastered",
    "S2": "StarCraft II",
    "WOW": "World of Warcraft",
    "WOW_WOW_CLASSIC": "World of Warcraft Classic",
    "PRO": "Overwatch 2",
    "W3": "Warcraft III: Reforged",
    "WTCG": "Hearthstone",
    "HERO": "Heroes of the Storm",
    "D3": "Diablo III",
    "FEN": "Diablo IV",
    "OSI": "Diablo II: Resurrected",
    "ANBS": "Diablo Immortal",
    "GRY": "Warcraft Rumble",
    "RTRO": "Blizzard Arcade Collection",
    "WLBY": "Crash Bandicoot 4: It's About Time",
    "VIPR": "Call of Duty: Black Ops 4",
    "ODIN": "Call of Duty: Modern Warfare",
    "LAZR": "Call of Duty: Modern Warfare 2 Campaign Remastered",
    "ZEUS": "Call of Duty: Black Ops Cold War",
    "FORE": "Call of Duty: Vanguard",
    "AUKS": "Call of Duty",
    "DST2": "Destiny 2",
}

# Keys in Battle.net.config's "Games" section (the app's internal names).
CONFIG_UIDS = {
    "s1": "S1", "s2": "S2", "wow": "WOW", "wow_classic": "WOW_WOW_CLASSIC", "prometheus": "PRO",
    "w3": "W3", "hs_beta": "WTCG", "heroes": "HERO", "diablo3": "D3", "fenris": "FEN", "osi": "OSI",
    "anbs": "ANBS", "gryphon": "GRY", "rtro": "RTRO", "wlby": "WLBY", "viper": "VIPR", "odin": "ODIN",
    "lazarus": "LAZR", "zeus": "ZEUS", "fore": "FORE", "auks": "AUKS", "destiny2": "DST2",
}

# Statuses that mean you can play it (a "Trial" is someone else's guest pass).
PLAYABLE = {"good", "free", "inactive", "suspended", "banned", "accountlock", ""}


class BattleNetError(Exception):
    pass


def code_from_title_id(title_id) -> str | None:
    try:
        n = int(title_id)
    except (TypeError, ValueError):
        return None
    raw = n.to_bytes(max(1, (n.bit_length() + 7) // 8), "big")
    try:
        code = raw.decode("ascii")
    except UnicodeDecodeError:
        return None
    return code.upper() if code.isprintable() and code.strip() else None


def _json_blobs(text: str) -> list[dict]:
    """Every JSON object in what was pasted (one page, both pages, or a page with stray text)."""
    out, dec, i = [], json.JSONDecoder(), 0
    while True:
        i = text.find("{", i)
        if i < 0:
            return out
        try:
            obj, end = dec.raw_decode(text, i)
        except ValueError:
            i += 1
            continue
        if isinstance(obj, dict):
            out.append(obj)
        i = end


def _name_in(acc: dict) -> str | None:
    for k in ("localizedGameName", "gameName", "titleName", "localizedTitle", "name", "title"):
        v = acc.get(k)
        if isinstance(v, str) and v.strip():
            return v.replace("\xa0", " ").strip()
    return None


def parse_pasted(text: str) -> tuple[dict[str, str], list[str]]:
    """{code: name} from the pasted pages, plus codes the app has no name for."""
    games: dict[str, str] = {}
    unknown: list[str] = []
    blobs = _json_blobs(text or "")
    if (text or "").strip() and not blobs:
        raise BattleNetError("That doesn't look like the Battle.net page. Copy everything the page shows "
                             "(it starts with {) and paste it again.")
    for blob in blobs:
        for acc in blob.get("gameAccounts") or []:
            if str(acc.get("gameAccountStatus") or "").lower() not in PLAYABLE:
                continue
            code = code_from_title_id(acc.get("titleId"))
            name = CODES.get(code or "") or _name_in(acc)
            if code and name:
                games[code] = name
            elif code and code not in unknown:
                unknown.append(code)
        for cg in blob.get("classicGames") or []:
            name = _name_in(cg)
            if name:
                games["classic:" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")] = re.sub(r"[®™]", "", name)
    if games.get("WOW"):
        games.setdefault("WOW_WOW_CLASSIC", CODES["WOW_WOW_CLASSIC"])
    return games, unknown


def _config_path() -> Path | None:
    base = os.environ.get("APPDATA")
    p = Path(base) / "Battle.net" / "Battle.net.config" if base else None
    return p if p and p.exists() else None


def last_played() -> dict[str, int]:
    """{code: unix time} from the Battle.net app's file on this PC."""
    p = _config_path()
    if not p:
        return {}
    try:
        data = json.loads(p.read_text("utf-8", errors="replace"))
    except (OSError, ValueError):
        return {}
    out = {}
    for uid, props in (data.get("Games") or {}).items():
        code = CONFIG_UIDS.get(uid.lower())
        try:
            ts = int(str((props or {}).get("LastPlayed") or "0").strip())
        except ValueError:
            continue
        if code and ts >= 1072915200:
            out[code] = ts
    return out


def fetch() -> list[dict]:
    games, _unknown = parse_pasted(settings.get("bnet_games") or "")
    seen = last_played()
    for code in seen:
        games.setdefault(code, CODES[code])  # launched on this PC, so you have it
    if not games:
        raise BattleNetError("Paste your Battle.net games list in Settings first.")
    return [{
        "platform": "battlenet",
        "external_id": code,
        "name": name,
        "playtime_min": 0,  # Blizzard doesn't share playtime
        "last_played": seen.get(code),
        "first_played": None,
        "ownership": None,
    } for code, name in games.items()]


def test() -> str:
    games, unknown = parse_pasted(settings.get("bnet_games") or "")
    seen = last_played()
    extra = [c for c in seen if c not in games]
    if not games and not seen:
        raise BattleNetError("No games found in what was pasted.")
    msg = f"Found {len(games) + len(extra)} games"
    msg += f", {len(seen)} with a last-played date from the Battle.net app on this PC." if seen else \
        ". The Battle.net app's file wasn't found on this PC, so there are no last-played dates."
    if unknown:
        msg += f" Not recognised yet: {', '.join(unknown)} (they're left out until a later version knows them)."
    return msg
