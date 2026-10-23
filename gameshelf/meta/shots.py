"""Which screenshot comes first.

IGDB lists screenshots in the order they were uploaded, so the first one is often a menu,
a dark loading screen or an odd crop. Stores let the developer pick the order, and lead
with their best shot, so the order is taken from the first store that has the game:

1. Steam (the store's own order; also sharper than IGDB's copies)
2. GOG
3. Microsoft Store (Xbox games, XGP)
4. otherwise IGDB's screenshots, ranked: full-size widescreen first, then the ones that look
   most like a game in motion (colourful, detailed, not too dark or too bright, not one flat
   colour like a menu or a title card).

A screenshot you chose with "Show this one first" stays first whichever order is used.
The preview and the game page both use the stored order, so they always agree.
"""

from __future__ import annotations

import io
import json
import math
import re
from concurrent.futures import ThreadPoolExecutor

from .. import db, net, settings
from . import igdb

ASSET_BASE = "https://shared.fastly.steamstatic.com/store_item_assets/"
STEAM_ITEMS = "https://api.steampowered.com/IStoreBrowseService/GetItems/v1/"
GOG_PRODUCT = "https://api.gog.com/products/{id}"
MS_PRODUCTS = "https://displaycatalog.mp.microsoft.com/v7.0/products"
MS_LOOKUP = "https://displaycatalog.mp.microsoft.com/v7.0/products/lookup"
MAX = 12           # screenshots kept per game
MIN_STORE = 3      # a store with fewer than this isn't used (IGDB usually has more)
RANK_LOOK = 10     # IGDB screenshots looked at when ranking (small copies, about 10 KB each)

_steam_limiter = net.RateLimiter(2)


class Unavailable(Exception):
    """A store didn't answer: leave the game for next time rather than settle for a worse order."""
_store_limiter = net.RateLimiter(3)


def key(url: str | None) -> str:
    """The same screenshot whatever its size or cache-buster: used to remember your pick."""
    u = re.sub(r"[?#].*$", "", url or "")
    u = re.sub(r"\.(600x338|1920x1080|116x65)\.jpg$", ".jpg", u)                 # Steam
    u = re.sub(r"_(product_card_v2_mobile_slider_639|ggvgl|ggvgm|ggvgt)(_2x)?\.jpg$", ".jpg", u)  # GOG
    u = re.sub(r"/t_[a-z0-9_]+/", "/", u)                                           # IGDB
    return u


def with_pick(urls: list[str], pick: str | None) -> list[str]:
    if not pick:
        return urls
    k = key(pick)
    first = [u for u in urls if key(u) == k]
    return first[:1] + [u for u in urls if key(u) != k] if first else urls


# ------------------------------------------------------------------ stores

def steam(appids: list[int]) -> tuple[dict[int, list[str]], set[int]]:
    """Steam's screenshots (all-ages ones, in the store's order), 100 games per call.
    Also returns the app ids Steam couldn't be asked about this time."""
    out: dict[int, list[str]] = {}
    failed: set[int] = set()
    for i in range(0, len(appids), 100):
        chunk = appids[i:i + 100]
        payload = {"ids": [{"appid": int(a)} for a in chunk],
                   "context": {"language": "english", "country_code": "CA"},
                   "data_request": {"include_screenshots": True}}
        params = {"input_json": json.dumps(payload, separators=(",", ":"))}
        if settings.get("steam_api_key"):
            params["key"] = settings.get("steam_api_key")
        _steam_limiter.wait()
        try:
            data = net.get_json(STEAM_ITEMS, params=params, retries=1)
        except net.HttpError:
            failed.update(int(a) for a in chunk)
            continue
        for item in ((data or {}).get("response") or {}).get("store_items") or []:
            appid = item.get("appid") or item.get("id")
            shots = ((item.get("screenshots") or {}).get("all_ages_screenshots")) or []
            shots = sorted((s for s in shots if s.get("filename")), key=lambda s: s.get("ordinal") or 0)
            urls = []
            for s in shots[:MAX]:
                f = s["filename"]
                # ss_<hash>.jpg?t=… → the 600×338 copy (the viewer swaps in 1920×1080)
                urls.append(ASSET_BASE + re.sub(r"\.jpg(\?|$)", r".600x338.jpg\1", f, count=1))
            if appid and urls:
                out[int(appid)] = urls
    return out, failed


def gog(product_id: str) -> list[str]:
    _store_limiter.wait()
    try:
        data = net.get_json(GOG_PRODUCT.format(id=int(product_id)), params={"expand": "screenshots"}, retries=1) or {}
    except ValueError:
        return []
    except net.HttpError as e:
        if e.status == 404:
            return []
        raise Unavailable() from None
    out = []
    for s in data.get("screenshots") or []:
        iid = s.get("image_id")
        if iid:
            out.append(f"https://images.gog-statics.com/{iid}_product_card_v2_mobile_slider_639.jpg")
    return out[:MAX]


def _ms_shots(product: dict) -> list[str]:
    lp = (product.get("LocalizedProperties") or [{}])[0]
    seen, shots = set(), []
    for im in lp.get("Images") or []:
        if im.get("ImagePurpose") != "Screenshot" or not im.get("Uri"):
            continue
        uri = ("https:" + im["Uri"]) if im["Uri"].startswith("//") else im["Uri"]
        if uri in seen:
            continue
        seen.add(uri)
        m = re.search(r"/(\d+)$", im.get("ImagePositionInfo") or "")
        shots.append((int(m.group(1)) if m else 999, len(shots), uri + "?w=640"))
    return [u for _, _, u in sorted(shots)][:MAX]


def microsoft(product_id: str | None = None, title_id: str | None = None) -> list[str]:
    _store_limiter.wait()
    try:
        if product_id:
            data = net.get_json(MS_PRODUCTS, params={"bigIds": product_id, "market": "CA", "languages": "en-US"}, retries=1)
        else:
            data = net.get_json(MS_LOOKUP, params={"alternateId": "XboxTitleId", "value": title_id,
                                                   "market": "CA", "languages": "en-US"}, retries=1)
    except net.HttpError as e:
        if e.status in (400, 404):
            return []
        raise Unavailable() from None
    for p in (data or {}).get("Products") or []:
        shots = _ms_shots(p)
        if shots:
            return shots
    return []


# ------------------------------------------------------------------ IGDB, ranked

def _igdb_id(url: str) -> str | None:
    m = re.search(r"/t_[a-z0-9_]+/([a-z0-9]+)\.(jpg|png|webp)", url or "")
    return m.group(1) if m else None


def _sizes(igdb_ids: list[int]) -> dict[str, tuple[int, int]]:
    """Each IGDB screenshot's full size, {image_id: (width, height)}."""
    out: dict[str, tuple[int, int]] = {}
    if not igdb_ids or not igdb.configured():
        return out
    for i in range(0, len(igdb_ids), 40):
        chunk = ",".join(str(int(x)) for x in igdb_ids[i:i + 40])
        try:
            rows = igdb.q("screenshots", f"fields image_id,width,height; where game = ({chunk}); limit 500;")
        except Exception:
            continue
        for r in rows:
            if r.get("image_id") and r.get("width") and r.get("height"):
                out[r["image_id"]] = (int(r["width"]), int(r["height"]))
    return out


def look(data: bytes) -> float | None:
    """How much a screenshot looks like a game in motion, roughly 0 (menu, black screen) to 1."""
    try:
        from PIL import Image, ImageFilter  # type: ignore
    except ImportError:
        return None
    try:
        im = Image.open(io.BytesIO(data)).convert("RGB").resize((96, 54))
    except Exception:
        return None
    px = list(im.getdata())
    n = len(px)
    lum = [0.299 * r + 0.587 * g + 0.114 * b for r, g, b in px]
    mean = sum(lum) / n
    std = math.sqrt(sum((v - mean) ** 2 for v in lum) / n)
    # colourfulness (Hasler & Süsstrunk)
    rg = [r - g for r, g, _ in px]
    yb = [0.5 * (r + g) - b for r, g, b in px]
    mrg, myb = sum(rg) / n, sum(yb) / n
    srg = math.sqrt(sum((v - mrg) ** 2 for v in rg) / n)
    syb = math.sqrt(sum((v - myb) ** 2 for v in yb) / n)
    colour = math.sqrt(srg ** 2 + syb ** 2) + 0.3 * math.sqrt(mrg ** 2 + myb ** 2)
    # detail: how much changes from pixel to pixel
    edges = im.convert("L").filter(ImageFilter.FIND_EDGES)
    detail = sum(edges.getdata()) / n
    # flat: share of the most common colour (menus, title cards, letterboxing)
    counts: dict[tuple, int] = {}
    for r, g, b in px:
        q = (r >> 5, g >> 5, b >> 5)
        counts[q] = counts.get(q, 0) + 1
    flat = max(counts.values()) / n
    score = 0.4 * min(colour / 90, 1) + 0.3 * min(std / 70, 1) + 0.3 * min(detail / 45, 1)
    if mean < 45:
        score -= (45 - mean) / 45 * 0.5
    if mean > 210:
        score -= (mean - 210) / 45 * 0.5
    score -= max(0.0, flat - 0.3) * 1.2
    return round(score, 4)


def _fetch(url: str) -> bytes | None:
    try:
        return net.request("GET", url, headers={"Accept": "image/*"}, timeout=20, retries=1).body
    except net.HttpError:
        return None


def ranked(urls: list[str], sizes: dict[str, tuple[int, int]]) -> list[str]:
    """IGDB's screenshots, best first."""
    if len(urls) < 2:
        return urls
    head = urls[:RANK_LOOK]
    small = [u.replace("/t_screenshot_big/", "/t_logo_med/") for u in head]  # 284×160, about 10 KB
    with ThreadPoolExecutor(max_workers=4) as ex:
        got = list(ex.map(_fetch, small))
    if not any(got):
        raise Unavailable()  # offline? try again later
    looks = [look(b) if b else None for b in got]

    def score(i: int) -> float:
        s = looks[i] if looks[i] is not None else 0.5
        w, h = sizes.get(_igdb_id(head[i]) or "", (0, 0))
        if w and h:
            if w < 1000:
                s -= 0.35  # small: a phone capture or an old thumbnail
            if not 1.55 <= w / h <= 1.95:
                s -= 0.3   # not widescreen: portrait, ultrawide or 4:3
        return s - i * 0.01  # IGDB's own order breaks ties

    order = sorted(range(len(head)), key=score, reverse=True)
    return [head[i] for i in order] + urls[RANK_LOOK:]


# ------------------------------------------------------------------ putting it together

def order(game_ids: list[int] | None = None, stopped=lambda: False, batch: int = 100) -> int:
    """Order the screenshots of the given games (default: games not done yet). Returns how many were done."""
    if game_ids is not None:
        if not game_ids:
            return 0
        rows = [dict(r) for r in db.query(
            f"SELECT id, steam_appid, igdb_id, igdb_shots, shot_first FROM games WHERE id IN ({','.join('?' * len(game_ids))})",
            tuple(game_ids))]
    else:
        rows = [dict(r) for r in db.query(
            "SELECT id, steam_appid, igdb_id, igdb_shots, shot_first FROM games WHERE shots_at IS NULL LIMIT ?", (batch,))]
    if not rows:
        return 0
    ids = [r["id"] for r in rows]
    marks = ",".join("?" * len(ids))
    gog_ids = {r["game_id"]: r["external_id"] for r in db.query(
        f"SELECT game_id, external_id FROM entries WHERE platform='gog' AND game_id IN ({marks})", tuple(ids))}
    ms_products = {r["game_id"]: r["item_id"] for r in db.query(
        f"SELECT game_id, item_id FROM sub_catalog WHERE service='gamepass' AND game_id IN ({marks})", tuple(ids))}
    xbox_titles = {r["game_id"]: r["external_id"] for r in db.query(
        f"SELECT game_id, external_id FROM entries WHERE platform='xbox' AND game_id IN ({marks}) "
        f"AND external_id GLOB '[0-9]*'", tuple(ids))}
    from_steam, steam_failed = steam([r["steam_appid"] for r in rows if r["steam_appid"]])
    need_rank = [r for r in rows if not (r["steam_appid"] and len(from_steam.get(r["steam_appid"], [])) >= MIN_STORE)
                 and r["id"] not in gog_ids and r["id"] not in ms_products and r["id"] not in xbox_titles]
    sizes = _sizes([r["igdb_id"] for r in need_rank if r["igdb_id"]])
    done = 0
    for r in rows:
        if stopped():
            break
        if r["steam_appid"] in steam_failed:
            continue  # Steam didn't answer: next time
        igdb_list = _loads(r["igdb_shots"])
        urls, src = [], None
        candidates = [
            ("steam", lambda: from_steam.get(r["steam_appid"], []) if r["steam_appid"] else []),
            ("gog", lambda: gog(gog_ids[r["id"]]) if r["id"] in gog_ids else []),
            ("xbox", lambda: microsoft(product_id=ms_products.get(r["id"]), title_id=xbox_titles.get(r["id"]))
             if r["id"] in ms_products or r["id"] in xbox_titles else []),
        ]
        try:
            for name, get in candidates:
                got = get()
                if len(got) >= MIN_STORE or (got and not igdb_list):
                    urls, src = got, name
                    break
            if not urls and igdb_list:
                urls, src = ranked(igdb_list, sizes), "igdb"
        except Unavailable:
            continue
        with db.tx() as c:
            c.execute("UPDATE games SET screenshots=?, shots_src=?, shots_at=? WHERE id=?",
                      (db.set_json(with_pick(urls, r["shot_first"])) if urls else r["igdb_shots"], src, db.now(), r["id"]))
        done += 1
    return done


def _loads(s):
    try:
        v = json.loads(s) if s else []
        return v if isinstance(v, list) else []
    except ValueError:
        return []


def pick_first(game_id: int, url: str | None) -> list[str]:
    """Show this screenshot first (None: back to the automatic order). Returns the new order."""
    rows = db.query("SELECT screenshots, shots_src, igdb_shots FROM games WHERE id=?", (game_id,))
    if not rows:
        return []
    cur = _loads(rows[0]["screenshots"])
    with db.tx() as c:
        if url:
            new = with_pick(cur, url)
            c.execute("UPDATE games SET shot_first=?, screenshots=? WHERE id=?", (key(url), db.set_json(new), game_id))
            return new
        c.execute("UPDATE games SET shot_first=NULL, shots_at=NULL WHERE id=?", (game_id,))
    order([game_id])
    return _loads(db.query("SELECT screenshots FROM games WHERE id=?", (game_id,))[0]["screenshots"])
