"""What's on PC Game Pass and PlayStation Plus right now, and what's new or leaving.

- PC Game Pass: Microsoft's public Game Pass lists (the ones xbox.com uses):
  every PC game, plus the official "Leaving soon" list. Titles, art and Xbox
  title IDs come from Microsoft's store catalog.
- PS+ Extra: the Game Catalog feed behind playstation.com's games page (no
  Game Trials or Premium Classics), with the same concept IDs as your
  PlayStation library.

"Recently added" means the app first saw the game in the list in the last 30
days. The first update only sets the starting point, so nothing is "new" until
the lists change. Sony doesn't publish a "leaving soon" list, so that tag is
Game Pass only.

Each game in a list is linked to the matching game in your library (by its
Xbox or PlayStation ID, or else by title), so library games get tags.
"""

from __future__ import annotations

import html
import re
from concurrent.futures import ThreadPoolExecutor

from .. import db, net

MARKET, LANG = "CA", "en-us"
PS_LOCALE = "en-ca"
SIGLS = "https://catalog.gamepass.com/sigls/v2"
GP_PC_ALL = "fdd9e2a7-0fee-49f6-ad69-4354098401ff"
GP_PC_LEAVING = "cc7fc951-d00f-410e-9e02-5e4628e04163"
DISPLAY = "https://displaycatalog.mp.microsoft.com/v7.0/products"
PS_GAMES_FEED = "https://www.playstation.com/bin/imagic/gameslist"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

NEW_DAYS = 30
SERVICES = {"gamepass": "XGP", "psplus": "PS+"}  # XGP = Xbox PC Game Pass (the user's shorthand)


# ------------------------------------------------------------------ fetching

def _sigl(list_id: str) -> list[str]:
    data = net.get_json(SIGLS, params={"id": list_id, "language": LANG, "market": MARKET},
                        headers={"User-Agent": UA}) or []
    return [x["id"] for x in data if isinstance(x, dict) and x.get("id")]


def _products(ids: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}

    def batch(chunk):
        try:
            return net.get_json(DISPLAY, params={"bigIds": ",".join(chunk), "market": MARKET, "languages": LANG},
                                headers={"User-Agent": UA}) or {}
        except Exception:
            return {}

    chunks = [ids[i:i + 20] for i in range(0, len(ids), 20)]
    with ThreadPoolExecutor(max_workers=4) as ex:
        for data in ex.map(batch, chunks):
            for p in data.get("Products") or []:
                lp = (p.get("LocalizedProperties") or [{}])[0]
                imgs = {}
                for im in lp.get("Images") or []:
                    imgs.setdefault(im.get("ImagePurpose"), im.get("Uri"))
                img = imgs.get("Poster") or imgs.get("BoxArt")
                if img and img.startswith("//"):
                    img = "https:" + img
                xbox_id = next((a.get("Value") for a in p.get("AlternateIds") or [] if a.get("IdType") == "XboxTitleId"), None)
                out[p.get("ProductId")] = {"title": lp.get("ProductTitle"), "image": img, "xbox_title_id": xbox_id}
    return out


def fetch_gamepass() -> list[dict]:
    ids = _sigl(GP_PC_ALL)
    leaving = set(_sigl(GP_PC_LEAVING))
    known = {r["item_id"]: dict(r) for r in db.query(
        "SELECT item_id, title, image, xbox_title_id FROM sub_catalog WHERE service='gamepass' AND title IS NOT NULL")}
    info = _products([i for i in ids if i not in known])
    items = []
    for pid in ids:
        d = known.get(pid) or info.get(pid)
        if not d or not d.get("title"):
            continue
        items.append({"item_id": pid, "title": d["title"], "image": d.get("image"),
                      "xbox_title_id": d.get("xbox_title_id"), "concept_id": None,
                      "leaving": pid in leaving, "url": f"https://www.xbox.com/en-CA/games/store/x/{pid}"})
    return items


def fetch_psplus() -> list[dict]:
    """The PS+ Extra Game Catalog, from the feed playstation.com's own "PlayStation Plus games" page reads.

    Only "plus-games-list" (the Game Catalog). The same feed also has Game Trials
    (time-limited demos), Classics (Premium only) and the monthly games: none of
    those are part of PS+ Extra, so they're left out."""
    data = net.get_json(PS_GAMES_FEED, params={"locale": PS_LOCALE, "categoryList": "plus-games-list"},
                        headers={"User-Agent": UA, "Accept": "application/json"})
    groups = data if isinstance(data, list) else (data or {}).get("plus-games-list") or []
    items, seen = [], set()
    for grp in groups:
        for gm in (grp or {}).get("games") or []:
            concept = str(gm.get("conceptId") or "").strip()
            title = html.unescape(gm.get("nameEn") or gm.get("name") or "").strip()
            if not concept or not title or concept in seen:
                continue
            seen.add(concept)
            img = gm.get("imageUrl") or None
            if img and img.startswith("/"):
                img = "https://www.playstation.com" + img
            url = gm.get("conceptUrl") or f"https://store.playstation.com/{PS_LOCALE}/concept/{concept}"
            items.append({"item_id": concept, "title": title, "image": img, "xbox_title_id": None,
                          "concept_id": concept, "leaving": False, "url": url})
    return items


# ------------------------------------------------------------------ storing

def _store(service: str, items: list[dict]) -> dict:
    now = db.now()
    with db.tx() as c:
        baseline = c.execute("SELECT value FROM meta WHERE key=?", (f"catalog_baseline_{service}",)).fetchone()
        if not baseline:
            c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (f"catalog_baseline_{service}", str(now)))
        for it in items:
            c.execute(
                """INSERT INTO sub_catalog(service, item_id, title, norm_title, image, xbox_title_id, concept_id, url,
                                           leaving, first_seen, last_seen)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(service, item_id) DO UPDATE SET title=excluded.title, norm_title=excluded.norm_title,
                     image=COALESCE(excluded.image, sub_catalog.image), xbox_title_id=COALESCE(excluded.xbox_title_id,
                     sub_catalog.xbox_title_id), url=excluded.url, leaving=excluded.leaving, last_seen=excluded.last_seen""",
                (service, it["item_id"], it["title"], db.normalize_title(_plain(it["title"])), it.get("image"),
                 it.get("xbox_title_id"), it.get("concept_id"), it.get("url"), int(bool(it.get("leaving"))), now, now))
        gone = c.execute("SELECT COUNT(*) n FROM sub_catalog WHERE service=? AND last_seen < ?", (service, now)).fetchone()["n"]
        c.execute("DELETE FROM sub_catalog WHERE service=? AND last_seen < ?", (service, now))
    link(service)
    return {"count": len(items), "left": gone}


def _plain(title: str) -> str:
    """Store titles carry edition and platform noise: "Forza Horizon 5 Standard Edition (PC)",
    "Dead Cells (PS4® & PS5®)", "Cities: Skylines - PlayStation®4 Edition"."""
    t = db.strip_pc_tags(re.sub(r"[®™©]", "", title or ""))
    t = re.sub(r"\s*\((?:xbox[^)]*|ps4|ps5|ps4\s*(?:&|and)\s*ps5|playstation\s*plus)\)", "", t, flags=re.I)
    t = re.sub(r"\s*[-–:]?\s*\b(?:ps4\s*(?:&|and)\s*ps5|ps5\s*(?:&|and)\s*ps4)\s*$", "", t, flags=re.I)
    t = re.sub(r"\s*[-–:]\s*playstation\s*[45]\s*edition\s*$", "", t, flags=re.I)
    t = re.sub(r"\s*[-–:]?\s*(?:standard|game of the year|goty|deluxe|definitive|complete|digital|collector[’']?s)\s+edition\s*$", "", t, flags=re.I)
    t = re.sub(r"\s+(?:for\s+)?(?:ps4|ps5)\s*$", "", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip(" -–:")


# Games on a list that aren't in your library get a game of their own, so they have
# details, art, reviews and time to beat like any other. Their copy is a "catalog"
# entry: it doesn't count as owning the game, and goes away when the game leaves the
# list or turns up in your library.
CATALOG_PLATFORM = {"gamepass": "xbox", "psplus": "playstation"}
CATALOG_PREFIX = {"gamepass": "xgp:", "psplus": "psplus:"}


def link(service: str | None = None) -> None:
    """Point each list entry at the matching library game, else at a game of its own."""
    services = [service] if service else list(SERVICES)
    owned = f"(SELECT game_id FROM entries WHERE {db.OWNED})"
    with db.tx() as c:
        for svc in services:
            c.execute("UPDATE sub_catalog SET game_id=NULL WHERE service=?", (svc,))
            if svc == "gamepass":
                c.execute("""UPDATE sub_catalog SET game_id=(SELECT e.game_id FROM entries e
                               WHERE e.platform='xbox' AND e.external_id=sub_catalog.xbox_title_id LIMIT 1)
                             WHERE service='gamepass' AND xbox_title_id IS NOT NULL""")
            if svc == "psplus":
                c.execute("""UPDATE sub_catalog SET game_id=(SELECT e.game_id FROM entries e
                               WHERE e.platform='playstation' AND e.external_id=sub_catalog.concept_id LIMIT 1)
                             WHERE service='psplus' AND concept_id IS NOT NULL""")
            # By title: against each owned game's name and the names of its copies, with
            # "PC" / "Windows" tags and editions ignored on both sides.
            keys: dict[str, int] = {}
            rows = c.execute(f"""SELECT g.id, g.title, e.name FROM games g JOIN entries e ON e.game_id = g.id
                                 WHERE {db.OWNED.replace('ownership', 'e.ownership')} ORDER BY g.id""").fetchall()
            for field in ("title", "name"):  # a game's own name wins over the names of its copies
                for r in rows:
                    k = db.normalize_title(_plain(r[field] or ""))
                    if k:
                        keys.setdefault(k, r["id"])
            for r in c.execute("SELECT item_id, title FROM sub_catalog WHERE service=? AND game_id IS NULL",
                               (svc,)).fetchall():
                k = db.normalize_title(_plain(r["title"]))
                c.execute("UPDATE sub_catalog SET norm_title=?, game_id=? WHERE service=? AND item_id=?",
                          (k, keys.get(k), svc, r["item_id"]))
            plat, pre = CATALOG_PLATFORM[svc], CATALOG_PREFIX[svc]
            # Your own copy turned up (or the game left the list): the catalog copy goes.
            c.execute(f"""DELETE FROM entries WHERE ownership='catalog' AND platform=? AND external_id LIKE ? AND (
                            substr(external_id, ?) NOT IN (SELECT item_id FROM sub_catalog WHERE service=?)
                            OR substr(external_id, ?) IN (SELECT item_id FROM sub_catalog WHERE service=? AND game_id IS NOT NULL))""",
                      (plat, pre + "%", len(pre) + 1, svc, len(pre) + 1, svc))
            for r in c.execute("SELECT item_id, title, image FROM sub_catalog WHERE service=? AND game_id IS NULL",
                               (svc,)).fetchall():
                gid = db.upsert_entry(c, platform=plat, external_id=pre + r["item_id"], name=_plain(r["title"]) or r["title"],
                                      ownership="catalog", psn_image=r["image"])
                c.execute("UPDATE sub_catalog SET game_id=? WHERE service=? AND item_id=?", (gid, svc, r["item_id"]))
        # A game that has your own copy needs no catalog copy (e.g. after two games were combined).
        c.execute(f"DELETE FROM entries WHERE ownership='catalog' AND game_id IN {owned}")
        db.delete_orphans(c)


def refresh() -> dict:
    """Update both lists. Each one fails on its own without stopping the other."""
    out = {}
    for svc, fn in (("gamepass", fetch_gamepass), ("psplus", fetch_psplus)):
        try:
            items = fn()
            if len(items) < 50:  # a broken page, not a real catalog: keep what we had
                raise RuntimeError(f"only {len(items)} games found")
            out[svc] = _store(svc, items)
        except Exception as e:
            out[svc] = {"error": str(e)}
    db.set_meta("catalogs_at", str(db.now()))
    return out


# ------------------------------------------------------------------ reading

def tags_by_game() -> dict[int, list[dict]]:
    """{game_id: [{"s": "gamepass", "new": bool, "leaving": bool}]} for library games."""
    out: dict[int, list[dict]] = {}
    for r in _rows("WHERE game_id IS NOT NULL"):
        out.setdefault(r["game_id"], []).append(_tag(r))
    return out


def store_links(game_id: int) -> list[dict]:
    """Store pages of a game's XGP / PS+ listings, for its details page."""
    return [{"s": r["service"], "url": r["url"]} for r in db.query(
        "SELECT service, url FROM sub_catalog WHERE game_id=? AND url IS NOT NULL", (game_id,))]


def _rows(where: str):
    base = {r["key"]: int(r["value"]) for r in db.query("SELECT key, value FROM meta WHERE key LIKE 'catalog_baseline_%'")}
    rows = [dict(r) for r in db.query(f"SELECT * FROM sub_catalog {where}")]
    for r in rows:
        r["_base"] = base.get(f"catalog_baseline_{r['service']}", 0)
    return rows


def _tag(r: dict) -> dict:
    recent = r["first_seen"] > r["_base"] + 3600 and r["first_seen"] >= db.now() - NEW_DAYS * 86400
    return {"s": r["service"], "new": bool(recent), "leaving": bool(r["leaving"]),
            "since": r["first_seen"] if recent else None}
