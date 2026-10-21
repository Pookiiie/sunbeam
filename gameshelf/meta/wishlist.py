"""Your Steam and PlayStation wishlists: release dates and current prices.

- Steam: IWishlistService/GetWishlist (needs your profile's game details to be
  public, like the library), then IStoreBrowseService/GetItems for each game's
  release date and price in Canadian dollars.
- PlayStation: the PlayStation Store's own wishlist query, signed in with the
  same NPSSO token as your PlayStation library. Each product's release date and
  price come from the store's product queries.

Each game is priced on every store: Steam, PlayStation, GOG and Epic. When a store's wishlist
doesn't have it (GOG and Epic never do: their wishlists aren't read), that store's price is
looked up (Steam by the game's Steam app id, else a Steam store search; the
PlayStation Store, GOG and Epic by their own searches, full games with the same name only),
unless you already own it there. The lowest price is the one shown on the card.

The app only reads the wishlists; it can't change them. Every wishlist game is
linked to the matching game in your library (owned, or on XGP / PS+), else it
gets a game of its own with a "wishlist" copy, so it has details, art, reviews
and time to beat like any other. That copy doesn't count as owning the game and
goes away when the game leaves your wishlist or turns up in your library.
"""

from __future__ import annotations

import datetime
import json
import re
from concurrent.futures import ThreadPoolExecutor

from .. import db, net, settings
from .catalogs import _plain

STORES = {"steam": "Steam", "psn": "PlayStation", "gog": "GOG", "epic": "Epic Games Store"}
STEAM_API = "https://api.steampowered.com"
PS_GRAPHQL = "https://web.np.playstation.com/api/graphql/v1/op"
PS_LOCALE = "en-CA"
# Persisted queries of the PlayStation Store website (Sony changes these hashes now and then).
PS_WISHLIST = ("storeRetrieveWishlist", "1fa88dd6c70279e3d914f7ba535b2558a7e45821d030cb2a63a8e450963d12c3")
PS_PRODUCT = ("metGetProductById", "a128042177bd93dd831164103d53b73ef790d56f51dae647064cb8f9d9fc9d1a")
PS_PRICE = ("productRetrieveForCtasWithPrice", "1f0ca607e170abbfb7d67bd76c9bbc97f21fe2e807be49e5fe764e14566cb605")
PS_SEARCH = ("getSearchResults", "4df6284f982e57bec70f23c77e2c219dc792eb19af7fb3d3a81767aa3f1958aa")
STEAM_SEARCH = "https://store.steampowered.com/api/storesearch/"
GOG_PRICES = "https://api.gog.com/products/prices"
GOG_CATALOG = "https://catalog.gog.com/v1/catalog"
# The Epic launcher's own store service (store.epicgames.com sits behind bot checks; this one
# answers the launcher, which is what Heroic and Playnite's plugins use).
EPIC_GRAPHQL = "https://launcher.store.epicgames.com/graphql"
EPIC_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) EpicGamesLauncher"
RESEARCH_AFTER = 14 * 86400  # a game not found on the other store is searched for again after this long

PLATFORM = "wishlist"  # the platform of a wishlist copy (external_id "steam:<appid>" / "psn:<id>")


class WishlistError(Exception):
    pass


def _table(c) -> None:
    c.execute("""CREATE TABLE IF NOT EXISTS wishlist (
        store TEXT NOT NULL, item_id TEXT NOT NULL, title TEXT, norm_title TEXT, image TEXT, url TEXT,
        added_at INTEGER, priority INTEGER,
        release_ts INTEGER, release_text TEXT, precision TEXT, released INTEGER,
        price_text TEXT, base_text TEXT, price_cents INTEGER, base_cents INTEGER, discount_pct INTEGER,
        sale_ends INTEGER, sale_note TEXT, concept_id TEXT, steam_appid INTEGER,
        first_seen INTEGER, last_seen INTEGER, game_id INTEGER, early_access INTEGER, ea_full_ts INTEGER,
        PRIMARY KEY (store, item_id))""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_wishlist_game ON wishlist(game_id)")
    # The other store's price for a wishlist game (item_id NULL: searched, not found there).
    c.execute("""CREATE TABLE IF NOT EXISTS wish_prices (
        game_id INTEGER NOT NULL, store TEXT NOT NULL, item_id TEXT, url TEXT,
        price_text TEXT, base_text TEXT, price_cents INTEGER, base_cents INTEGER, discount_pct INTEGER,
        sale_ends INTEGER, sale_note TEXT, checked_at INTEGER,
        PRIMARY KEY (game_id, store))""")


# ------------------------------------------------------------------ Steam

def _steam_release(rel: dict) -> dict:
    """Steam's release info as {release_ts, release_text, precision, released}."""
    ts = rel.get("steam_release_date") or None
    if not rel.get("is_coming_soon"):
        return {"release_ts": ts, "release_text": None, "precision": "day" if ts else None, "released": 1}
    shown = rel.get("coming_soon_display") or ""
    custom = (rel.get("custom_release_date_message") or "").strip() or None
    precision = {"date_full": "day", "date_month": "month", "date_quarter": "quarter", "date_year": "year"}.get(shown)
    if not precision or not ts:
        return {"release_ts": None, "release_text": custom or ("To be announced" if shown == "text_tba" else "Coming soon"),
                "precision": None, "released": 0}
    return {"release_ts": ts, "release_text": custom, "precision": precision, "released": 0}


def _steam_ea(item: dict) -> dict:
    """Early access on Steam, with the full release date once the developer announces it."""
    rel = item.get("release") or {}
    ea = bool(item.get("is_early_access") or rel.get("is_early_access"))
    return {"early_access": int(ea), "ea_full_ts": (rel.get("release_from_early_access_date") or None) if ea else None}


def _steam_price(item: dict) -> dict:
    p = item.get("best_purchase_option") or {}
    if not p.get("formatted_final_price"):
        # Free games (The Looker, prologues…) have no purchase option, only is_free.
        return {"price_text": "Free", "price_cents": 0, "base_cents": 0} if item.get("is_free") else {}
    pct = p.get("discount_pct") or 0
    ends = max([d.get("discount_end_date") or 0 for d in p.get("active_discounts") or []] or [0]) or None
    return {
        "price_text": p.get("formatted_final_price"),
        "base_text": p.get("formatted_original_price") if pct else None,
        "price_cents": int(p.get("final_price_in_cents") or 0),
        "base_cents": int(p.get("original_price_in_cents") or p.get("final_price_in_cents") or 0),
        "discount_pct": int(pct) or None,
        "sale_ends": ends if pct else None,
    }


def fetch_steam() -> list[dict]:
    from ..sources import steam
    steam_id = steam.resolve_steam_id()
    data = net.get_json(f"{STEAM_API}/IWishlistService/GetWishlist/v1/", params={"steamid": steam_id}) or {}
    wished = (data.get("response") or {}).get("items") or []
    if not wished:
        return []
    out = []
    for i in range(0, len(wished), 50):
        chunk = wished[i:i + 50]
        req = {"ids": [{"appid": w["appid"]} for w in chunk],
               "context": {"language": "english", "country_code": "CA"},
               "data_request": {"include_release": True, "include_assets": True}}
        info = net.get_json(f"{STEAM_API}/IStoreBrowseService/GetItems/v1/",
                            params={"input_json": json.dumps(req, separators=(",", ":"))}) or {}
        by_id = {it.get("appid") or it.get("id"): it for it in (info.get("response") or {}).get("store_items") or []}
        for w in chunk:
            it = by_id.get(w["appid"]) or {}
            name = (it.get("name") or "").strip()
            if not name:
                continue  # delisted, or not visible in Canada
            assets = it.get("assets") or {}
            img = None
            if assets.get("library_capsule") and assets.get("asset_url_format"):
                img = "https://shared.akamai.steamstatic.com/store_item_assets/" + \
                    assets["asset_url_format"].replace("${FILENAME}", assets["library_capsule"])
            out.append({
                "store": "steam", "item_id": str(w["appid"]), "title": name, "image": img,
                "url": f"https://store.steampowered.com/app/{w['appid']}",
                "added_at": w.get("date_added"), "priority": w.get("priority"), "steam_appid": int(w["appid"]),
                **_steam_release(it.get("release") or {}), **_steam_price(it), **_steam_ea(it),
            })
    return out


# ------------------------------------------------------------------ PlayStation

def _ps_query(op: tuple[str, str], variables: dict, token: str | None = None):
    headers = {"Accept": "application/json", "Content-Type": "application/json",
               "x-apollo-operation-name": op[0], "x-psn-store-locale-override": PS_LOCALE}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return net.get_json(PS_GRAPHQL, params={
        "operationName": op[0],
        "variables": json.dumps(variables, separators=(",", ":")),
        "extensions": json.dumps({"persistedQuery": {"version": 1, "sha256Hash": op[1]}}, separators=(",", ":")),
    }, headers=headers, retries=1) or {}


def _money_cents(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _ps_price(pid: str) -> dict:
    try:
        data = _ps_query(PS_PRICE, {"productId": pid})
    except Exception:
        return {}
    ctas = (((data.get("data") or {}).get("productRetrieve") or {}).get("webctas")) or []
    best, note = None, None
    for cta in ctas:
        p = cta.get("price") or {}
        if p.get("isTiedToSubscription") or p.get("discountedValue") in (None, ""):
            continue  # "Included" with a PS Plus tier: that's the PS+ list's job
        upsell = p.get("applicability") == "UPSELL"
        if upsell and "PS_PLUS" not in (p.get("serviceBranding") or []):
            continue
        if best is None or (p.get("discountedValue") or 0) < (best.get("discountedValue") or 0):
            best, note = p, ("with PS+" if upsell else None)
    if not best:
        return {}
    base, final = _money_cents(best.get("basePriceValue")), _money_cents(best.get("discountedValue"))
    pct = round(100 * (1 - final / base)) if base and final is not None and final < base else None
    ends = None
    if pct and str(best.get("endTime") or "").isdigit():
        ends = int(best["endTime"]) // 1000
    return {"price_text": best.get("discountedPrice") if final else "Free", "base_text": best.get("basePrice") if pct else None,
            "price_cents": final, "base_cents": base, "discount_pct": pct, "sale_ends": ends,
            "sale_note": note if pct else None}


def _ps_release(pid: str) -> dict:
    try:
        data = _ps_query(PS_PRODUCT, {"productId": pid})
    except Exception:
        return {}
    p = ((data.get("data") or {}).get("productRetrieve")) or {}
    raw = p.get("releaseDate")
    if not raw:
        return {}
    try:
        ts = int(datetime.datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return {}
    if ts > db.now() + 3 * 365 * 86400:  # Sony's placeholder dates (Dec 31 2099) mean "not announced"
        return {"release_ts": None, "release_text": "To be announced", "precision": None, "released": 0}
    return {"release_ts": ts, "precision": "day", "released": int(ts <= db.now())}


def fetch_psn() -> list[dict]:
    from ..sources import playstation
    token = playstation._access_token()
    data = _ps_query(PS_WISHLIST, {}, token)
    if data.get("errors") and not (data.get("data") or {}).get("storeWishlistSecure"):
        msg = "; ".join(str(e.get("message") or e) for e in data["errors"][:2])
        raise WishlistError(f"the PlayStation Store didn't share your wishlist ({msg[:160]})")
    rows = (data.get("data") or {}).get("storeWishlistSecure")
    if rows is None:
        raise WishlistError("the PlayStation Store didn't share your wishlist (its answer has changed)")
    items = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        rid, name = str(r.get("id") or "").strip(), (r.get("name") or "").strip()
        if not rid or not name:
            continue
        kind = r.get("__typename") or ("Concept" if rid.isdigit() else "Product")
        img = ((r.get("boxArt") or {}).get("url")) or None
        for m in r.get("media") or []:
            if not img and isinstance(m, dict) and m.get("role") in ("MASTER", "GAMEHUB_COVER_ART", "PORTRAIT_BANNER"):
                img = m.get("url")
        items.append({
            "store": "psn", "item_id": rid, "title": name, "image": img,
            "url": f"https://store.playstation.com/{PS_LOCALE.lower()}/{'concept' if kind == 'Concept' else 'product'}/{rid}",
            "added_at": None, "priority": None, "concept_id": rid if kind == "Concept" else
            (str((r.get("concept") or {}).get("id")) if (r.get("concept") or {}).get("id") else None),
            "_product": kind != "Concept",
        })

    def details(it):
        if not it.pop("_product"):
            return it
        return {**it, **_ps_release(it["item_id"]), **_ps_price(it["item_id"])}

    with ThreadPoolExecutor(max_workers=4) as ex:
        return list(ex.map(details, items))


# ------------------------------------------------------------------ GOG

def _gog_cents(v) -> int | None:
    """GOG's prices: "5999 CAD" (price service) or "59.99" (catalog)."""
    if v in (None, ""):
        return None
    s = str(v).strip().split()[0]
    try:
        return int(s) if s.isdigit() else round(float(s) * 100)
    except ValueError:
        return None


def _gog_prices(ids: list[str]) -> dict[str, dict]:
    """Canadian prices for GOG product ids, from GOG's price service (no sign-in needed)."""
    out: dict[str, dict] = {}
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        try:
            data = net.get_json(GOG_PRICES, params={"ids": ",".join(chunk), "countryCode": "CA", "currency": "CAD"},
                                retries=1) or {}
        except net.HttpError:
            continue
        for item in ((data.get("_embedded") or {}).get("items")) or []:
            pid = str((((item.get("_embedded") or {}).get("product")) or {}).get("id") or "")
            prices = [p for p in (item.get("_embedded") or {}).get("prices") or []
                      if ((p.get("currency") or {}).get("code") or "CAD") == "CAD"]
            if not pid or not prices:
                continue
            p = prices[0]
            final, base = _gog_cents(p.get("finalPrice")), _gog_cents(p.get("basePrice"))
            if final is None:
                continue
            pct = round(100 * (1 - final / base)) if base and final < base else None
            out[pid] = {"price_text": _money(final), "base_text": _money(base) if pct else None,
                        "price_cents": final, "base_cents": base if base is not None else final, "discount_pct": pct}
    return out


def _gog_find(title: str) -> dict | None:
    """A full game on GOG with this name, and its Canadian price (GOG's store search)."""
    data = net.get_json(GOG_CATALOG, params={"limit": 20, "query": f"like:{_plain(title)}", "order": "desc:score",
                                             "productType": "in:game,pack", "page": 1, "countryCode": "CA",
                                             "currencyCode": "CAD", "locale": "en-US"}, retries=1) or {}
    want = _key(title)
    for p in data.get("products") or []:
        if p.get("productType") != "game" or _key(p.get("title")) != want:
            continue
        price = p.get("price") or {}
        money = price.get("finalMoney") or {}
        if money.get("currency") and money.get("currency") != "CAD":
            return {"id": str(p["id"]), "slug": p.get("slug"), "link": p.get("storeLink")}
        final = _gog_cents(money.get("amount"))
        base = _gog_cents((price.get("baseMoney") or {}).get("amount"))
        pct = round(100 * (1 - final / base)) if final is not None and base and final < base else None
        return {"id": str(p["id"]), "slug": p.get("slug"), "link": p.get("storeLink"),
                "price": None if final is None else {"price_text": _money(final), "base_text": _money(base) if pct else None,
                                                     "price_cents": final, "base_cents": base if base is not None else final,
                                                     "discount_pct": pct}}
    return None


# ------------------------------------------------------------------ Epic

EPIC_PRICE = """price(country: $country) { totalPrice { discountPrice originalPrice currencyCode currencyInfo { decimals } }
  lineOffers { appliedRules { endDate } } }"""
EPIC_SEARCH_Q = """query searchStoreQuery($keywords: String, $country: String!, $allowCountries: String, $locale: String,
  $count: Int, $category: String) {
  Catalog { searchStore(keywords: $keywords, country: $country, allowCountries: $allowCountries, locale: $locale,
    count: $count, category: $category) { elements { title id namespace offerType productSlug urlSlug
      catalogNs { mappings(pageType: "productHome") { pageSlug pageType } } offerMappings { pageSlug pageType }
      """ + EPIC_PRICE + """ } } } }"""


def _epic_gql(query: str, variables: dict, token: str | None = None) -> dict:
    headers = {"User-Agent": EPIC_UA, "Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"bearer {token}"
    resp = net.request("POST", EPIC_GRAPHQL, json_body={"query": query, "variables": variables}, headers=headers, retries=1)
    if "json" not in (resp.headers.get("Content-Type") or "").lower():
        raise WishlistError("the Epic store didn't answer (it may be checking for bots); trying again at the next sync")
    return resp.json() or {}


def _epic_slug(o: dict) -> str | None:
    for m in ((o.get("catalogNs") or {}).get("mappings") or []) + (o.get("offerMappings") or []):
        if m.get("pageSlug"):
            return m["pageSlug"]
    return o.get("productSlug") or o.get("urlSlug")


def _epic_price(o: dict) -> dict:
    tp = ((o.get("price") or {}).get("totalPrice")) or {}
    if tp.get("discountPrice") is None or (tp.get("currencyCode") or "CAD") != "CAD":
        return {}
    d = int((tp.get("currencyInfo") or {}).get("decimals") or 2)
    final = round(int(tp["discountPrice"]) * 100 / 10 ** d)
    base = round(int(tp.get("originalPrice") or tp["discountPrice"]) * 100 / 10 ** d)
    pct = round(100 * (1 - final / base)) if base and final < base else None
    ends = None
    if pct:
        for line in (o.get("price") or {}).get("lineOffers") or []:
            for rule in line.get("appliedRules") or []:
                e = _iso_ts(rule.get("endDate"))
                ends = max(ends or 0, e or 0) or None
    return {"price_text": _money(final), "base_text": _money(base) if pct else None, "price_cents": final,
            "base_cents": base, "discount_pct": pct, "sale_ends": ends}


def _iso_ts(s) -> int | None:
    if not s:
        return None
    try:
        return int(datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _epic_find(title: str) -> dict | None:
    d = _epic_gql(EPIC_SEARCH_Q, {"keywords": _plain(title), "country": "CA", "allowCountries": "CA", "locale": "en-CA",
                                  "count": 10, "category": "games/edition/base"})
    want = _key(title)
    for o in ((((d.get("data") or {}).get("Catalog") or {}).get("searchStore")) or {}).get("elements") or []:
        if _key(o.get("title")) == want and (o.get("offerType") in (None, "BASE_GAME")):
            slug = _epic_slug(o)
            return {"id": o.get("id"), "url": f"https://store.epicgames.com/en-US/p/{slug}" if slug else None,
                    "price": _epic_price(o)}
    return None


# ------------------------------------------------------------------ the other store's price

def _key(title: str) -> str:
    """Title key for matching store listings: no edition/platform noise, no trailing year."""
    t = db.normalize_title(_plain(title or ""))
    return re.sub(r"\s*\(?\b(?:19|20)\d\d\)?\s*$", "", t).strip()


def _steam_items(appids: list[int]) -> dict[int, dict]:
    req = {"ids": [{"appid": a} for a in appids], "context": {"language": "english", "country_code": "CA"},
           "data_request": {}}
    info = net.get_json(f"{STEAM_API}/IStoreBrowseService/GetItems/v1/",
                        params={"input_json": json.dumps(req, separators=(",", ":"))}) or {}
    return {it.get("appid") or it.get("id"): it for it in (info.get("response") or {}).get("store_items") or []}


def _steam_find(title: str) -> int | None:
    """A game on the Steam store by name (exact match once editions and years are set aside)."""
    data = net.get_json(STEAM_SEARCH, params={"term": _plain(title), "l": "english", "cc": "CA"}, retries=1) or {}
    want = _key(title)
    for it in data.get("items") or []:
        if it.get("type") == "app" and _key(it.get("name")) == want:
            return int(it["id"])
    return None


def _ps_find(title: str) -> list[str]:
    """PlayStation Store products that are the full game with this name (PS4 and PS5 versions)."""
    data = _ps_query(PS_SEARCH, {"countryCode": "CA", "languageCode": "en", "nextCursor": "", "pageOffset": 0,
                                 "pageSize": 20, "searchTerm": _plain(title)})
    results = (((data.get("data") or {}).get("universalSearch")) or {}).get("results") or []
    want = _key(title)
    return [r["id"] for r in results if isinstance(r, dict) and r.get("__typename") == "Product"
            and r.get("storeDisplayClassification") == "FULL_GAME" and _key(r.get("name")) == want][:3]


def _other_steam(g: dict, prev: dict) -> dict:
    appid = g["steam_appid"] or (int(prev["item_id"]) if prev.get("item_id") else None) or _steam_find(g["title"])
    if not appid:
        return {"item_id": None}
    it = _steam_items([appid]).get(appid) or {}
    return {"item_id": str(appid), "url": f"https://store.steampowered.com/app/{appid}", **_steam_price(it)}


def _other_psn(g: dict, prev: dict) -> dict:
    ids = [prev["item_id"]] if prev.get("item_id") else []
    priced = [(pid, _ps_price(pid)) for pid in ids]
    if not any(p for _, p in priced):  # nothing remembered, or it stopped answering: search again
        priced = [(pid, _ps_price(pid)) for pid in _ps_find(g["title"])]
    priced = [(pid, p) for pid, p in priced if p.get("price_cents") is not None]
    if not priced:
        return {"item_id": None}
    pid, p = min(priced, key=lambda x: x[1]["price_cents"])
    return {"item_id": pid, "url": f"https://store.playstation.com/{PS_LOCALE.lower()}/product/{pid}", **p}


def _other_gog(g: dict, prev: dict) -> dict:
    if prev.get("item_id"):  # found before: just its price now
        p = _gog_prices([prev["item_id"]]).get(prev["item_id"])
        if p:
            return {"item_id": prev["item_id"], "url": prev.get("url"), **p}
    hit = _gog_find(g["title"])
    if not hit:
        return {"item_id": None}
    url = (f"https://www.gog.com{hit['link']}" if (hit.get("link") or "").startswith("/") else hit.get("link")) \
        or f"https://www.gog.com/en/game/{hit.get('slug') or hit['id']}"
    return {"item_id": hit["id"], "url": url, **(hit.get("price") or {})}


def _other_epic(g: dict, prev: dict) -> dict:
    hit = _epic_find(g["title"])
    if not hit:
        return {"item_id": None}
    return {"item_id": hit["id"], "url": hit.get("url"), **(hit.get("price") or {})}


OTHER = {"steam": _other_steam, "psn": _other_psn, "gog": _other_gog, "epic": _other_epic}
OWN_PLATFORM = {"steam": "steam", "psn": "playstation", "gog": "gog", "epic": "epic"}
PRICE_FIELDS = ("item_id", "url", "price_text", "base_text", "price_cents", "base_cents", "discount_pct", "sale_ends", "sale_note")


def other_prices() -> int:
    """Price every wishlist game on the store whose wishlist doesn't have it (skipped where
    you own it). Returns how many games have a price there."""
    with db.tx() as c:
        _table(c)
    wished: dict[int, set] = {}
    for r in db.query("SELECT game_id, store FROM wishlist WHERE game_id IS NOT NULL"):
        wished.setdefault(r["game_id"], set()).add(r["store"])
    if not wished:
        return 0
    ids = ",".join(str(int(g)) for g in wished)
    owned: dict[int, set] = {}
    for r in db.query(f"""SELECT game_id, platform FROM entries WHERE game_id IN ({ids})
                          AND COALESCE(ownership, '') NOT IN ('catalog', 'wishlist', 'game_pass', 'ps_plus')"""):
        owned.setdefault(r["game_id"], set()).add(r["platform"])
    games = {r["id"]: dict(r) for r in db.query(f"SELECT id, title, steam_appid FROM games WHERE id IN ({ids})")}
    prev = {(r["game_id"], r["store"]): dict(r) for r in db.query("SELECT * FROM wish_prices")}
    now = db.now()
    jobs = [(gid, store) for gid, stores in wished.items() if gid in games for store in OTHER
            if store not in stores and OWN_PLATFORM[store] not in owned.get(gid, ())]

    def look(job):
        gid, store = job
        p = prev.get(job) or {}
        if p and not p.get("item_id") and now - (p.get("checked_at") or 0) < RESEARCH_AFTER:
            return job, None  # not on that store at the last look: keep that for a while
        try:
            return job, OTHER[store](games[gid], p)
        except Exception:
            return job, None  # the store didn't answer: keep what we had

    with ThreadPoolExecutor(max_workers=4) as ex:
        found = list(ex.map(look, jobs))
    keep = set(jobs)
    with db.tx() as c:
        for (gid, store), res in found:
            if res is None:
                continue
            vals = [res.get(f) for f in PRICE_FIELDS]
            c.execute(f"""INSERT OR REPLACE INTO wish_prices(game_id, store, {', '.join(PRICE_FIELDS)}, checked_at)
                          VALUES (?, ?, {', '.join('?' * len(PRICE_FIELDS))}, ?)""", (gid, store, *vals, now))
        for gid, store in prev:
            if (gid, store) not in keep:  # off your wishlists, bought there, or now on that store's wishlist
                c.execute("DELETE FROM wish_prices WHERE game_id=? AND store=?", (gid, store))
    return db.query("SELECT COUNT(*) n FROM wish_prices WHERE price_cents IS NOT NULL")[0]["n"]


# ------------------------------------------------------------------ storing and linking

FIELDS = ("title", "norm_title", "image", "url", "added_at", "priority", "release_ts", "release_text", "precision",
          "released", "price_text", "base_text", "price_cents", "base_cents", "discount_pct", "sale_ends", "sale_note",
          "concept_id", "steam_appid", "early_access", "ea_full_ts")


def _store(store: str, items: list[dict]) -> int:
    now = db.now()
    with db.tx() as c:
        _table(c)
        for it in items:
            it = {**it, "norm_title": db.normalize_title(_plain(it["title"]))}
            vals = [it.get(f) for f in FIELDS]
            c.execute(f"""INSERT INTO wishlist(store, item_id, {', '.join(FIELDS)}, first_seen, last_seen)
                          VALUES (?, ?, {', '.join('?' * len(FIELDS))}, ?, ?)
                          ON CONFLICT(store, item_id) DO UPDATE SET {', '.join(f'{f}=excluded.{f}' for f in FIELDS)},
                            last_seen=excluded.last_seen""",
                      (store, it["item_id"], *vals, now, now))
        c.execute("DELETE FROM wishlist WHERE store=? AND last_seen < ?", (store, now))
    return len(items)


def _year(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).year if ts else None


def link() -> None:
    """Point each wishlist game at the matching library game, else at a game of its own."""
    owned = f"(SELECT game_id FROM entries WHERE {db.NOT_WISHLIST})"
    with db.tx() as c:
        _table(c)
        # Library games (yours, or on XGP / PS+) by title: their own name first, then their copies' names.
        # Two games with one name (Dead Space 2008 and 2023) are told apart by release year.
        keys: dict[str, list[tuple[int, int | None]]] = {}
        rows = c.execute(f"""SELECT g.id, g.title, g.release_date, e.name FROM games g JOIN entries e ON e.game_id = g.id
                             WHERE {db.NOT_WISHLIST.replace('ownership', 'e.ownership')} ORDER BY g.id""").fetchall()
        for field in ("title", "name"):
            for r in rows:
                k = db.normalize_title(_plain(r[field] or ""))
                if k and r["id"] not in [x[0] for x in keys.get(k, [])]:
                    keys.setdefault(k, []).append((r["id"], _year(r["release_date"])))

        def by_title(title, ts):
            found = keys.get(db.normalize_title(_plain(title or ""))) or []
            if len(found) > 1 and ts:
                for gid, yr in found:
                    if yr and abs(yr - _year(ts)) <= 1:
                        return gid
            return found[0][0] if found else None
        for r in c.execute("SELECT * FROM wishlist").fetchall():
            gid = None
            if r["steam_appid"]:
                g = c.execute(f"SELECT id FROM games WHERE steam_appid=? AND id IN {owned} LIMIT 1", (r["steam_appid"],)).fetchone()
                gid = g and g["id"]
            if not gid and r["concept_id"]:
                g = c.execute(f"SELECT game_id FROM entries WHERE platform='playstation' AND external_id=? AND {db.NOT_WISHLIST} LIMIT 1",
                              (r["concept_id"],)).fetchone()
                gid = g and g["game_id"]
            if not gid:
                gid = by_title(r["title"], r["release_ts"])
            ext = f"{r['store']}:{r['item_id']}"
            if gid:
                c.execute("DELETE FROM entries WHERE platform=? AND external_id=?", (PLATFORM, ext))
            else:
                gid = db.upsert_entry(c, platform=PLATFORM, external_id=ext, name=_plain(r["title"]) or r["title"],
                                      ownership="wishlist", steam_appid=r["steam_appid"], psn_image=r["image"])
            c.execute("UPDATE wishlist SET game_id=? WHERE store=? AND item_id=?", (gid, r["store"], r["item_id"]))
            if r["steam_appid"]:
                # The Steam page you wishlisted is the game's Steam version (for reviews and art), unless
                # you own a Steam copy. IGDB's store links sometimes point at another app (an edition, a DLC).
                c.execute("""UPDATE games SET steam_appid=?, reviews_at=NULL,
                               art_at=CASE WHEN steam_appid IS NULL THEN art_at ELSE NULL END
                             WHERE id=? AND COALESCE(steam_appid, 0) <> ?
                             AND id NOT IN (SELECT game_id FROM entries WHERE platform='steam')""",
                          (r["steam_appid"], gid, r["steam_appid"]))
        # Games that left your wishlists lose their wishlist copy.
        c.execute(f"""DELETE FROM entries WHERE platform=? AND external_id NOT IN
                      (SELECT store || ':' || item_id FROM wishlist)""", (PLATFORM,))
        db.delete_orphans(c)


def configured() -> dict[str, bool]:
    return {"steam": bool(settings.get("steam_id")), "psn": bool(settings.get("psn_npsso"))}


FETCH = {"steam": lambda: fetch_steam(), "psn": lambda: fetch_psn()}


def refresh() -> dict:
    """Update every wishlist. Each one fails on its own without stopping the others."""
    out: dict = {}
    on = configured()
    for store, fn in FETCH.items():
        if not on[store]:
            continue
        try:
            items = fn()
            if not items and store == "steam":
                out[store] = {"count": 0, "note": "empty, or your Steam profile's game details aren't public"}
                # An empty answer is also what a private profile gives: keep what we had.
                continue
            out[store] = {"count": _store(store, items)}
        except Exception as e:
            out[store] = {"error": str(e)}
    link()
    try:
        out["other_store"] = {"count": other_prices()}
    except Exception as e:
        out["other_store"] = {"error": str(e)}
    db.set_meta("wishlist_at", str(db.now()))
    return out


# ------------------------------------------------------------------ reading

def by_game() -> dict[int, dict]:
    """{game_id: summary} for every game on a wishlist (see _summary)."""
    try:
        rows = [dict(r) for r in db.query("SELECT * FROM wishlist WHERE game_id IS NOT NULL")]
    except Exception:
        return {}
    grouped: dict[int, list[dict]] = {}
    for r in rows:
        grouped.setdefault(r["game_id"], []).append(r)
    # Where you own it: not copies played through XGP or PS+, and not Xbox at all (Xbox's play
    # history looks the same for games you bought and games you played on XGP that have since left).
    owned = {}
    for r in db.query(f"""SELECT game_id, GROUP_CONCAT(DISTINCT platform) p FROM entries
                         WHERE COALESCE(ownership, '') NOT IN ('catalog', 'wishlist', 'game_pass', 'ps_plus')
                         AND platform <> 'xbox' AND NOT COALESCE(excluded, 0)
                         AND game_id IN (SELECT game_id FROM wishlist) GROUP BY game_id"""):
        owned[r["game_id"]] = sorted((r["p"] or "").split(","))
    others: dict[int, list[dict]] = {}
    try:
        for r in db.query("SELECT * FROM wish_prices"):
            others.setdefault(r["game_id"], []).append(dict(r))
    except Exception:
        pass
    return {gid: _summary(rs, owned.get(gid, []), others.get(gid, [])) for gid, rs in grouped.items()}


def _cents(text) -> int | None:
    """ "C$ 1,299.99" / "$79.99" -> 129999 / 7999; "Free" -> 0."""
    if not text:
        return None
    if str(text).strip().lower() == "free":
        return 0
    m = re.search(r"\d[\d,]*(?:\.\d{1,2})?", str(text))
    return round(float(m.group(0).replace(",", "")) * 100) if m else None


def _money(cents) -> str | None:
    if cents is None:
        return None
    return "Free" if cents == 0 else f"C${cents / 100:,.2f}"


def _summary(rows: list[dict], owned_on: list[str], others: list[dict] | None = None) -> dict:
    now = db.now()
    out = [r for r in rows if r["released"] == 1]
    soon = [r for r in rows if r["released"] != 1 and r["release_ts"]]
    if out:  # out somewhere: the earliest store release (you could buy it then)
        r = min(out, key=lambda x: x["release_ts"] or now)
        rel = {"release_ts": r["release_ts"], "precision": r["precision"] or ("day" if r["release_ts"] else None),
               "released": True, "release_text": None}
    elif soon:
        prec_rank = {"day": 0, "month": 1, "quarter": 2, "year": 3}
        r = min(soon, key=lambda x: (x["release_ts"], prec_rank.get(x["precision"], 4)))
        rel = {"release_ts": r["release_ts"], "precision": r["precision"], "released": False, "release_text": r["release_text"]}
    else:
        texts = [r["release_text"] for r in rows if r["release_text"]]
        rel = {"release_ts": None, "precision": None, "released": None if any(r["released"] is None for r in rows) else False,
               "release_text": texts[0] if texts else None}
    others = others or []
    for r in rows + others:
        # A sale that ended since the last sync: back to the usual price until the next one.
        if r["discount_pct"] and r["sale_ends"] and r["sale_ends"] < now:
            r.update(price_text=r["base_text"] or r["price_text"], price_cents=r["base_cents"] or r["price_cents"],
                     base_text=None, discount_pct=None, sale_ends=None, sale_note=None)
    stores = [{
        "store": r["store"], "url": r["url"], "added_at": r["added_at"] or r["first_seen"], "price_text": r["price_text"],
        "base_text": r["base_text"], "discount_pct": r["discount_pct"], "sale_ends": r["sale_ends"], "sale_note": r["sale_note"],
        "release_ts": r["release_ts"], "release_text": r["release_text"], "precision": r["precision"], "released": r["released"],
    } for r in sorted(rows, key=lambda x: x["store"])]
    # Every price we know, cheapest first: the wishlists' own and the other store's ("wished": False).
    # Both stores are asked for Canadian prices; written the same way ("C$38.99") so they compare at a glance.
    # A store where you own it shows "Owned" on the game page instead of a price, and isn't the card's price.
    owned_stores = {st for st, plat in OWN_PLATFORM.items() if plat in owned_on}
    prices = []
    for wished, rs in ((True, rows), (False, others)):
        for r in rs:
            if not r["price_text"] or r["store"] in owned_stores:
                continue
            cents = r["price_cents"] if r["price_cents"] is not None else _cents(r["price_text"])
            base = (r["base_cents"] if r["base_cents"] is not None else _cents(r["base_text"])) if r["discount_pct"] else None
            prices.append({"store": r["store"], "wished": wished, "url": r["url"], "price_cents": cents,
                           "price_text": _money(cents) or r["price_text"], "base_text": (_money(base) or r["base_text"]) if base else None,
                           "discount_pct": r["discount_pct"], "sale_ends": r["sale_ends"], "sale_note": r["sale_note"]})
    # Same price on both: the store whose wishlist has it; on both wishlists, a pick that's random
    # but steady for each game (so it doesn't flip with every sync).
    coin = (rows[0].get("game_id") or 0) % 2 if rows else 0
    prices.sort(key=lambda x: (x["price_cents"] if x["price_cents"] is not None else 1 << 40, not x["wished"],
                               (x["store"] == "steam") == bool(coin)))
    best = prices[0] if prices else None
    missing = sorted({r["store"] for r in others if not r["item_id"]})  # looked for, not on that store
    return {**rel, "stores": stores, "added_at": min(s["added_at"] or now for s in stores),
            "prices": prices, "best": best, "missing": missing,
            "early_access": any(r.get("early_access") for r in rows),
            "ea_full_ts": max((r.get("ea_full_ts") or 0 for r in rows), default=0) or None,
            # On sale: when the lowest price is a sale price
            "sale": best if best and best["discount_pct"] else None, "owned_on": owned_on}


def test() -> str:
    """Read the wishlists without saving anything (for checking the connection)."""
    on = configured()
    parts = []
    for store, fn in FETCH.items():
        if not on[store]:
            continue
        try:
            items = fn()
            sale = sum(1 for i in items if i.get("discount_pct"))
            soon = sum(1 for i in items if i.get("released") == 0)
            parts.append(f"{STORES[store]}: {len(items)} games ({soon} not out yet, {sale} on sale)")
        except Exception as e:
            parts.append(f"{STORES[store]}: couldn't read it ({e})")
    if not parts:
        raise WishlistError("Set up Steam or PlayStation in Settings first.")
    return ". ".join(parts) + "."
