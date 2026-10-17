"""IGDB (owned by Twitch): metadata, cover art, critic scores and time-to-beat.

Free. Create an app at https://dev.twitch.tv/console/apps (OAuth redirect URL:
http://localhost, category: Application Integration, client type: Confidential),
then copy its Client ID and generate a Client Secret.
"""

from __future__ import annotations

import difflib
import json
import threading
import time

from .. import db, net, settings

TOKEN_URL = "https://id.twitch.tv/oauth2/token"
API = "https://api.igdb.com/v4"
IMG = "https://images.igdb.com/igdb/image/upload/{size}/{image_id}.jpg"
STEAM_SOURCE = 1  # external_game_source / legacy category id for Steam

PLATFORM_HINTS = {  # our platform -> IGDB platform ids that count as a match
    "playstation": {48, 167},           # PS4, PS5
    "steam": {6},                       # PC
    "epic": {6}, "gog": {6}, "ubisoft": {6}, "battlenet": {6},
    "xbox": {49, 169, 6},               # Xbox One, Series X|S, PC
    "switch": {130},
    "switch2": {130, 508},              # Switch, Switch 2
}

_limiter = net.RateLimiter(3.5)
_token_lock = threading.Lock()
_token: dict = {}


class IGDBError(Exception):
    pass


def configured() -> bool:
    return bool(settings.get("igdb_client_id") and settings.get("igdb_client_secret"))


def _auth_headers() -> dict:
    cid, secret = settings.get("igdb_client_id"), settings.get("igdb_client_secret")
    if not (cid and secret):
        raise IGDBError("Add your IGDB (Twitch) Client ID and Secret in Settings.")
    with _token_lock:
        if _token.get("cid") != cid or _token.get("expires", 0) < time.time() + 60:
            try:
                data = net.request(
                    "POST",
                    TOKEN_URL,
                    params={"client_id": cid, "client_secret": secret, "grant_type": "client_credentials"},
                ).json()
            except net.HttpError as e:
                if e.status in (400, 401, 403):
                    raise IGDBError("Twitch rejected the IGDB Client ID / Secret. Check them in Settings.") from None
                raise
            _token.update(cid=cid, value=data["access_token"], expires=time.time() + data.get("expires_in", 3600))
        return {"Client-ID": cid, "Authorization": f"Bearer {_token['value']}"}


def q(endpoint: str, body: str) -> list[dict]:
    _limiter.wait()
    resp = net.request("POST", f"{API}/{endpoint}", headers=_auth_headers(), data=body, timeout=45)
    return resp.json() or []


def image(image_id: str | None, size: str = "t_cover_big_2x") -> str | None:
    return IMG.format(size=size, image_id=image_id) if image_id else None


def _quote(s: str) -> str:
    return '"' + s.replace("\\", "").replace('"', "") + '"'


# ------------------------------------------------------------------ matching

def steam_to_igdb(appids: list[int]) -> dict[int, int]:
    """Map Steam app ids -> IGDB game ids via IGDB's external_games."""
    out: dict[int, int] = {}
    for i in range(0, len(appids), 200):
        chunk = appids[i : i + 200]
        uids = ",".join(_quote(str(a)) for a in chunk)
        rows = q("external_games", f"fields *; where uid = ({uids}); limit 500;")
        for r in rows:
            source = r.get("external_game_source", r.get("category"))
            if isinstance(source, dict):
                source = source.get("id")
            url = r.get("url") or ""
            if source == STEAM_SOURCE or "store.steampowered.com" in url:
                try:
                    out.setdefault(int(r["uid"]), int(r["game"]))
                except (KeyError, ValueError, TypeError):
                    continue
    return out


SEARCH_FIELDS = (
    "name,first_release_date,platforms,parent_game,version_parent,total_rating_count,"
    "alternative_names.name,cover.image_id"
)


def search(title: str, limit: int = 12) -> list[dict]:
    title = title.strip()
    if not title:
        return []
    return q("games", f"search {_quote(title)}; fields {SEARCH_FIELDS}; limit {limit};")


def best_match(title: str, platforms: list[str]) -> tuple[dict | None, float]:
    """Pick the most likely IGDB game for a store title. Returns (game, confidence)."""
    candidates = search(db.clean_display_title(title), limit=15)
    if not candidates:
        return None, 0.0
    target = db.normalize_title(title)
    hints = set().union(*(PLATFORM_HINTS.get(p, set()) for p in platforms)) if platforms else set()
    best, best_score = None, 0.0
    for c in candidates:
        names = [c.get("name") or ""] + [a.get("name", "") for a in c.get("alternative_names") or []]
        sim = max(difflib.SequenceMatcher(None, target, db.normalize_title(n)).ratio() for n in names)
        score = sim
        if hints and hints & set(c.get("platforms") or []):
            score += 0.08
        if c.get("version_parent") or c.get("parent_game"):
            score -= 0.06  # editions / DLC: prefer the main game entry
        score += min((c.get("total_rating_count") or 0) / 4000, 0.04)
        if score > best_score:
            best, best_score = c, score
    return (best, best_score) if best_score >= 0.72 else (None, best_score)


# ------------------------------------------------------------------ details

DETAIL_FIELDS = (
    "name,slug,url,summary,storyline,first_release_date,aggregated_rating,aggregated_rating_count,"
    "rating,rating_count,genres.name,themes.name,game_modes.name,player_perspectives.name,"
    "involved_companies.company.name,involved_companies.developer,involved_companies.publisher,"
    "franchises.name,collections.name,platforms.abbreviation,platforms.name,cover.image_id,"
    "artworks.image_id,screenshots.image_id,websites.url"
)


def details(igdb_ids: list[int]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for i in range(0, len(igdb_ids), 200):
        chunk = ",".join(str(x) for x in igdb_ids[i : i + 200])
        for g in q("games", f"fields {DETAIL_FIELDS}; where id = ({chunk}); limit 500;"):
            out[g["id"]] = g
    return out


def times_to_beat(igdb_ids: list[int]) -> dict[int, dict]:
    """IGDB's own time-to-beat data (seconds -> hours)."""
    out: dict[int, dict] = {}
    for i in range(0, len(igdb_ids), 200):
        chunk = ",".join(str(x) for x in igdb_ids[i : i + 200])
        try:
            rows = q(
                "game_time_to_beats",
                f"fields game_id,hastily,normally,completely,count; where game_id = ({chunk}); limit 500;",
            )
        except net.HttpError:
            return out
        for r in rows:
            def hours(k):
                v = r.get(k)
                return round(v / 3600, 1) if v else None
            out[r["game_id"]] = {"main": hours("hastily") or hours("normally"),
                                 "extra": hours("normally"), "complete": hours("completely")}
    return out


def _names(items) -> list[str]:
    return [i["name"] for i in items or [] if isinstance(i, dict) and i.get("name")]


def apply_details(c, game_id: int, g: dict) -> None:
    devs = [ic["company"]["name"] for ic in g.get("involved_companies") or []
            if ic.get("developer") and isinstance(ic.get("company"), dict)]
    pubs = [ic["company"]["name"] for ic in g.get("involved_companies") or []
            if ic.get("publisher") and isinstance(ic.get("company"), dict)]
    cover = (g.get("cover") or {}).get("image_id")
    artworks = [a["image_id"] for a in g.get("artworks") or [] if a.get("image_id")]
    shots = [s["image_id"] for s in g.get("screenshots") or [] if s.get("image_id")]
    franchises = _names(g.get("franchises")) or _names(g.get("collections"))
    platforms = [p.get("abbreviation") or p.get("name") for p in g.get("platforms") or [] if isinstance(p, dict)]
    c.execute(
        """UPDATE games SET summary=?, storyline=?, genres=?, themes=?, modes=?, perspectives=?,
           developers=?, publishers=?, franchises=?, igdb_platforms=?, screenshots=?, release_date=?,
           critic_score=?, critic_count=?, user_score=?, user_count=?, igdb_url=?, igdb_cover=?,
           igdb_artwork=?, meta_at=?, title=COALESCE(?, title) WHERE id=?""",
        (
            g.get("summary"), g.get("storyline"),
            db.set_json(_names(g.get("genres"))), db.set_json(_names(g.get("themes"))),
            db.set_json(_names(g.get("game_modes"))), db.set_json(_names(g.get("player_perspectives"))),
            db.set_json(devs), db.set_json(pubs), db.set_json(franchises), db.set_json(platforms),
            db.set_json([image(s, "t_screenshot_big") for s in shots[:12]]),
            g.get("first_release_date"),
            round(g["aggregated_rating"], 1) if g.get("aggregated_rating") else None,
            g.get("aggregated_rating_count"),
            round(g["rating"], 1) if g.get("rating") else None,
            g.get("rating_count"),
            g.get("url"),
            image(cover, "t_cover_big_2x"),
            image(artworks[0], "t_1080p") if artworks else (image(shots[0], "t_1080p") if shots else None),
            db.now(),
            g.get("name"),
            game_id,
        ),
    )


def test() -> str:
    hits = search("Hollow Knight", limit=1)
    return "Connected to IGDB." if hits else "Connected, but a test search returned nothing."


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def steam_appid_from_websites(g: dict) -> int | None:
    """IGDB often lists the Steam store page among a game's websites."""
    import re
    for w in g.get("websites") or []:
        m = re.search(r"store\.steampowered\.com/app/(\d+)", (w or {}).get("url") or "")
        if m:
            return int(m.group(1))
    return None


def art_options(igdb_id: int) -> dict:
    rows = q("games", f"fields cover.image_id,artworks.image_id,screenshots.image_id; where id = {int(igdb_id)};")
    if not rows:
        return {}
    g = rows[0]
    cover = (g.get("cover") or {}).get("image_id")
    wide = [a["image_id"] for a in (g.get("artworks") or []) if a.get("image_id")]
    wide += [a["image_id"] for a in (g.get("screenshots") or []) if a.get("image_id")]
    return {
        "cover": [{"url": image(cover, "t_cover_big_2x"), "thumb": image(cover, "t_cover_big"), "source": "IGDB"}] if cover else [],
        "hero": [{"url": image(i, "t_1080p"), "thumb": image(i, "t_screenshot_med"), "source": "IGDB"} for i in wide[:16]],
    }
