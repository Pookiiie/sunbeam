"""Local web server: JSON API + the UI. Listens on 127.0.0.1 only."""

from __future__ import annotations

import json
import os
import time
import mimetypes
import re
import threading
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from . import __version__, brand, db, duplicates, installed, settings, switch_import, switch_video, sync
from .meta import hltb, igdb, opencritic, sgdb, wishlist
from .paths import DATA_DIR, STATIC_DIR
from .sources import PLATFORMS, battlenet, epic, gog, playstation, steam, ubisoft, xbox

ROUTES: list[tuple[str, re.Pattern, callable]] = []


def route(method: str, pattern: str):
    def deco(fn):
        ROUTES.append((method, re.compile("^" + pattern + "$"), fn))
        return fn
    return deco


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


# ------------------------------------------------------------------ library

@route("GET", "/api/library")
def get_library(req, body, query):
    # Games only on a wishlist come too (wishlist_only): the Wishlist page and Curator's wishlist rows use them.
    return {"games": db.library(with_wishlist=True), "platforms": PLATFORMS, "year": db.year_stats(), "shelves": _shelves(),
            "wish_order": [r["game_id"] for r in db.query("SELECT game_id FROM wish_order ORDER BY pos")],
            "wishlist_at": int(db.get_meta("wishlist_at", "0") or 0)}


@route("GET", r"/api/games/(\d+)")
def get_game(req, body, query, game_id):
    g = db.game_detail(int(game_id))
    if not g:
        raise ApiError(404, "Game not found.")
    return g


@route("PATCH", r"/api/games/(\d+)")
def patch_game(req, body, query, game_id):
    gid = int(game_id)
    with db.tx() as c:
        if "status" in body:
            status = body["status"] or ""
            if status not in ("", "completed", "dnf"):
                raise ApiError(400, "Status must be completed, dnf (will not finish) or empty.")
            c.execute("UPDATE games SET status=?, status_at=?, status_manual=1, status_auto=NULL WHERE id=?",
                      (status, db.now() if status else None, gid))
        if "restore_status" in body:
            # Undo: put back exactly what was there (an automatic mark stays automatic).
            r = body["restore_status"] or {}
            status = r.get("status") or ""
            if status not in ("", "completed", "dnf"):
                raise ApiError(400, "Status must be completed, dnf (will not finish) or empty.")
            at, manual, auto = r.get("status_at"), r.get("status_manual"), r.get("status_auto")
            c.execute("UPDATE games SET status=?, status_at=?, status_manual=?, status_auto=? WHERE id=?",
                      (status, int(at) if isinstance(at, (int, float)) else None,
                       int(manual) if isinstance(manual, (int, float)) else None,
                       str(auto)[:500] if auto else None, gid))
        if "hidden" in body:
            v = body["hidden"]
            c.execute("UPDATE games SET hidden=? WHERE id=?", (None if v is None else int(bool(v)), gid))
        if "endless" in body:
            v = body["endless"]
            c.execute("UPDATE games SET endless=? WHERE id=?", (None if v is None else int(bool(v)), gid))
        if "ttb_custom" in body:
            v = body["ttb_custom"]
            try:
                v = None if v is None else max(0.1, min(float(v), 10000.0))
            except (TypeError, ValueError):
                raise ApiError(400, "Time to beat must be a number of hours.")
            c.execute("UPDATE games SET ttb_custom=? WHERE id=?", (v, gid))
        if "interest" in body:
            v = body["interest"] or None
            if v not in (None, "more", "less", "skip"):
                raise ApiError(400, "Interest must be more, less, skip or empty.")
            c.execute("UPDATE games SET interest=? WHERE id=?", (v, gid))
        if "notes" in body:
            c.execute("UPDATE games SET notes=? WHERE id=?", (str(body["notes"])[:5000], gid))
        if "title" in body and str(body["title"]).strip():
            c.execute("UPDATE games SET title=? WHERE id=?", (str(body["title"]).strip(), gid))
    return db.game_detail(gid)


# ------------------------------------------------------------------ installed games, Play

@route("GET", "/api/installed")
def get_installed(req, body, query):
    """Called when the window comes back into focus: looks again (at most every couple of minutes)."""
    started = installed.scan_if_stale() if query.get("look") else False
    out = {"at": int(db.get_meta("installs_at", "0") or 0), "looking": started, "errors": installed.errors}
    if query.get("details"):
        out["found"] = installed.last_found  # for checking a game that should have been found
    return out


@route("POST", r"/api/games/(\d+)/play")
def post_play(req, body, query, game_id):
    try:
        return {"platform": installed.play(int(game_id), body.get("platform") or None)}
    except LookupError as e:
        raise ApiError(404, str(e))


@route("POST", r"/api/games/(\d+)/install")
def post_install(req, body, query, game_id):
    try:
        installed.install(int(game_id), str(body.get("platform") or ""))
    except LookupError as e:
        raise ApiError(404, str(e))
    return {"ok": True}


# ------------------------------------------------------------------ shelves

def _shelves() -> list[dict]:
    out = []
    for r in db.query("""SELECT s.id, s.name, s.position, s.rules, COUNT(sg.game_id) AS count FROM shelves s
                         LEFT JOIN shelf_games sg ON sg.shelf_id = s.id GROUP BY s.id ORDER BY s.position, s.id"""):
        d = dict(r)
        try:
            d["rules"] = json.loads(d["rules"]) if d["rules"] else None
        except ValueError:
            d["rules"] = None
        out.append(d)
    return out


def _shelf_rules(body):
    """A smart shelf's rules: the Library's filters as the page sends them (kept as they are)."""
    rules = body.get("rules")
    if rules is None:
        return None
    if not isinstance(rules, dict):
        raise ApiError(400, "A smart shelf's rules didn't come through.")
    text = json.dumps(rules)
    if len(text) > 20000:
        raise ApiError(400, "That smart shelf has too many rules.")
    return text


def _shelf_name(body) -> str:
    name = " ".join(str(body.get("name") or "").split())[:60]
    if not name:
        raise ApiError(400, "Give the shelf a name.")
    return name


@route("GET", "/api/shelves")
def get_shelves(req, body, query):
    return {"shelves": _shelves()}


@route("POST", "/api/shelves")
def post_shelf(req, body, query):
    name = _shelf_name(body)
    with db.tx() as c:
        if c.execute("SELECT 1 FROM shelves WHERE lower(name)=lower(?)", (name,)).fetchone():
            raise ApiError(400, f"There's already a shelf called {name}.")
        pos = c.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM shelves").fetchone()[0]
        cur = c.execute("INSERT INTO shelves(name, position, created_at, rules) VALUES (?, ?, ?, ?)",
                        (name, pos, db.now(), _shelf_rules(body)))
        sid = cur.lastrowid
        for gid in body.get("games") or []:
            c.execute("INSERT OR IGNORE INTO shelf_games(shelf_id, game_id, added_at) VALUES (?, ?, ?)", (sid, int(gid), db.now()))
    return {"id": sid, "shelves": _shelves()}


@route("PATCH", r"/api/shelves/(\d+)")
def patch_shelf(req, body, query, shelf_id):
    with db.tx() as c:
        if "name" in body:
            name = _shelf_name(body)
            if c.execute("SELECT 1 FROM shelves WHERE lower(name)=lower(?) AND id<>?", (name, int(shelf_id))).fetchone():
                raise ApiError(400, f"There's already a shelf called {name}.")
            c.execute("UPDATE shelves SET name=? WHERE id=?", (name, int(shelf_id)))
        if body.get("rules") is not None:
            c.execute("UPDATE shelves SET rules=? WHERE id=?", (_shelf_rules(body), int(shelf_id)))
    return {"shelves": _shelves()}


@route("DELETE", r"/api/shelves/(\d+)")
def delete_shelf(req, body, query, shelf_id):
    with db.tx() as c:
        c.execute("DELETE FROM shelf_games WHERE shelf_id=?", (int(shelf_id),))
        c.execute("DELETE FROM shelves WHERE id=?", (int(shelf_id),))
    return {"shelves": _shelves()}


@route("POST", r"/api/shelves/(\d+)/games")
def post_shelf_games(req, body, query, shelf_id):
    """{"add": [game ids], "remove": [game ids]}"""
    sid = int(shelf_id)
    with db.tx() as c:
        row = c.execute("SELECT rules FROM shelves WHERE id=?", (sid,)).fetchone()
        if not row:
            raise ApiError(404, "That shelf is gone.")
        if row[0]:
            raise ApiError(400, "A smart shelf fills itself: change its rules instead.")
        for gid in body.get("add") or []:
            c.execute("INSERT OR IGNORE INTO shelf_games(shelf_id, game_id, added_at) VALUES (?, ?, ?)", (sid, int(gid), db.now()))
        for gid in body.get("remove") or []:
            c.execute("DELETE FROM shelf_games WHERE shelf_id=? AND game_id=?", (sid, int(gid)))
    return {"shelves": _shelves()}


@route("POST", "/api/wishlist/order")
def post_wish_order(req, body, query):
    """Wishlist › My order: the whole list, first to last."""
    ids = [int(x) for x in body.get("ids") or []][:5000]
    with db.tx() as c:
        c.execute("DELETE FROM wish_order")
        c.executemany("INSERT OR IGNORE INTO wish_order(game_id, pos) VALUES (?, ?)", [(gid, i) for i, gid in enumerate(ids)])
    return {"ok": True}


# ------------------------------------------------------------------ your year in games

@route("GET", r"/api/year/(\d{4})")
def get_year(req, body, query, year):
    return db.year_in_games(int(year))


@route("POST", r"/api/games/(\d+)/match")
def post_match(req, body, query, game_id):
    igdb_id = int(body.get("igdb_id") or 0)
    if not igdb_id:
        raise ApiError(400, "Pick a game to match.")
    new_id = sync.rematch(int(game_id), igdb_id)
    return db.game_detail(new_id)


@route("POST", r"/api/games/(\d+)/refresh")
def post_refresh(req, body, query, game_id):
    sync.refresh_one(int(game_id), prefer_sgdb=bool(body.get("prefer_sgdb")))
    return db.game_detail(int(game_id))


@route("GET", r"/api/games/(\d+)/art")
def get_art_options(req, body, query, game_id):
    data = sync.art_options(int(game_id))
    if not data:
        raise ApiError(404, "Game not found.")
    return data


@route("POST", r"/api/games/(\d+)/art")
def post_art(req, body, query, game_id):
    slot = body.get("slot")
    if slot not in ("cover", "hero", "logo"):
        raise ApiError(400, "Pick cover, banner or logo.")
    url = (body.get("url") or "").strip() or None
    if url and not url.lower().startswith(("https://", "http://")):
        raise ApiError(400, "Paste a full image link starting with https://")
    sync.set_art(int(game_id), slot, url)
    return db.game_detail(int(game_id))


@route("GET", "/api/igdb/search")
def igdb_search(req, body, query):
    term = (query.get("q") or [""])[0]
    limit = int((query.get("limit") or ["12"])[0])
    if (query.get("clean") or [""])[0]:
        # A store's title: try it as is, then without the store's extra words until IGDB finds something.
        # Up to three tries; a game whose name is exactly one of the tries goes first.
        results, seen = [], set()
        variants = _search_variants(term)
        keys = {db.normalize_title(v) for v in variants}
        for t in variants[:3]:
            for r in igdb.search(t, limit=limit):
                if r["id"] not in seen:
                    seen.add(r["id"]); results.append(r)
            if any(db.normalize_title(r.get("name") or "") in keys for r in results):
                break
        results.sort(key=lambda r: db.normalize_title(r.get("name") or "") not in keys)  # stable: IGDB's order otherwise
        results = results[:limit]
    else:
        results = igdb.search(term, limit=limit)
    out = []
    for r in results:
        out.append({
            "id": r["id"],
            "name": r.get("name"),
            "year": _year(r.get("first_release_date")),
            "cover": igdb.image((r.get("cover") or {}).get("image_id"), "t_cover_small_2x"),
            "is_edition": bool(r.get("version_parent") or r.get("parent_game")),
        })
    return {"results": out}


def _search_variants(title: str) -> list[str]:
    """ "Call of Duty: Black Ops 7 - Cross-Gen Bundle" -> itself, "Call of Duty: Black Ops 7", ...
    Store names carry bundles, editions, platforms and years that IGDB's names don't."""
    import re
    t = db.clean_display_title(title)
    t = re.sub(r"\s*\((game preview|\d{4}|old version|test.*?|beta)\)", "", t, flags=re.I).strip()
    out = [t]
    def add(x):
        x = re.sub(r"\s+", " ", x).strip(" -–—:")
        if len(x) >= 3 and x not in out:
            out.append(x)
    add(re.sub(r"\s+(xbox one|xbox series x\|s|xbox series|for windows( 10)?|windows( 10)?|pc)$", "", t, flags=re.I))
    add(re.split(r"\s+[-–—]\s+", t)[0])  # before " - Cross-Gen Bundle"
    add(re.sub(r"\b(standard|deluxe|gold|ultimate|complete|definitive|digital|game of the year|goty|anniversary|"
               r"\d+(st|nd|rd|th)|cross-gen|campaign|premium|enhanced)\b.*$", "", out[-1], flags=re.I))
    add(t.split(":")[0])  # the series name, last
    return out[:5]


def _year(ts):
    import datetime
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).year if ts else None


@route("POST", "/api/manual")
def post_manual(req, body, query):
    platform = body.get("platform") or "switch2"
    if platform not in PLATFORMS:
        raise ApiError(400, "Unknown platform.")
    title = (body.get("title") or "").strip()
    igdb_id = int(body.get("igdb_id") or 0) or None
    if not title:
        raise ApiError(400, "Enter a title.")
    hours = float(body.get("hours") or 0)
    last_played = int(body["last_played"]) if body.get("last_played") else None
    with db.tx() as c:
        existing = c.execute("SELECT id FROM games WHERE igdb_id=?", (igdb_id,)).fetchone() if igdb_id else None
        if existing:
            # Attach to the game we already have (e.g. own it on Steam and Switch 2).
            ts = db.now()
            c.execute(
                """INSERT INTO entries(game_id, platform, external_id, name, playtime_min, last_played, manual,
                   updated_at) VALUES (?,?,?,?,?,?,1,?)""",
                (existing["id"], platform, f"manual:{uuid.uuid4().hex[:12]}", title, int(hours * 60),
                 last_played, ts),
            )
            game_id = existing["id"]
        else:
            # Same name as a game you already have? It joins that game.
            game_id = db.upsert_entry(
                c, platform=platform, external_id=f"manual:{uuid.uuid4().hex[:12]}", name=title,
                playtime_min=int(hours * 60), last_played=last_played, manual=True,
            )
            if igdb_id:
                c.execute("UPDATE games SET igdb_id=?, match_state='manual' WHERE id=? AND igdb_id IS NULL",
                          (igdb_id, game_id))
        if body.get("status") in ("completed", "dnf"):
            c.execute("UPDATE games SET status=?, status_at=?, status_manual=1 WHERE id=?", (body["status"], db.now(), game_id))
    threading.Thread(target=sync.refresh_one, args=(game_id,), daemon=True).start()
    return db.game_detail(game_id)


@route("GET", "/api/duplicates")
def get_duplicates(req, body, query):
    return {"groups": duplicates.find()}


@route("GET", "/api/duplicates/review")
def get_dup_review(req, body, query):
    """Whether a sync found new games that look like duplicates (the page then asks)."""
    return {"pending": len(duplicates.review_ids())}


@route("POST", "/api/duplicates/review")
def post_dup_review(req, body, query):
    duplicates.reviewed()
    return {"ok": True}


@route("POST", "/api/duplicates/combine")
def post_combine(req, body, query):
    keep = int(body.get("keep") or 0)
    others = [int(x) for x in body.get("others") or [] if int(x) != keep]
    if not keep or not others:
        raise ApiError(400, "Pick at least two games to combine.")
    return duplicates.combine(keep, others)


@route("POST", "/api/duplicates/dismiss")
def post_dismiss(req, body, query):
    ids = [int(x) for x in body.get("ids") or []]
    if len(ids) < 2:
        raise ApiError(400, "Nothing to dismiss.")
    duplicates.dismiss(ids)
    return {"ok": True}


@route("POST", "/api/switch/preview")
def post_switch_preview(req, body, query):
    images = [i for i in (body.get("images") or []) if isinstance(i, str)]
    if not images:
        raise ApiError(400, "Add at least one screenshot.")
    if len(images) > 30:
        raise ApiError(400, "That's a lot of screenshots: try 30 or fewer at a time.")
    return switch_import.preview(images)


@route("GET", "/api/switch/video")
def get_switch_video(req, body, query):
    return switch_video.status()


@route("POST", "/api/switch/import")
def post_switch_import(req, body, query):
    items = [i for i in (body.get("items") or []) if isinstance(i, dict)]
    if not items:
        raise ApiError(400, "Tick at least one game to add.")
    result = switch_import.apply(items)
    if body.get("from_video"):
        switch_video.mark_applied()
    if result["new_ids"]:
        threading.Thread(target=sync.enrich, kwargs={"game_ids": result["new_ids"]}, daemon=True).start()
    return result


@route("PATCH", r"/api/entries/(\d+)")
def patch_entry(req, body, query, entry_id):
    row = db.query("SELECT * FROM entries WHERE id=?", (int(entry_id),))
    if not row:
        raise ApiError(404, "Not found.")
    e = row[0]
    if not e["manual"] and ("hours" in body or "last_played" in body):
        raise ApiError(400, "Imported playtime comes from the store. Use “Don't count” to adjust it.")
    with db.tx() as c:
        # Any copy: how much of it is yours, and whether it counts at all.
        if "not_mine_hours" in body:
            # Hours of this copy someone else played; subtracted now and after every future sync.
            adjust = max(int(round(float(body["not_mine_hours"] or 0) * 60)), 0)
            c.execute("UPDATE entries SET adjust_min=? WHERE id=?", (adjust, int(entry_id)))
        if "excluded" in body:
            c.execute("UPDATE entries SET excluded=? WHERE id=?", (int(bool(body["excluded"])), int(entry_id)))
        if "hours" in body:
            c.execute("UPDATE entries SET playtime_min=?, updated_at=? WHERE id=?",
                      (int(float(body["hours"] or 0) * 60), db.now(), int(entry_id)))
            db.log_playtime(c)
        if "last_played" in body:
            c.execute("UPDATE entries SET last_played=? WHERE id=?",
                      (int(body["last_played"]) if body["last_played"] else None, int(entry_id)))
    return db.game_detail(row[0]["game_id"])


@route("DELETE", r"/api/entries/(\d+)")
def delete_entry(req, body, query, entry_id):
    row = db.query("SELECT * FROM entries WHERE id=?", (int(entry_id),))
    if not row:
        raise ApiError(404, "Not found.")
    if not row[0]["manual"]:
        raise ApiError(400, "Only games you added by hand can be removed.")
    with db.tx() as c:
        c.execute("DELETE FROM play_days WHERE entry_id=?", (int(entry_id),))
        c.execute("DELETE FROM entries WHERE id=?", (int(entry_id),))
        db.delete_orphans(c)
    return {"game": db.game_detail(row[0]["game_id"])}


# ------------------------------------------------------------------ settings & sync

@route("GET", "/api/settings")
def get_settings(req, body, query):
    return {"settings": settings.public_view(), "hltb_installed": hltb.HowLongToBeat is not None,
            "version": __version__, "gog_galaxy": gog.has_galaxy(), "oc": sync.oc_status()}


@route("POST", "/api/settings")
def post_settings(req, body, query):
    changes = {}
    for key, value in (body or {}).items():
        if key not in settings.FIELDS or key in settings.INTERNAL:
            continue
        if settings.FIELDS[key] and value in (None, ""):
            continue  # blank secret field = keep the existing value
        changes[key] = value
    settings.update(changes)
    return get_settings(req, body, query)


@route("POST", r"/api/settings/clear/(\w+)")
def clear_setting(req, body, query, key):
    if key in settings.FIELDS:
        settings.update({key: ""})
    return get_settings(req, body, query)


TESTS = {"steam": steam.test, "playstation": playstation.test, "epic": epic.test, "gog": gog.test, "xbox": xbox.test, "battlenet": battlenet.test, "ubisoft": ubisoft.test, "igdb": igdb.test, "sgdb": sgdb.test,
         "opencritic": opencritic.test, "wishlist": wishlist.test}


@route("POST", r"/api/test/(\w+)")
def post_test(req, body, query, service):
    fn = TESTS.get(service)
    if not fn:
        raise ApiError(404, "Unknown service.")
    try:
        return {"ok": True, "message": fn()}
    except Exception as e:
        return {"ok": False, "message": str(e)}


def _busy():
    kind = sync.status().get("kind")
    raise ApiError(409, "A refresh is already running." if kind == "refresh" else "A sync is already running.")


@route("POST", "/api/sync")
def post_sync(req, body, query):
    sources = [s for s in body.get("sources", []) if s in sync.SOURCES]
    if not sources:
        raise ApiError(400, "Pick at least one library to sync.")
    if not sync.start_sync(sources):
        _busy()
    return sync.status()


@route("POST", "/api/refresh")
def post_refresh_all(req, body, query):
    mode = "all" if body.get("mode") == "all" else "missing"
    resume = bool(body.get("resume"))
    if resume and not sync.refresh_job():
        raise ApiError(400, "There's no full refresh to continue.")
    if not sync.start_refresh(mode, list(body.get("steps") or []), resume=resume):
        _busy()
    return sync.status()


@route("GET", "/api/refresh/job")
def get_refresh_job(req, body, query):
    """A full refresh that's paused or was interrupted (None if there's none)."""
    return {"job": sync._job_view()}


@route("POST", "/api/refresh/forget")
def post_refresh_forget(req, body, query):
    """Drop a paused full refresh."""
    st = sync.status()
    if st.get("running") and st.get("kind") == "refresh" and st.get("mode") == "all":
        sync.cancel(forget=True)  # the full refresh itself is running: stop it and forget it
    else:
        sync._save_job(None)      # (a "fill in missing" refresh may keep going)
    return {"job": None}


@route("GET", "/api/refresh/estimate")
def get_refresh_estimate(req, body, query):
    return sync.estimate()


@route("GET", "/api/sync")
def get_sync(req, body, query):
    st = sync.status()
    st["last_sync_at"] = int(db.get_meta("last_sync_at", "0") or 0)
    try:
        st["prompt"] = json.loads(db.get_meta("sync_prompt") or "[]")  # what an update would like a sync for
    except ValueError:
        st["prompt"] = []
    return st


@route("POST", "/api/sync/prompt")
def post_sync_prompt(req, body, query):
    db.set_meta("sync_prompt", "")  # "Later": not asked again for this update
    return {"ok": True}


@route("POST", "/api/ubisoft/link")
def post_ubisoft_link(req, body, query):
    """The Send to <app> bookmark hands its Ubisoft data to the page, which posts it here."""
    try:
        return ubisoft.link(body.get("payload"))
    except ubisoft.UbisoftError as e:
        raise ApiError(400, str(e))


# ------------------------------------------------------------------ desktop app and updates

@route("GET", "/api/ping")
def get_ping(req, body, query):
    """The page says it's still open (desktop mode quits a minute after the last one)."""
    from . import desktop
    desktop.ping()
    return {"ok": True, "version": __version__}


@route("POST", "/api/open")
def post_open(req, body, query):
    """Open a link to another site in your normal browser (from the app window)."""
    from . import desktop
    url = str(body.get("url") or "")
    if not re.match(r"^https?://[^\s]+$", url):
        raise ApiError(400, "Only web links can be opened.")
    desktop.open_external(url)
    return {"ok": True}


@route("POST", "/api/window")
def post_window(req, body, query):
    """The app window's size and place, so the next one opens the same way."""
    try:
        rect = {k: int(body[k]) for k in ("x", "y", "w", "h")}
    except (KeyError, TypeError, ValueError):
        raise ApiError(400, "Expected x, y, w and h.")
    rect["max"] = bool(body.get("max"))
    if rect["w"] >= 480 and rect["h"] >= 360:
        settings.update({"window_rect": rect})
    return {"ok": True}


@route("GET", "/api/desktop")
def get_desktop(req, body, query):
    from . import desktop
    s = settings.load()
    return {"version": __version__, "name": brand.NAME, "windows": desktop.is_windows(), "packaged": desktop.packaged(),
            "app": desktop.APP_MODE, "shortcuts": desktop.shortcuts_state(),
            "setup_done": bool(s.get("setup_done")), "shortcuts_asked": bool(s.get("shortcuts_asked"))}


@route("POST", "/api/desktop/shortcuts")
def post_shortcuts(req, body, query):
    from . import desktop
    which = [w for w in body.get("which") or [] if w in ("desktop", "startmenu")]
    settings.update({"shortcuts_asked": True})
    try:
        made = desktop.make_shortcuts(which) if which else []
    except Exception as e:
        raise ApiError(500, str(e))
    return {"made": made, "shortcuts": desktop.shortcuts_state()}


@route("GET", "/api/update")
def get_update(req, body, query):
    from . import updater
    return updater.check(force=query.get("force", ["0"])[0] == "1")


@route("POST", "/api/update")
def post_update(req, body, query):
    from . import updater
    try:
        updater.start_install(req.server)
    except RuntimeError as e:
        raise ApiError(400, str(e))
    return updater.check()


@route("POST", "/api/sync/cancel")
def post_cancel(req, body, query):
    sync.cancel(forget=bool(body.get("forget")))
    return sync.status()


# ------------------------------------------------------------------ HTTP plumbing

class Handler(BaseHTTPRequestHandler):
    server_version = "gameshelf/" + __version__

    def log_message(self, fmt, *args):  # quieter console
        if args and isinstance(args[1], str) and args[1].startswith(("4", "5")):
            super().log_message(fmt, *args)

    def _allowed_host(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")

    def _send(self, status: int, payload: bytes, ctype: str, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status: int, obj):
        self._send(status, json.dumps(obj, default=str).encode(), "application/json; charset=utf-8",
                   {"Cache-Control": "no-store"})

    def _dispatch(self, method: str):
        if not self._allowed_host():
            return self._json(403, {"error": "Forbidden"})
        parts = urlsplit(self.path)
        path = parts.path
        if not path.startswith("/api/"):
            if method != "GET":
                return self._json(405, {"error": "Method not allowed"})
            return self._static(path)
        # Mutating calls must come from our own page (blocks cross-site requests).
        if method != "GET" and self.headers.get("X-GameShelf") != "1":
            return self._json(403, {"error": "Missing app header"})
        if method == "POST" and path == "/api/switch/video":
            return self._upload_video()
        body = {}
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            except json.JSONDecodeError:
                return self._json(400, {"error": "Invalid JSON"})
        query = parse_qs(parts.query)
        for m, pattern, fn in ROUTES:
            if m != method:
                continue
            match = pattern.match(path)
            if match:
                try:
                    return self._json(200, fn(self, body, query, *match.groups()))
                except ApiError as e:
                    return self._json(e.status, {"error": str(e)})
                except Exception as e:
                    traceback.print_exc()
                    return self._json(500, {"error": str(e)})
        return self._json(404, {"error": "Not found"})

    def _upload_video(self):
        """Screen recordings can be large: stream them to disk instead of reading them into memory."""
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return self._json(400, {"error": "Choose a screen recording first."})
        if length > switch_video.MAX_BYTES:
            return self._json(413, {"error": "That video is over 2 GB. Record a shorter swipe-through, or split it."})
        if switch_video.status().get("state") in ("starting", "setup", "scanning", "reading"):
            return self._json(409, {"error": "A recording is already being read. Wait for it to finish."})
        name = os.path.basename(unquote(self.headers.get("X-Filename") or "recording"))[:120]
        ext = os.path.splitext(name)[1].lower() if os.path.splitext(name)[1].lower() in (".mp4", ".mov", ".m4v", ".webm", ".mkv") else ".mp4"
        dest = DATA_DIR / f"upload-{int(time.time())}{ext}"
        left = length
        try:
            with open(dest, "wb") as f:
                while left > 0:
                    chunk = self.rfile.read(min(1 << 20, left))
                    if not chunk:
                        break
                    f.write(chunk)
                    left -= len(chunk)
        except OSError as e:
            return self._json(500, {"error": f"Couldn't save the video: {e}"})
        if left > 0:
            try:
                os.remove(dest)
            except OSError:
                pass
            return self._json(400, {"error": "The upload stopped part way. Try again."})
        if not switch_video.start(str(dest), name):
            try:
                os.remove(dest)
            except OSError:
                pass
            return self._json(409, {"error": "A recording is already being read. Wait for it to finish."})
        return self._json(200, switch_video.status())

    def _static(self, path: str):
        if path in ("", "/"):
            path = "/index.html"
        target = (STATIC_DIR / path.lstrip("/")).resolve()
        if STATIC_DIR.resolve() not in target.parents or not target.is_file():
            target = STATIC_DIR / "index.html"
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        payload = target.read_bytes()
        if target.name == "index.html":  # the app's name (brand.py) goes into the page
            import html as _html
            payload = payload.replace(b"__APP__", _html.escape(brand.NAME).encode())
        self._send(200, payload, ctype, {"Cache-Control": "no-cache"})

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")


mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")


def serve(port: int) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    sync.start_oc_auto()  # OpenCritic scores, a day's allowance at a time, by themselves
    return httpd
