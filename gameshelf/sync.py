"""Background sync: import libraries, then fill in metadata, art, reviews and
time-to-beat. Runs in one worker thread; the UI polls `status()`."""

from __future__ import annotations

import re
import threading
import time
import traceback

from . import brand, db, net, rules

APP_NAME = brand.NAME
from .meta import achievements, catalogs, hltb, igdb, opencritic, sgdb, steamstore, wishlist
from .sources import battlenet, epic, playstation, steam, ubisoft, xbox

SOURCES = {"steam": steam, "playstation": playstation, "epic": epic, "xbox": xbox, "ubisoft": ubisoft, "battlenet": battlenet}

_state_lock = threading.Lock()
_state: dict = {"running": False}
_cancel = threading.Event()
_forget = threading.Event()  # set with _cancel: drop a full refresh instead of pausing it
_since: int | None = None    # a full refresh re-fetches games last updated before this time


# Enrichment steps, in the order they run.
STEPS = [
    ("match", "Identifying games"),
    ("details", "Fetching game details"),
    ("art", "Collecting artwork"),
    ("reviews", "Reading Steam user reviews"),
    ("ttb", "Looking up time to beat"),
    ("critics", "Reading OpenCritic scores"),
    ("completions", "Checking achievements and trophies"),
]


def status() -> dict:
    with _state_lock:
        out = {k: (list(v) if isinstance(v, list) else v) for k, v in _state.items() if k != "plan"}
        plan = _state.get("plan")
        if plan:
            out.update(_overall(plan))
        out["timeline"] = _timeline_view(plan)
        return out


def _timeline_view(plan) -> list[dict]:
    """Every step of the job with its state, merged with live counts."""
    items = []
    for t in _state.get("timeline", []):
        item = dict(t)
        if plan and t["id"] in plan["steps"] and item["state"] != "skipped":
            st = plan["steps"][t["id"]]
            item["total"], item["done"] = st["total"], min(st["done"], st["total"])
            if st["total"] == 0 and item["state"] != "current":
                item["state"] = "skipped"
                item.setdefault("note", "Nothing to do")
        items.append(item)
    return items


def _tl(step_id: str, **changes):
    """Update one step in the timeline (caller holds no lock)."""
    if not _tracking():
        return
    with _state_lock:
        for t in _state.get("timeline", []):
            if t["id"] == step_id:
                t.update(changes)


def _overall(plan: dict) -> dict:
    """One progress figure across every remaining step, weighted by roughly how
    long each item takes (an artwork or HowLongToBeat lookup is much slower than
    a batch of game details)."""
    total = sum(st["total"] * st["weight"] for st in plan["steps"].values())
    done = sum(min(st["done"], st["total"]) * st["weight"] for st in plan["steps"].values())
    frac = done / total if total else 1.0
    frac = max(frac, plan.get("max_frac", 0.0))  # never let the bar slide backwards
    plan["max_frac"] = frac
    now = time.time()
    elapsed = now - plan["started"]
    eta = None
    if frac < 1 and elapsed > 8:
        # Each step at its own pace: measured once a step has a few items done,
        # otherwise the typical seconds per game for that kind of lookup.
        secs = 0.0
        for n, _ in STEPS:
            st = plan["steps"][n]
            left = max(st["total"] - st["done"], 0)
            if not left:
                continue
            rate = st["weight"]
            if st.get("started") and st["done"] >= 5 and now - st["started"] >= 5:
                rate = (now - st["started"]) / st["done"]
            secs += left * rate
        eta = int(secs)
    names = [n for n, _ in STEPS if plan["steps"][n]["total"] > 0]
    current = _state.get("stage")
    return {
        "overall": round(frac, 4),
        "eta_s": eta,
        "step_no": names.index(current) + 1 if current in names else None,
        "step_count": len(names),
        "items_left": sum(max(st["total"] - st["done"], 0) for st in plan["steps"].values()),
    }


def _tracking() -> bool:
    # Only the background sync drives the progress bar; single-game refreshes
    # (Add game, Fix match, Refresh) run quietly in their own threads.
    return threading.current_thread().name == "sync"


def _set(**kw):
    with _state_lock:
        _state.update(kw)


def _log(msg: str, level: str = "info"):
    if not _tracking():
        return  # a single-game refresh in its own thread: not part of the sync's log
    with _state_lock:
        _state.setdefault("log", []).append({"t": int(time.time()), "level": level, "msg": msg})
        _state["log"] = _state["log"][-80:]
        if level == "error":
            _state.setdefault("errors", []).append(msg)


def _stage(name: str, label: str, total: int = 0):
    if not _tracking():
        return
    with _state_lock:
        _state.update(stage=name, stage_label=label, done=0, total=total)
        for t in _state.get("timeline", []):
            if t["id"] == name:
                t["state"] = "current"
            elif t["state"] == "current":
                t["state"] = "done"
        plan = _state.get("plan")
        if plan and name in plan["steps"]:
            plan["steps"][name]["total"] = total  # real count replaces the estimate
            plan["steps"][name]["done"] = 0
            plan["steps"][name]["started"] = time.time()


def _tick(n: int = 1):
    if not _tracking():
        return
    with _state_lock:
        _state["done"] = _state.get("done", 0) + n
        plan = _state.get("plan")
        if plan and _state.get("stage") in plan["steps"]:
            plan["steps"][_state["stage"]]["done"] += n


def _finish_step(name: str):
    """Mark a step complete (also covers steps that had nothing to do or stopped on an error)."""
    if not _tracking():
        return
    with _state_lock:
        plan = _state.get("plan")
        if plan and name in plan["steps"]:
            st = plan["steps"][name]
            st["done"] = st["total"]
        for t in _state.get("timeline", []):
            if t["id"] == name and t["state"] in ("current", "upcoming"):
                t["state"] = "done"


def cancel(forget: bool = False):
    """Stop the running job. A full refresh is paused (it carries on next time) unless forget."""
    if forget:
        _forget.set()
    _cancel.set()


ALL_STEPS = ("info", "art", "reviews", "ttb", "critics", "completions")  # what a refresh can update


def _begin(kind: str, target, args: tuple, **extra) -> bool:
    """Start a background job. Only one runs at a time (they share rate limits)."""
    with _state_lock:
        if _state.get("running"):
            return False
        _state.clear()
        _state.update(running=True, kind=kind, started_at=int(time.time()), log=[], errors=[],
                      stage="starting", stage_label="Starting", done=0, total=0, **extra)
    _cancel.clear()
    _forget.clear()
    threading.Thread(target=_job, args=(target, args), daemon=True, name="sync").start()
    return True


def _job(target, args):
    try:
        target(*args)
        _log("Stopped early." if _cancel.is_set() else "All done.")
    except Exception as e:  # never kill the server
        _log(f"Failed: {e}", "error")
        traceback.print_exc()
    finally:
        _set(running=False, finished_at=int(time.time()), stage="done", stage_label="Finished")


SOURCE_LABELS = {"steam": "Steam", "playstation": "PlayStation", "epic": "Epic Games Store", "xbox": "Xbox",
                 "ubisoft": "Ubisoft Connect", "battlenet": "Battle.net"}
STEP_HINTS = {
    "match": "Finds each game on IGDB",
    "details": "Description, genres, developers",
    "art": "Covers, banners and logos",
    "reviews": "Steam's positive-review percentage",
    "ttb": "Main, extras and 100% times",
    "critics": "Top critic average and % recommended",
    "completions": "Marks games you've finished, from your achievements and trophies",
}


def _info_timeline(steps, for_new: bool) -> list[dict]:
    wanted = {"match": "info", "details": "info", "art": "art", "reviews": "reviews", "ttb": "ttb", "critics": "critics",
              "completions": "completions"}
    labels = {
        "match": ("Identify games", "Identify new games"),
        "details": ("Fetch game details", "Fetch details for new games"),
        "art": ("Collect artwork", "Collect artwork for new games"),
        "reviews": ("Read Steam reviews", "Read Steam reviews for new games"),
        "ttb": ("Look up time to beat", "Look up time to beat for new games"),
        "critics": ("Read OpenCritic scores", "Read OpenCritic scores for new games"),
        "completions": ("Detect finished games", "Detect finished games"),
    }
    out = []
    for sid, _ in STEPS:
        if wanted[sid] not in steps:
            continue
        out.append({"id": sid, "label": labels[sid][1 if for_new else 0],
                    "hint": STEP_HINTS[sid], "state": "upcoming"})
    return out


def start_sync(sources: list[str]) -> bool:
    """Import libraries and playtime. Games seen for the first time also get
    their details, art, reviews and time to beat."""
    timeline = [{"id": f"import_{s}", "label": f"Import {SOURCE_LABELS[s]} library",
                 "hint": "Games, playtime, last played", "state": "upcoming"} for s in sources]
    timeline.append({"id": "catalogs", "label": "Update XGP and PS+ lists",
                     "hint": "What's included, recently added and leaving soon", "state": "upcoming"})
    if any(wishlist.configured().values()):
        timeline.append({"id": "wishlists", "label": "Update wishlists",
                         "hint": "Steam and PlayStation: release dates and prices", "state": "upcoming"})
    timeline += _info_timeline(ALL_STEPS, for_new=True)
    started = _begin("sync", _run_sync_all, (sources,), sources=sources, timeline=timeline)
    if started:
        db.set_meta("sync_prompt", "")  # a sync after an update that asked for one: done
    return started


def _run_sync_all(sources: list[str]):
    _run_sync(sources)
    if not _cancel.is_set():
        _early_access()
    try:
        from . import installed
        installed.scan()  # new games may already be installed
    except Exception as e:
        _log(f"Couldn't check which games are installed: {e}", "warn")


def _early_access():
    """Which games are in early access (Steam, else IGDB). Quiet unless it finds some."""
    from .meta import early_access
    try:
        n = early_access.refresh()
    except Exception as e:
        _log(f"Couldn't check for early access games: {e}", "warn")
        return
    if n:
        _log(f"{n} game{'s are' if n != 1 else ' is'} in early access.")


# A full refresh ("Re-fetch for every game") takes a long time for a big library, so it's
# remembered (meta "refresh_job": {since, steps, paused}) and picks up where it stopped: a game
# whose info was fetched after `since` is done. It ends when every step has gone through every
# game, or when you cancel it.
def refresh_job() -> dict | None:
    import json
    try:
        job = json.loads(db.get_meta("refresh_job") or "null")
    except ValueError:
        return None
    return job if isinstance(job, dict) and job.get("since") else None


def _save_job(job: dict | None) -> None:
    import json
    db.set_meta("refresh_job", json.dumps(job) if job else "")


def start_refresh(mode: str, steps: list[str], resume: bool = False) -> bool:
    """Update info for games already in the library.
    mode "missing": only fill gaps. mode "all": re-fetch for every game (resumable).
    resume: carry on with the remembered full refresh."""
    job = refresh_job()
    if resume:
        if not job:
            return False
        mode, steps = "all", job["steps"]
    steps = [s for s in steps if s in ALL_STEPS] or list(ALL_STEPS)
    if mode == "all" and not resume:
        job = {"since": db.now(), "steps": steps, "paused": False}
    if mode == "all":
        job["paused"] = False
        _save_job(job)
    return _begin("refresh", _run_refresh, (mode, steps, job["since"] if mode == "all" else None),
                  mode=mode, steps=steps, resumed=resume, timeline=_info_timeline(steps, for_new=False))


def _run_sync(sources: list[str]):
    before = db.query("SELECT COALESCE(MAX(id), 0) m FROM games")[0]["m"]
    for name in sources:
        if _cancel.is_set():
            return
        _import(name)
    if _cancel.is_set():
        return
    _catalogs()
    if any(wishlist.configured().values()):
        _wishlists()
    db.set_meta("last_sync_at", str(db.now()))
    new = db.query("SELECT COUNT(*) n FROM games WHERE id > ?", (before,))[0]["n"]
    if not new:
        with _state_lock:
            for t in _state.get("timeline", []):
                if not t["id"].startswith("import_") and t["id"] not in ("completions", "catalogs", "wishlists"):
                    t.update(state="skipped", note="No new games")
        _log("No new games. Playtime and last played are up to date.")
        # Games you've played since the last check may have new achievements, and covers
        # flagged for another look (e.g. Steam's stand-in covers) get fixed.
        redo_art = db.query("SELECT COUNT(*) n FROM games WHERE art_at IS NULL")[0]["n"]
        # and Steam reviews for games whose Steam version changed (e.g. matched to a wishlisted Steam page)
        redo_rev = [r["id"] for r in db.query("SELECT id FROM games WHERE steam_appid IS NOT NULL AND reviews_at IS NULL")]
        with _state_lock:
            for t in _state.get("timeline", []):
                if (t["id"] == "art" and redo_art) or (t["id"] == "reviews" and redo_rev):
                    t.update(state="upcoming", note=None, label={"art": "Collect artwork", "reviews": "Read Steam reviews"}[t["id"]])
        if redo_rev:  # just those games (the regular two-weekly re-read is Refresh assets' job)
            _reviews(False, f" AND id IN ({','.join('?' * len(redo_rev))})", redo_rev)
            _tl("reviews", state="done")
        enrich(steps=(("art",) if redo_art else ()) + ("completions",))
        return
    cat = db.query(f"""SELECT COUNT(*) n FROM games WHERE id > ? AND id NOT IN
                      (SELECT game_id FROM entries WHERE {db.OWNED}) AND id IN
                      (SELECT game_id FROM entries WHERE ownership = 'catalog')""", (before,))[0]["n"]
    wish = db.query(f"""SELECT COUNT(*) n FROM games WHERE id > ? AND id NOT IN
                       (SELECT game_id FROM entries WHERE {db.NOT_WISHLIST})""", (before,))[0]["n"]
    mine = new - cat - wish
    parts = ([f"{mine} new game{'s' if mine != 1 else ''}"] if mine else []) + \
            ([f"{cat} new on XGP or PS+"] if cat else []) + \
            ([f"{wish} new on your wishlists"] if wish else [])
    _log(f"{' and '.join(parts)}. Fetching their info.")
    try:
        enrich(min_id=before + 1)
    finally:
        if mine:
            _check_duplicates(before)


def _check_duplicates(before: int) -> None:
    """New games that look like one already in the library: the page asks about them
    (nothing is combined without your OK)."""
    from . import duplicates
    try:
        n = duplicates.flag_new(before)
    except Exception as e:
        _log(f"Couldn't check the new games for duplicates: {e}")
        return
    if n:
        _log(f"{n} new game{'s look' if n != 1 else ' looks'} like a game you already have. Have a look in the duplicates window.")


def _run_refresh(mode: str, steps: list[str], since: int | None = None):
    global _since
    _since = since
    try:
        enrich(refresh=(mode == "all"), steps=steps)
    finally:
        _since = None
    if mode != "all":
        return
    if _cancel.is_set() and not _forget.is_set():
        job = refresh_job()
        if job:
            job["paused"] = True
            _save_job(job)
            _log("Paused. The full refresh carries on from here when you continue it (Refresh assets).")
    else:
        _save_job(None)  # finished, or cancelled
        if _forget.is_set():
            _log("Full refresh cancelled.")


def _catalogs():
    _stage("catalogs", "Updating XGP and PS+ lists")
    res = catalogs.refresh()
    notes = []
    for svc, r in res.items():
        label = catalogs.SERVICES[svc]
        if r.get("error"):
            _log(f"{label} list: couldn't update ({r['error']}).", "warn")
        else:
            notes.append(f"{label} {r['count']:,}")
    _tl("catalogs", state="done", note=", ".join(notes) or "Couldn't update")


def _wishlists():
    _stage("wishlists", "Updating wishlists")
    res = wishlist.refresh()
    notes = []
    other = res.pop("other_store", None)  # prices on the store whose wishlist doesn't have the game
    if other and other.get("error"):
        _log(f"Prices on the other store: couldn't check ({other['error']}).", "warn")
    elif other:
        _log(f"Prices on the other store: found for {other['count']:,} wishlist game{'s' if other['count'] != 1 else ''}.")
    for store, r in res.items():
        label = wishlist.STORES.get(store, store)
        if r.get("error"):
            _log(f"{label} wishlist: couldn't update ({r['error']}).", "warn")
        elif r.get("note"):
            _log(f"{label} wishlist: {r['note']}.", "warn")
        else:
            notes.append(f"{label} {r['count']:,}")
    _tl("wishlists", state="done", note=", ".join(notes) or "Couldn't update")


def _import(name: str):
    module = SOURCES[name]
    label = SOURCE_LABELS[name]
    _stage(f"import_{name}", f"Importing {label} library")
    try:
        items = module.fetch()
    except Exception as e:
        _log(f"{label}: {e}", "error")
        _tl(f"import_{name}", state="failed", note="Couldn't import, see below")
        return
    with db.tx() as c:
        for it in items:
            db.upsert_entry(
                c,
                platform=it["platform"], external_id=it["external_id"], name=it["name"],
                playtime_min=it.get("playtime_min", 0), last_played=it.get("last_played"),
                first_played=it.get("first_played"), ownership=it.get("ownership"),
                steam_appid=it.get("steam_appid"), psn_image=it.get("psn_image"),
            )
        if hasattr(module, "after_import"):
            module.after_import(c)
        db.log_playtime(c)
    notice = getattr(module, "notice", None)
    _log(f"{label}: {len(items)} games imported." if not notice else f"{label}: {len(items)} games kept from last time.")
    if notice:
        _log(f"{label}: {notice}", "warn")
    _tl(f"import_{name}", state="done", note=f"{len(items):,} games" if not notice else "Couldn't refresh, see below")


# ------------------------------------------------------------------ enrichment

def enrich(refresh: bool = False, game_ids: list[int] | None = None, min_id: int | None = None,
           steps=ALL_STEPS, prefer_sgdb: bool = False):
    scope = ""
    args: list = []
    if game_ids:
        scope = f" AND id IN ({','.join('?' * len(game_ids))})"
        args = list(game_ids)
    elif min_id:
        scope = " AND id >= ?"
        args = [min_id]

    _plan(refresh, scope, args, prefer_sgdb, steps)
    # Finished-game detection covers every game played since its last check, not just new ones.
    comp_scope, comp_args = (scope, args) if game_ids else ("", [])
    if "info" in steps:
        if igdb.configured():
            _match(refresh, scope, args)
            _finish_step("match")
            if _cancel.is_set():
                return
            _details(refresh, scope, args)
            _finish_step("details")
        else:
            _tl("match", state="skipped", note="IGDB isn't set up")
            _tl("details", state="skipped", note="IGDB isn't set up")
            _log("IGDB isn't set up yet, so game details were skipped. "
                 "Add it in Settings, then use Refresh assets.", "warn")
    for name, fn in (("art", lambda: _art(refresh, scope, args, prefer_sgdb)),
                     ("reviews", lambda: _reviews(refresh, scope, args)),
                     ("ttb", lambda: _ttb(refresh, scope, args)),
                     ("critics", lambda: _critics(refresh, scope, args)),
                     ("completions", lambda: _completions(refresh, comp_scope, comp_args))):
        if name not in steps:
            continue
        if _cancel.is_set():
            return
        fn()
        _finish_step(name)
    try:
        catalogs.link()  # titles may have changed: re-match Game Pass / PS Plus lists
        wishlist.link()  # and wishlist games (two games may have been combined)
    except Exception as e:
        _log(f"Couldn't re-link the XGP / PS+ lists and wishlists: {e}", "warn")


def _conds(refresh: bool) -> dict[str, str]:
    stale = db.now() - 14 * 86400
    now = db.now()
    if refresh and _since:
        return _resume_conds(_since, now)
    # Not out yet (wishlist games): nothing to look up on HowLongToBeat or OpenCritic until release.
    out = _not_out(now)
    return {
        "match": "match_state IN ('pending', 'none')" if refresh else "match_state = 'pending'",
        "details": "igdb_id IS NOT NULL" + ("" if refresh else " AND meta_at IS NULL"),
        "art": "1=1" if refresh else "art_at IS NULL",
        # games with a Steam version whose reviews are due, plus games we haven't yet looked for on Steam
        "reviews": "1=1" if refresh else
                   f"((steam_appid IS NOT NULL AND (reviews_at IS NULL OR reviews_at < {stale})) "
                   f"OR (steam_appid IS NULL AND steam_checked_at IS NULL))",
        # never looked up, or found nothing a month ago (HowLongToBeat adds games all the time)
        "ttb": out + (" AND 1=1" if refresh else
               f" AND (ttb_at IS NULL OR (ttb_main IS NULL AND ttb_extra IS NULL AND ttb_complete IS NULL "
               f"AND ttb_at < {db.now() - 30 * 86400}))"),
        # not looked up yet; scores of recent games move, so re-read them for a few months after release
        # a wishlist game out in the last two months that OpenCritic didn't have yet: once a week
        "critics": out + (" AND 1=1" if refresh else
                   f" AND (oc_checked_at IS NULL OR (oc_id IS NOT NULL AND oc_at < {db.now() - 30 * 86400} "
                   f"AND COALESCE(release_date, 0) > {db.now() - 180 * 86400}) "
                   f"OR (oc_id IS NULL AND oc_checked_at < {now - 7 * 86400} AND id IN (SELECT game_id FROM wishlist "
                   f"WHERE released = 1 AND release_ts > {now - 60 * 86400})))"),
        # games you haven't marked, played since we last looked at their achievements
        # (never games in early access: there's more to come)
        "completions": "status = '' AND COALESCE(status_manual, 0) = 0 AND COALESCE(early_access, 0) = 0 AND id IN (SELECT game_id FROM entries "
                       "WHERE platform IN ('steam', 'playstation') AND playtime_min > 0 AND NOT COALESCE(excluded, 0)"
                       + ("" if refresh else " AND (games.completion_checked_at IS NULL OR "
                                             "COALESCE(last_played, 0) > games.completion_checked_at)") + ")",
    }


def _not_out(now: int) -> str:
    return (f"NOT (COALESCE(release_date, 0) > {now} OR id IN (SELECT game_id FROM wishlist "
            f"WHERE game_id IS NOT NULL AND released = 0 AND game_id NOT IN (SELECT game_id FROM wishlist WHERE released = 1)))")


def _resume_conds(since: int, now: int) -> dict[str, str]:
    """A full refresh: every game whose info wasn't fetched since the refresh began."""
    return {
        "match": "match_state IN ('pending', 'none')",
        "details": f"igdb_id IS NOT NULL AND (meta_at IS NULL OR meta_at < {since})",
        "art": f"(art_at IS NULL OR art_at < {since})",
        "reviews": f"((steam_appid IS NOT NULL AND (reviews_at IS NULL OR reviews_at < {since})) "
                   f"OR (steam_appid IS NULL AND (steam_checked_at IS NULL OR steam_checked_at < {since})))",
        "ttb": _not_out(now) + f" AND (ttb_at IS NULL OR ttb_at < {since})",
        "critics": _not_out(now) + f" AND (oc_checked_at IS NULL OR oc_checked_at < {since})",
        "completions": "status = '' AND COALESCE(status_manual, 0) = 0 AND COALESCE(early_access, 0) = 0 AND id IN (SELECT game_id FROM entries "
                       "WHERE platform IN ('steam', 'playstation') AND playtime_min > 0 AND NOT COALESCE(excluded, 0)) "
                       f"AND (completion_checked_at IS NULL OR completion_checked_at < {since})",
    }


def job_progress(job: dict) -> float:
    """How far a remembered full refresh got (0..1), counting games per step."""
    since, now = job["since"], db.now()
    c = _resume_conds(since, now)
    wanted = {"info": ["details"], "art": ["art"], "reviews": ["reviews"], "ttb": ["ttb"],
              "critics": ["critics"], "completions": ["completions"]}
    total = left = 0
    games = _count("1=1")
    for step in job.get("steps") or []:
        for key in wanted.get(step, []):
            n = _count(c[key])
            left += n
            total += max(games if key != "completions" else n, n)
    return round(1 - left / total, 3) if total else 1.0


def _count(cond: str, scope: str = "", args=()) -> int:
    return db.query(f"SELECT COUNT(*) n FROM games WHERE {cond}{scope}", args)[0]["n"]


def _plan(refresh: bool, scope: str, args, prefer_sgdb: bool, steps=ALL_STEPS):
    """Estimate every step's work up front so the bar covers the whole sync."""
    if not _tracking():
        return
    c = _conds(refresh)
    use_igdb = igdb.configured() and "info" in steps
    use_sgdb = sgdb.configured()
    counts = {
        "match": _count(c["match"], scope, args) if use_igdb else 0,
        # games identified in the first step need details too
        "details": (_count(c["details"], scope, args)
                    + _count(f"({c['match']}) AND igdb_id IS NULL", scope, args)) if use_igdb else 0,
        "art": _count(c["art"], scope, args) if "art" in steps else 0,
        "reviews": _count(c["reviews"], scope, args) if "reviews" in steps else 0,
        "ttb": _count(c["ttb"], scope, args) if "ttb" in steps else 0,
        "critics": _count(c["critics"], scope, args) if ("critics" in steps and opencritic.configured()) else 0,
        "completions": _count(c["completions"], *((scope, args) if scope.startswith(" AND id IN") else ("", [])))
                       if "completions" in steps else 0,
    }
    # Rough seconds per game for each step.
    art_weight = 0.25  # Steam art, plus a quick check that the cover is portrait
    if use_sgdb and counts["art"]:
        slow = counts["art"] if prefer_sgdb else _count(
            f"({c['art']}) AND NOT (steam_appid IS NOT NULL AND id IN "
            f"(SELECT game_id FROM entries WHERE platform='steam'))", scope, args)
        art_weight = 0.25 + 1.0 * slow / counts["art"]
    weights = {
        "match": 0.4,
        "details": 0.03,
        "art": art_weight,
        "reviews": 0.5,
        "ttb": 1.3 if hltb.available() else 0.03,
        "critics": 0.35,
        "completions": 0.4,
    }
    # Past today's OpenCritic allowance the rest are skipped in an instant.
    if counts["critics"] and opencritic.configured():
        try:
            left = opencritic.budget_left()
            allowed = 0 if left.get("paused") else left.get("requests", 0)
            weights["critics"] *= min(1.0, allowed / counts["critics"])
        except Exception:
            pass
    with _state_lock:
        _state["plan"] = {
            "started": time.time(),
            "steps": {n: {"total": counts[n], "done": 0, "weight": weights[n]} for n, _ in STEPS},
        }


def _rows(sql: str, args):
    return [dict(r) for r in db.query(sql, args)]


def _platforms_of(game_id: int) -> list[str]:
    return [r["platform"] for r in db.query("SELECT DISTINCT platform FROM entries WHERE game_id=?", (game_id,))]


def _assign_igdb(game_id: int, igdb_id: int, state: str):
    """Set a game's IGDB id, merging with any game that already has it."""
    with db.tx() as c:
        other = c.execute("SELECT id FROM games WHERE igdb_id=? AND id<>?", (igdb_id, game_id)).fetchone()
        if other:
            db.merge_games(c, other["id"], game_id)
            return other["id"]
        c.execute("UPDATE games SET igdb_id=?, match_state=? WHERE id=?", (igdb_id, state, game_id))
        return game_id


def _match(refresh: bool, scope: str, args):
    cond = _conds(refresh)["match"]
    todo = _rows(f"SELECT id, title, steam_appid FROM games WHERE {cond}{scope}", args)
    if not todo:
        return
    _stage("match", "Identifying games", len(todo))

    steam_games = [g for g in todo if g["steam_appid"]]
    if steam_games:
        try:
            mapping = igdb.steam_to_igdb([g["steam_appid"] for g in steam_games])
        except Exception as e:
            _log(f"IGDB: {e}", "error")
            return
        for g in steam_games:
            if g["steam_appid"] in mapping:
                _assign_igdb(g["id"], mapping[g["steam_appid"]], "auto")
                g["done"] = True
            _tick()

    for g in todo:
        if _cancel.is_set():
            return
        if g.get("done"):
            continue
        if g["steam_appid"] and not db.query("SELECT 1 FROM games WHERE id=?", (g["id"],)):
            continue
        try:
            hit, _score = igdb.best_match(g["title"], _platforms_of(g["id"]))
        except Exception as e:
            _log(f"IGDB search failed for {g['title']}: {e}", "warn")
            hit = None
        if hit:
            _assign_igdb(g["id"], hit["id"], "auto")
        else:
            with db.tx() as c:
                c.execute("UPDATE games SET match_state='none' WHERE id=?", (g["id"],))
        if not g["steam_appid"]:
            _tick()
    unmatched = db.query("SELECT COUNT(*) n FROM games WHERE match_state='none'")[0]["n"]
    if unmatched:
        _log(f"{unmatched} games couldn't be identified automatically. Review them in Sync › Library checks.", "warn")


def _details(refresh: bool, scope: str, args):
    cond = _conds(refresh)["details"]
    todo = _rows(f"SELECT id, igdb_id, steam_appid FROM games WHERE {cond}{scope}", args)
    if not todo:
        return
    _stage("details", "Fetching game details", len(todo))
    ids = [g["igdb_id"] for g in todo]
    try:
        info = igdb.details(ids)
    except Exception as e:
        _log(f"IGDB details: {e}", "error")
        return
    # Find Steam versions of console games too, so they get Steam reviews and art.
    no_steam = [g["igdb_id"] for g in todo if not g["steam_appid"]]
    steam_ids: dict[int, int] = {}
    if no_steam:
        try:
            for i in range(0, len(no_steam), 200):
                chunk = ",".join(str(x) for x in no_steam[i : i + 200])
                for r in igdb.q("external_games", f"fields *; where game = ({chunk}); limit 500;"):
                    src = r.get("external_game_source", r.get("category"))
                    if (src == igdb.STEAM_SOURCE or "store.steampowered.com" in (r.get("url") or "")) \
                            and str(r.get("uid", "")).isdigit():
                        steam_ids.setdefault(r["game"], int(r["uid"]))
        except Exception as e:
            _log(f"IGDB store links: {e}", "warn")
    with db.tx() as c:
        for g in todo:
            d = info.get(g["igdb_id"])
            if d:
                igdb.apply_details(c, g["id"], d)
            appid = steam_ids.get(g["igdb_id"]) or (igdb.steam_appid_from_websites(d) if d else None)
            if appid:
                c.execute("UPDATE games SET steam_appid=COALESCE(steam_appid, ?) WHERE id=?", (appid, g["id"]))
            _tick()
    _log(f"Details updated for {len(info)} games.")


def _art(refresh: bool, scope: str, args, prefer_sgdb: bool = False):
    cond = _conds(refresh)["art"]
    todo = _rows(f"SELECT id, title, steam_appid, art_locks, cover_url, hero_url, logo_url, release_date FROM games WHERE {cond}{scope}", args)
    if not todo:
        return
    use_sgdb = sgdb.configured()
    _stage("art", "Collecting artwork", len(todo))
    # Steam's real asset paths for every game that has a Steam version, in a few batched calls.
    steam_ids = sorted({g["steam_appid"] for g in todo if g["steam_appid"]})
    steam_art: dict[int, dict] = {}
    steam_failed = False
    if steam_ids:
        try:
            steam_art = steamstore.store_assets(steam_ids)
        except Exception as e:
            steam_failed = True
            _log(f"Steam artwork lookup: {e}", "warn")

    def from_steam(appid):
        art = dict(steam_art.get(appid) or {})
        if not art.get("cover"):
            # Older listing without asset data: use the fixed paths, but only files that exist.
            art = {**steamstore.legacy_art(appid), **art}
        return art

    for g in todo:
        if _cancel.is_set():
            return
        art: dict = {}
        failed = steam_failed and bool(g["steam_appid"])
        appid = g["steam_appid"]
        # Steam's official art comes first whenever the game is on Steam (it generally looks
        # best); SteamGridDB fills whatever is missing, and covers console-only games.
        if appid:
            art = from_steam(appid)
            # Some games only have a wide store image where the cover should be, or Steam's
            # stand-in (the banner over a smeared fill): skip those for SteamGridDB, then IGDB.
            if art.get("cover") and (steamstore.is_generated_cover(art["cover"])
                                     or not steamstore.is_portrait_cover(art["cover"])):
                art.pop("cover")
        if use_sgdb and not (art.get("cover") and art.get("hero") and art.get("logo")):
            try:
                year = None
                if g["release_date"]:
                    import datetime
                    year = datetime.datetime.fromtimestamp(g["release_date"], datetime.timezone.utc).year
                sg = sgdb.artwork(g["title"], appid, year)
                if sg.get("cover") and not steamstore.is_portrait_cover(sg["cover"]):
                    sg["cover"] = None  # the wrong shape too: IGDB's or PlayStation's cover is next
                art = {**{k: v for k, v in sg.items() if v}, **{k: v for k, v in art.items() if v}}
            except Exception as e:
                failed = True
                _log(f"SteamGridDB: {e}", "warn")
        if failed and not any(art.values()):
            _tick()  # a service didn't answer: keep the art we had, and try again next time
            continue
        # Art you picked by hand stays put.
        for slot in (g["art_locks"] or "").split(","):
            if slot in ("cover", "hero", "logo"):
                art[slot] = g[f"{slot}_url"]
        # Covers missing everywhere fall back to IGDB / PlayStation art in the app.
        with db.tx() as c:
            c.execute(
                "UPDATE games SET cover_url=?, hero_url=?, logo_url=?, art_at=? WHERE id=?",
                (art.get("cover"), art.get("hero"), art.get("logo"), db.now(), g["id"]),
            )
        _tick()


def _reviews(refresh: bool, scope: str, args):
    cond = _conds(refresh)["reviews"]
    todo = _rows(f"SELECT id, title, steam_appid FROM games WHERE {cond}{scope}", args)
    if not todo:
        return
    _stage("reviews", "Reading Steam user reviews", len(todo))
    found = 0
    for g in todo:
        if _cancel.is_set():
            return
        if not g["steam_appid"]:
            # Owned elsewhere (e.g. PS5): see if the game is also on Steam, for its reviews.
            appid = steamstore.find_app(g["title"])
            with db.tx() as c:
                c.execute("UPDATE games SET steam_appid=COALESCE(steam_appid, ?), steam_checked_at=? WHERE id=?",
                          (appid, db.now(), g["id"]))
            if not appid:
                _tick()
                continue
            g["steam_appid"] = appid
            found += 1
        try:
            r = steamstore.reviews(g["steam_appid"])
        except Exception:
            _tick()  # Steam didn't answer: keep the score we had, and try again next time
            continue
        with db.tx() as c:
            c.execute(
                "UPDATE games SET steam_review_pct=?, steam_review_count=?, steam_review_desc=?, reviews_at=? WHERE id=?",
                (r and r["pct"], r and r["count"], r and r["desc"], db.now(), g["id"]),
            )
        _tick()
    if found:
        _log(f"Found {found} more game{'s' if found != 1 else ''} on Steam for user reviews.")


def _ttb(refresh: bool, scope: str, args):
    cond = _conds(refresh)["ttb"]
    todo = _rows(f"SELECT id, title, igdb_id, release_date, igdb_platforms FROM games WHERE {cond}{scope}", args)
    if not todo:
        return
    _stage("ttb", "Looking up time to beat", len(todo))
    igdb_times: dict[int, dict] = {}
    igdb_failed = False
    if igdb.configured():
        try:
            igdb_times = igdb.times_to_beat([g["igdb_id"] for g in todo if g["igdb_id"]])
        except Exception as e:
            igdb_failed = True
            _log(f"IGDB time-to-beat: {e}", "warn")
    use_hltb = hltb.available()
    if not use_hltb:
        _log("HowLongToBeat isn't available, using IGDB's time-to-beat data instead.", "warn")
    for g in todo:
        if _cancel.is_set():
            return
        t, source = None, None
        if use_hltb:
            names = [r["name"] for r in db.query("SELECT DISTINCT name FROM entries WHERE game_id=?", (g["id"],))
                     if r["name"] and r["name"] != g["title"]]
            import datetime, json as _json
            year = datetime.datetime.fromtimestamp(g["release_date"], datetime.timezone.utc).year if g["release_date"] else None
            try:
                plats = " ".join(_json.loads(g["igdb_platforms"] or "[]")).lower()
            except ValueError:
                plats = ""
            # Arcade / phone-only entries are only right when IGDB says so too.
            niche = bool(plats) and not re.search(r"pc|windows|playstation|xbox|switch|nintendo|mac|linux|steam", plats)
            try:
                t = hltb.lookup(g["title"], names, year=year, pc_or_console=not niche)
            except Exception:
                t, igdb_failed = None, True  # (treated like a failed lookup: keep what we had)
            source = "HowLongToBeat" if t else None
        if not t and g["igdb_id"] in igdb_times:
            t, source = igdb_times[g["igdb_id"]], "IGDB"
        if not t and igdb_failed:
            _tick()  # a service didn't answer: keep the times we had, and try again next time
            continue
        with db.tx() as c:
            c.execute(
                "UPDATE games SET ttb_main=?, ttb_extra=?, ttb_complete=?, ttb_source=?, ttb_at=?, hltb_id=? WHERE id=?",
                (t and t.get("main"), t and t.get("extra"), t and t.get("complete"), source, db.now(),
                 (t or {}).get("id") if source == "HowLongToBeat" else None, g["id"]),
            )
        _tick()


def _critics(refresh: bool, scope: str, args):
    if not opencritic.configured():
        _tl("critics", state="skipped", note="Add an OpenCritic key in Settings")
        return
    cond = _conds(refresh)["critics"]
    # Most recently played first, so the games you care about get scores first.
    todo = _rows(f"""SELECT g.id, g.title, g.steam_appid, g.igdb_url, g.oc_id FROM games g
                  LEFT JOIN (SELECT game_id, MAX(last_played) lp,
                                    MIN(CASE WHEN ownership IN ('catalog', 'wishlist') THEN 1 ELSE 0 END) cat
                             FROM entries GROUP BY game_id) e ON e.game_id = g.id
                  WHERE {cond.replace("release_date", "g.release_date")}{scope.replace(" id", " g.id")}
                  ORDER BY COALESCE(e.cat, 0), COALESCE(e.lp, 0) DESC""", args)
    if not todo:
        return
    _stage("critics", "Reading OpenCritic scores", len(todo))
    _tl("critics", note="Matching your games to OpenCritic")
    ids = opencritic.id_map()
    _tl("critics", note=None)
    if ids.get("error"):
        _log(f"Couldn't get OpenCritic matches from Wikidata ({ids['error']}), so each game needs a search, "
             f"and {APP_NAME} makes at most {opencritic.DAILY_SEARCHES} OpenCritic searches a day.", "warn")
    done = found = waiting = 0
    searches_left = True
    try:
        for g in todo:
            if _cancel.is_set():
                return
            oc_id = g["oc_id"] or ids.get("steam", {}).get(g["steam_appid"]) \
                or ids.get("igdb", {}).get(opencritic.igdb_slug(g["igdb_url"]) or "")
            if not oc_id:
                if not searches_left:
                    waiting += 1  # needs a search: leave it for another day
                    _tick()
                    continue
                try:
                    oc_id = opencritic.search(g["title"])
                except opencritic.BudgetSpent as e:
                    if not e.searches_only:
                        raise
                    searches_left = False
                    waiting += 1
                    _tick()
                    continue
            sc = opencritic.scores(oc_id) if oc_id else None
            with db.tx() as c:
                if sc:
                    c.execute("""UPDATE games SET oc_id=?, oc_score=?, oc_recommend=?, oc_tier=?, oc_count=?, oc_url=?,
                                 oc_at=?, oc_checked_at=? WHERE id=?""",
                              (oc_id, sc["score"], sc["recommend"], sc["tier"], sc["count"], sc["url"],
                               db.now(), db.now(), g["id"]))
                    found += 1
                else:
                    c.execute("UPDATE games SET oc_checked_at=? WHERE id=?", (db.now(), g["id"]))
            done += 1
            _tick()
    except opencritic.BudgetSpent:
        left = len(todo) - done  # (games waiting for a search are among them)
        _tl("critics", state="skipped", note=f"Daily limit reached, {left:,} for another day")
        _log(f"{APP_NAME} reads at most {opencritic.DAILY_REQUESTS} OpenCritic scores a day (to stay inside the free plan). "
             f"{left:,} games will be picked up by your next sync or Refresh assets on another day.", "warn")
    except RuntimeError as e:
        _log(f"OpenCritic: {e}", "error")
        return
    except net.HttpError as e:  # OpenCritic having a bad moment: no retry (the free plan counts every call)
        _tl("critics", state="skipped", note="OpenCritic didn't answer")
        _log(f"OpenCritic didn't answer ({e}). The rest will be picked up on another day.", "warn")
        return
    if waiting and searches_left is False and done + waiting == len(todo):
        _tl("critics", note=f"{waiting:,} need a search, for another day")
        _log(f"{waiting:,} games need an OpenCritic search ({opencritic.DAILY_SEARCHES} a day); they'll be picked up on another day.", "warn")
    if found:
        _log(f"OpenCritic scores for {found:,} game{'s' if found != 1 else ''}.")


def _completions(refresh: bool, scope: str, args):
    cond = _conds(refresh)["completions"]
    todo = _rows(f"""SELECT g.id, g.title,
                       (SELECT external_id FROM entries WHERE game_id=g.id AND platform='steam' AND playtime_min > 0 LIMIT 1) appid,
                       (SELECT name FROM entries WHERE game_id=g.id AND platform='playstation' AND playtime_min > 0 LIMIT 1) psn_name
                     FROM games g WHERE {cond.replace("games.", "g.")}{scope.replace(" id", " g.id")}""", args)
    if not todo:
        return
    _stage("completions", "Checking achievements and trophies", len(todo))
    st = ps = None
    if any(g["appid"] for g in todo) and achievements.configured_steam():
        try:
            st = achievements.SteamAch()
        except Exception as e:
            _log(f"Steam achievements: {e}", "warn")
    if any(g["psn_name"] for g in todo) and achievements.configured_psn():
        try:
            ps = achievements.PsnTrophies()
        except Exception as e:
            _log(f"PlayStation trophies: {e}", "warn")
    found = []
    lib = {x["id"]: x for x in db.library()}
    for g in todo:
        if _cancel.is_set():
            return
        if g["id"] in lib and rules.is_open_ended(lib[g["id"]]):
            # No finish line, so never "completed" (an achievement for one campaign doesn't change that).
            with db.tx() as c:
                c.execute("UPDATE games SET completion_checked_at=? WHERE id=?", (db.now(), g["id"]))
            _tick()
            continue
        ev = None
        try:
            if st and g["appid"]:
                ev = st.check(int(g["appid"]))
            if not ev and ps and g["psn_name"]:
                ev = ps.check(g["psn_name"]) or (ps.check(g["title"]) if g["title"] != g["psn_name"] else None)
        except Exception as e:
            _log(f"{g['title']}: {e}", "warn")
        with db.tx() as c:
            if ev:
                # Re-check the condition: you may have marked it yourself meanwhile.
                c.execute("""UPDATE games SET status='completed', status_at=?, status_auto=?, completion_checked_at=?
                             WHERE id=? AND status='' AND COALESCE(status_manual, 0)=0""",
                          (ev["at"] or db.now(), ev["why"], db.now(), g["id"]))
                found.append(g["title"])
            else:
                c.execute("UPDATE games SET completion_checked_at=? WHERE id=?", (db.now(), g["id"]))
        _tick()
    if found:
        names = ", ".join(found[:5]) + (f" and {len(found) - 5} more" if len(found) > 5 else "")
        _log(f"Marked {len(found)} game{'s' if len(found) != 1 else ''} completed from your achievements: {names}.")
        _tl("completions", note=f"{len(found):,} marked completed")


def recheck_auto_completions() -> int:
    """Undo automatic "completed" marks that today's rules wouldn't make: open-ended games, games in early access,
    and achievements that turned out not to mean the end (L.A. Noire's "Complete a story
    case"). Statuses you set yourself are never touched. Runs at start-up; cheap."""
    undone = 0
    for g in db.library():
        if g.get("status") != "completed" or not g.get("status_auto") or g.get("status_manual"):
            continue
        if rules.is_open_ended(g) or g.get("early_access") == 1 or not achievements.still_valid(g["status_auto"]):
            with db.tx() as c:
                c.execute("""UPDATE games SET status='', status_at=NULL, status_auto=NULL, completion_checked_at=NULL
                             WHERE id=? AND COALESCE(status_manual, 0)=0""", (g["id"],))
            undone += 1
    return undone


# ------------------------------------------------------------------ one game

def refresh_one(game_id: int, prefer_sgdb: bool = False):
    """Re-fetch everything for a single game (after fixing a match, or on request)."""
    with db.tx() as c:
        c.execute("UPDATE games SET meta_at=NULL, art_at=NULL, reviews_at=NULL, ttb_at=NULL, hltb_id=NULL WHERE id=?",
                  (game_id,))
        # A Steam version found by search (not one you own on Steam) is looked for again, so a
        # wrong one (Watch Dogs 2's for Watch Dogs) doesn't keep feeding art and reviews.
        own = c.execute("SELECT external_id FROM entries WHERE game_id=? AND platform='steam' LIMIT 1",
                        (game_id,)).fetchone()
        appid = int(own["external_id"]) if own and str(own["external_id"]).isdigit() else None
        if not appid:  # the Steam page on your wishlist is right too
            w = c.execute("SELECT steam_appid FROM wishlist WHERE game_id=? AND steam_appid IS NOT NULL LIMIT 1",
                          (game_id,)).fetchone()
            appid = w and w["steam_appid"]
        c.execute("UPDATE games SET steam_appid=?, steam_checked_at=NULL WHERE id=?", (appid, game_id))
    enrich(refresh=False, game_ids=[game_id], prefer_sgdb=prefer_sgdb,
           steps=[x for x in ALL_STEPS if x != "completions"])


def rematch(game_id: int, igdb_id: int) -> int:
    hit = igdb.details([igdb_id]).get(igdb_id)  # before taking the database (IGDB can be slow)
    new_id = _assign_igdb(game_id, igdb_id, "manual")
    with db.tx() as c:
        # A different game: its critic score is looked up again too.
        c.execute("UPDATE games SET match_state='manual', oc_id=NULL, oc_score=NULL, oc_recommend=NULL, oc_tier=NULL, "
                  "oc_count=NULL, oc_url=NULL, oc_at=NULL, oc_checked_at=NULL WHERE id=?", (new_id,))
        if hit and hit.get("name"):
            c.execute("UPDATE games SET title=? WHERE id=?", (hit["name"], new_id))
    refresh_one(new_id)
    return new_id


def estimate() -> dict:
    """What a "fill in missing" refresh would pick up, per step."""
    c = _conds(False)
    return {
        "games": _count("1=1"),
        "info": _count(c["match"]) + _count(c["details"]),
        "unidentified": _count("match_state = 'none'"),
        "art": _count(c["art"]),
        "reviews": _count(c["reviews"]),
        "ttb": _count(c["ttb"]),
        "critics": _count(c["critics"]),
        "completions": _count(c["completions"]),
        "opencritic": opencritic.configured(),
        "oc_left": opencritic.budget_left() if opencritic.configured() else None,
        "igdb": igdb.configured(),
        "sgdb": sgdb.configured(),
        "hltb": hltb.available(),
        "job": _job_view(),
    }


def _job_view() -> dict | None:
    job = refresh_job()
    if not job:
        return None
    return {**job, "progress": job_progress(job)}


def art_options(game_id: int) -> dict:
    """Everything we can offer for a game's cover, banner and logo."""
    g = db.query("SELECT * FROM games WHERE id=?", (game_id,))
    if not g:
        return {}
    g = dict(g[0])
    out: dict[str, list] = {"cover": [], "hero": [], "logo": []}
    notes = []

    def add(slot, items):
        seen = {o["url"] for o in out[slot]}
        for it in items or []:
            if it.get("url") and it["url"] not in seen:
                out[slot].append(it)
                seen.add(it["url"])

    if g["steam_appid"]:
        try:
            st = steamstore.store_assets([g["steam_appid"]]).get(g["steam_appid"]) or {}
            for slot in ("cover", "hero", "logo"):
                if st.get(slot):
                    add(slot, [{"url": st[slot], "thumb": st[slot], "source": "Steam"}])
        except Exception:
            pass
    if g["igdb_id"] and igdb.configured():
        try:
            io = igdb.art_options(g["igdb_id"])
            add("cover", io.get("cover"))
            add("hero", io.get("hero"))
        except Exception:
            pass
    if g["psn_image"]:
        add("cover", [{"url": g["psn_image"], "thumb": g["psn_image"], "source": "PlayStation"}])
    if sgdb.configured():
        try:
            so = sgdb.options(g["title"], g["steam_appid"])
            for slot in ("cover", "hero", "logo"):
                add(slot, so.get(slot))
        except Exception as e:
            notes.append(f"SteamGridDB: {e}")
    else:
        notes.append("Add a SteamGridDB key in Settings for many more choices, especially logos and banners.")
    locks = [x for x in (g["art_locks"] or "").split(",") if x]
    current = {"cover": g["cover_url"], "hero": g["hero_url"], "logo": g["logo_url"]}
    return {"options": out, "current": current, "locks": locks, "notes": notes}


def set_art(game_id: int, slot: str, url: str | None) -> None:
    """Pick a slot's image by hand (locked), or pass None to go back to automatic."""
    row = db.query("SELECT art_locks FROM games WHERE id=?", (game_id,))
    if not row or slot not in ("cover", "hero", "logo"):
        return
    locks = {x for x in (row[0]["art_locks"] or "").split(",") if x}
    if url:
        locks.add(slot)
        with db.tx() as c:
            c.execute(f"UPDATE games SET {slot}_url=?, art_locks=? WHERE id=?", (url, ",".join(sorted(locks)), game_id))
    else:
        locks.discard(slot)
        with db.tx() as c:
            c.execute("UPDATE games SET art_locks=?, art_at=NULL WHERE id=?", (",".join(sorted(locks)) or None, game_id))
        enrich(game_ids=[game_id], steps=["art"])
