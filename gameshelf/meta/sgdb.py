"""SteamGridDB: community-made cover art, hero banners and logos for any game
(not just Steam). Free API key: https://www.steamgriddb.com/profile/preferences/api
"""

from __future__ import annotations

import urllib.parse

from .. import net, settings

API = "https://www.steamgriddb.com/api/v2"
_limiter = net.RateLimiter(4)


def configured() -> bool:
    return bool(settings.get("sgdb_api_key"))


def _get(path: str, params: dict | None = None):
    _limiter.wait()
    try:
        data = net.get_json(
            API + path, params=params, headers={"Authorization": f"Bearer {settings.get('sgdb_api_key')}"}
        )
    except net.HttpError as e:
        if e.status == 404:
            return []
        raise
    return (data or {}).get("data") or []


def find_game_id(title: str, steam_appid: int | None, year: int | None = None) -> int | None:
    if steam_appid:
        _limiter.wait()
        try:
            data = net.get_json(
                f"{API}/games/steam/{steam_appid}",
                headers={"Authorization": f"Bearer {settings.get('sgdb_api_key')}"},
            )
            gid = ((data or {}).get("data") or {}).get("id")
            if gid:
                return gid
        except net.HttpError:
            pass
    # Search by name, and only accept a close title match (the first hit for
    # "Final Fantasy XV" can be XVI); prefer the right release year.
    import datetime
    import difflib
    from .. import db
    hits = _get(f"/search/autocomplete/{urllib.parse.quote(db.clean_display_title(title), safe='')}")
    target = db.normalize_title(title)
    best, best_score = None, 0.0
    for h in hits[:10]:
        name = db.normalize_title(h.get("name") or "")
        score = difflib.SequenceMatcher(None, target, name).ratio()
        if name != target and (name.startswith(target + " ") or target.startswith(name + " ")):
            score -= 0.15  # a sequel or edition of it, not the game itself
        rel = h.get("release_date")
        if year and rel:
            try:
                if datetime.datetime.fromtimestamp(int(rel), datetime.timezone.utc).year == year:
                    score += 0.05
            except (ValueError, OSError):
                pass
        if score > best_score:
            best, best_score = h, score
    return best["id"] if best and best_score >= 0.86 else None


def _first(items: list) -> str | None:
    for it in items:
        if it.get("url") and not it.get("nsfw"):
            return it["url"]
    return None


def artwork(title: str, steam_appid: int | None, year: int | None = None) -> dict:
    """Returns {"cover", "hero", "logo"}; any may be None."""
    gid = find_game_id(title, steam_appid, year)
    if not gid:
        return {}
    common = {"nsfw": "false", "humor": "false"}
    return {
        "cover": _first(_get(f"/grids/game/{gid}", {**common, "dimensions": "600x900,342x482,660x930",
                                                     "types": "static"})),
        "hero": _first(_get(f"/heroes/game/{gid}", {**common, "types": "static"})),
        "logo": _first(_get(f"/logos/game/{gid}", {**common, "types": "static"})),
    }


def test() -> str:
    hits = _get("/search/autocomplete/hades")
    return "Connected to SteamGridDB." if hits else "Connected, but a test search returned nothing."


def options(title: str, steam_appid: int | None, per_slot: int = 30, year: int | None = None) -> dict:
    """Lots of community artwork to choose from: {"cover": [...], "hero": [...], "logo": [...]}."""
    gid = find_game_id(title, steam_appid, year)
    if not gid:
        return {}
    common = {"nsfw": "false", "types": "static"}
    def pick(items):
        out = []
        for it in items:
            if it.get("url") and not it.get("nsfw"):
                out.append({"url": it["url"], "thumb": it.get("thumb") or it["url"], "source": "SteamGridDB",
                            "w": it.get("width"), "h": it.get("height")})
            if len(out) >= per_slot:
                break
        return out
    return {
        "cover": pick(_get(f"/grids/game/{gid}", {**common, "dimensions": "600x900,342x482,660x930"})),
        "hero": pick(_get(f"/heroes/game/{gid}", common)),
        "logo": pick(_get(f"/logos/game/{gid}", common)),
    }
