"""Public Steam store data: user review summaries and official library artwork.

Artwork: Steam stores art for newer games (roughly 2024 onward) under hashed
paths, e.g. .../apps/{appid}/{hash}/library_600x900_2x.jpg. The old fixed path
then serves a grey placeholder, which the browser shows instead of failing over.
So we ask Steam's store service for each game's real asset paths, and only use
the old fixed paths after confirming they exist.
"""

from __future__ import annotations

import json

from .. import net, settings

_limiter = net.RateLimiter(2)
ASSET_BASE = "https://shared.fastly.steamstatic.com/store_item_assets/"
LEGACY = ASSET_BASE + "steam/apps/{appid}/{file}"

# asset key in Steam's response -> our slot, best first
_SLOTS = {
    "cover": ("library_capsule_2x", "library_capsule"),
    "hero": ("library_hero_2x", "library_hero"),
    "logo": ("library_logo_2x", "library_logo"),
    "header": ("header", "main_capsule"),
}


def store_assets(appids: list[int]) -> dict[int, dict]:
    """Real artwork URLs for many Steam apps at once: {appid: {"cover", "hero", "logo", "header"}}."""
    out: dict[int, dict] = {}
    for i in range(0, len(appids), 100):
        chunk = appids[i : i + 100]
        payload = {
            "ids": [{"appid": int(a)} for a in chunk],
            "context": {"language": "english", "country_code": "US"},
            "data_request": {"include_assets": True},
        }
        params = {"input_json": json.dumps(payload, separators=(",", ":"))}
        key = settings.get("steam_api_key")
        if key:
            params["key"] = key
        _limiter.wait()
        try:
            data = net.get_json("https://api.steampowered.com/IStoreBrowseService/GetItems/v1/", params=params)
        except net.HttpError:
            continue
        for item in ((data or {}).get("response") or {}).get("store_items") or []:
            appid = item.get("appid") or item.get("id")
            assets = item.get("assets") or {}
            fmt = assets.get("asset_url_format")
            if not appid or not fmt:
                continue
            urls = {}
            for slot, names in _SLOTS.items():
                for name in names:
                    filename = assets.get(name)
                    if isinstance(filename, str) and filename:
                        urls[slot] = ASSET_BASE + fmt.replace("${FILENAME}", filename)
                        break
            out[int(appid)] = urls
    return out


def image_size(url: str) -> tuple[int, int] | None:
    """Width and height of a JPEG/PNG/WebP, reading only the start of the file."""
    import struct
    try:
        r = net.request("GET", url, retries=0, timeout=12, headers={"Range": "bytes=0-65535", "Accept": "image/*"})
    except net.HttpError:
        return None
    b = r.body
    if b[:8] == b"\x89PNG\r\n\x1a\n" and len(b) >= 24:
        return struct.unpack(">II", b[16:24])
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        if b[12:16] == b"VP8X" and len(b) >= 30:
            return (1 + int.from_bytes(b[24:27], "little"), 1 + int.from_bytes(b[27:30], "little"))
        if b[12:16] == b"VP8 " and len(b) >= 30:
            w, h = struct.unpack("<HH", b[26:30])
            return (w & 0x3FFF, h & 0x3FFF)
    if b[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(b):
            if b[i] != 0xFF:
                i += 1
                continue
            marker = b[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", b[i + 5:i + 9])
                return (w, h)
            seg = struct.unpack(">H", b[i + 2:i + 4])[0]
            i += 2 + seg
    return None


def is_generated_cover(url: str) -> bool:
    """Steam makes a "portrait.png" for games that never got real library art: the wide store
    banner at the top of a tall, smeared fill. It's the right shape but looks wrong."""
    return (url or "").split("?")[0].rsplit("/", 1)[-1].lower() == "portrait.png"


def is_portrait_cover(url: str) -> bool:
    """True when the image is a proper tall cover (about 2:3), not a wide banner."""
    size = image_size(url)
    if not size or not size[1]:
        return True  # couldn't tell: give it the benefit of the doubt
    return size[0] / size[1] <= 0.8


def url_exists(url: str) -> bool:
    try:
        r = net.request("HEAD", url, retries=1, timeout=10, headers={"Accept": "*/*"})
        return 200 <= r.status < 300 and (r.headers.get("Content-Type") or "").startswith("image/")
    except net.HttpError:
        return False


def legacy_art(appid: int) -> dict:
    """The old fixed-path artwork, keeping only files that really exist."""
    files = {
        "cover": "library_600x900_2x.jpg",
        "hero": "library_hero.jpg",
        "logo": "logo.png",
        "header": "header.jpg",
    }
    out = {}
    for slot, file in files.items():
        url = LEGACY.format(appid=appid, file=file)
        if url_exists(url):
            out[slot] = url
    return out


def find_app(title: str) -> int | None:
    """Look a game up on the Steam store by name. Only accepts a close title match."""
    import difflib
    from .. import db
    _limiter.wait()
    try:
        data = net.get_json(
            "https://store.steampowered.com/api/storesearch/",
            params={"term": db.clean_display_title(title), "cc": "US", "l": "english"},
        )
    except net.HttpError:
        return None
    target = db.normalize_title(title)
    best, best_sim = None, 0.0
    for item in (data or {}).get("items") or []:
        if item.get("type") not in (None, "app", "game"):
            continue
        sim = difflib.SequenceMatcher(None, target, db.normalize_title(item.get("name") or "")).ratio()
        if sim > best_sim:
            best, best_sim = item, sim
    return int(best["id"]) if best and best_sim >= 0.9 and str(best.get("id", "")).isdigit() else None


def reviews_many(appids: list[int]) -> dict[int, dict | None]:
    """Steam's user review summary for many apps at once (100 per call), from the store service.
    (The old appreviews page stopped answering scripts in Oct 2026: it now redirects to the store.)
    Apps Steam couldn't be asked about are left out, so they're tried again next time."""
    out: dict[int, dict | None] = {}
    ids = [int(a) for a in appids if a]
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        payload = {"ids": [{"appid": a} for a in chunk], "context": {"language": "english", "country_code": "CA"},
                   "data_request": {"include_reviews": True}}
        params = {"input_json": json.dumps(payload, separators=(",", ":"))}
        if settings.get("steam_api_key"):
            params["key"] = settings.get("steam_api_key")
        _limiter.wait()
        try:
            data = net.get_json("https://api.steampowered.com/IStoreBrowseService/GetItems/v1/", params=params, retries=1)
        except net.HttpError:
            continue
        got = set()
        for item in ((data or {}).get("response") or {}).get("store_items") or []:
            appid = item.get("appid") or item.get("id")
            if not appid:
                continue
            got.add(int(appid))
            rv = item.get("reviews") or {}
            s = rv.get("summary_filtered") or rv.get("summary_language_specific") or {}
            count = int(s.get("review_count") or 0)
            out[int(appid)] = {"pct": float(s.get("percent_positive") or 0), "count": count,
                               "desc": s.get("review_score_label")} if count else None
        if got:  # Steam answered: an app it left out has no store page, so no reviews
            for a in chunk:
                out.setdefault(a, None)
    return out


def reviews(appid: int) -> dict | None:
    return reviews_many([appid]).get(int(appid))

# ------------------------------------------------------------------ what players say

_top_cache: dict[int, tuple[float, list]] = {}
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


def _plain(text: str) -> str:
    import re as _re
    t = _re.sub(r"\[/?[a-z0-9*]+(=[^\]]*)?\]", " ", text or "", flags=_re.I)  # Steam's [b], [url=…], [spoiler]…
    t = _re.sub(r"[ \t]+", " ", t)
    t = _re.sub(r"\s*\n\s*(\n\s*)+", "\n\n", t)
    return t.strip()


def top_reviews(appid: int, n: int = 3) -> list[dict]:
    """The most helpful English Steam reviews (what the store page shows first), with a critical
    one among them when there's one near the top, so you hear both sides."""
    import time as _time
    appid = int(appid)
    hit = _top_cache.get(appid)
    if hit and _time.time() - hit[0] < 86400:
        return hit[1]
    _limiter.wait()
    data = net.get_json(f"https://store.steampowered.com/ajaxappreviews/{appid}", retries=1, timeout=20,
                        headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
                        params={"filter": "summary", "language": "english", "purchase_type": "all", "review_type": "all",
                                "use_review_quality": 1, "filter_offtopic_activity": 1, "cursor": "*", "num_per_page": 10}) or {}
    rows = []
    for r in data.get("reviews") or []:
        text = _plain(r.get("review") or "")
        if len(text) < 30:
            continue  # "10/10", "yes": not much to go on
        a = r.get("author") or {}
        rows.append({"up": bool(r.get("voted_up")), "text": text[:900], "long": len(text) > 900,
                     "hours": round((a.get("playtime_at_review") or a.get("playtime_forever") or 0) / 60, 1),
                     "helpful": int(r.get("votes_up") or 0), "at": int(r.get("timestamp_created") or 0),
                     "early": bool(r.get("written_during_early_access")), "id": str(r.get("recommendationid") or "")})
    top = rows[:n]
    if top and all(x["up"] for x in top):
        neg = next((x for x in rows[n:] if not x["up"]), None)
        if neg:
            top = top[:n - 1] + [neg]
    _top_cache[appid] = (_time.time(), top)
    return top
