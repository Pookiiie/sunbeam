"""Steam via the official Web API.

Needs a Steam Web API key (https://steamcommunity.com/dev/apikey) and your
SteamID64 or profile URL. Your profile's "Game details" must be Public
(Steam > Profile > Edit Profile > Privacy Settings).
"""

from __future__ import annotations

import re

from .. import net, settings

API = "https://api.steampowered.com"


class SteamError(Exception):
    pass


def _key() -> str:
    key = settings.get("steam_api_key")
    if not key:
        raise SteamError("Add your Steam Web API key in Settings.")
    return key


def resolve_steam_id(raw: str | None = None) -> str:
    raw = (raw or settings.get("steam_id") or "").strip().rstrip("/")
    if not raw:
        raise SteamError("Add your Steam profile URL or SteamID in Settings.")
    m = re.search(r"(\d{17})", raw)
    if m:
        return m.group(1)
    m = re.search(r"/id/([^/?#]+)", raw)
    vanity = m.group(1) if m else raw
    data = net.get_json(
        f"{API}/ISteamUser/ResolveVanityURL/v1/", params={"key": _key(), "vanityurl": vanity}
    )
    resp = (data or {}).get("response", {})
    if resp.get("success") != 1:
        raise SteamError(f"Couldn't find a Steam profile named “{vanity}”. Paste your full profile URL instead.")
    return resp["steamid"]


def fetch() -> list[dict]:
    steam_id = resolve_steam_id()
    try:
        data = net.get_json(
            f"{API}/IPlayerService/GetOwnedGames/v1/",
            params={
                "key": _key(),
                "steamid": steam_id,
                "include_appinfo": 1,
                "include_played_free_games": 1,
                "skip_unvetted_apps": 0,
                "format": "json",
            },
            timeout=60,
        )
    except net.HttpError as e:
        if e.status in (401, 403):
            raise SteamError("Steam rejected the API key. Double-check it in Settings.") from None
        raise
    games = (data or {}).get("response", {}).get("games")
    if games is None:
        raise SteamError(
            "Steam returned no games. Set your profile's “Game details” to Public "
            "(Profile › Edit Profile › Privacy Settings), then sync again."
        )
    out = []
    for g in games:
        appid = g.get("appid")
        if not appid:
            continue
        last = g.get("rtime_last_played") or None
        if last and last < 1072915200:  # Steam's "before tracking" placeholder (1970)
            last = None
        out.append(
            {
                "platform": "steam",
                "external_id": str(appid),
                "name": g.get("name") or f"Steam app {appid}",
                "playtime_min": int(g.get("playtime_forever") or 0),
                "last_played": int(last) if last else None,
                "first_played": None,
                "ownership": None,
                "steam_appid": int(appid),
            }
        )
    return out


def test() -> str:
    steam_id = resolve_steam_id()
    data = net.get_json(
        f"{API}/ISteamUser/GetPlayerSummaries/v2/", params={"key": _key(), "steamids": steam_id}
    )
    players = (data or {}).get("response", {}).get("players", [])
    name = players[0].get("personaname") if players else steam_id
    games = fetch()
    return f"Connected as {name}: {len(games)} games visible."
