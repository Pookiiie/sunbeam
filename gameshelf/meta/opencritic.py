"""OpenCritic critic scores, through OpenCritic's official API on RapidAPI.

The free "Basic" plan allows 200 requests and 25 searches a day, so:
- Game IDs come from Wikidata (one free bulk query maps thousands of games to
  their OpenCritic page by Steam app ID or IGDB slug), saving searches.
- A daily budget is kept in the database; when it runs out, the rest of the
  library is picked up by the next sync or refresh on a later day.
"""

from __future__ import annotations

import datetime
import difflib
import re
import time

from .. import brand, db, net, settings

HOST = "opencritic-api.p.rapidapi.com"
# The free plan allows 200 requests and 25 searches a day, and RapidAPI can bill for going over.
# The app stays well under: its own daily cap, plus RapidAPI's own "remaining" counts, which
# it reads from every response and obeys (stopping once 10% or less of the allowance is left, 20 of 200).
# 180 a day (raised from 140 at the user's request, Oct 2026) leaves a margin of 20.
DAILY_REQUESTS = 180
DAILY_SEARCHES = 15
KEEP_FREE = 0.10
WIKIDATA = "https://query.wikidata.org/sparql"
# Wikidata properties: OpenCritic ID, IGDB game ID (slug), Steam application ID
SPARQL = """SELECT ?oc ?igdb ?steam WHERE {
  ?item wdt:P2864 ?oc .
  OPTIONAL { ?item wdt:P5794 ?igdb . }
  OPTIONAL { ?item wdt:P1733 ?steam . }
}"""

# One call a second. (It was 3.5 a second until Oct 2026: RapidAPI answered "too many requests"
# after a quick burst of searches, and that used to pause lookups for the rest of the day.)
_limiter = net.RateLimiter(1.0)
COOL_DOWN = 15 * 60  # after a "slow down" from RapidAPI, wait this long before the next lookup
LEGACY_STOP = "RapidAPI said the limit was reached"  # written by 1.6.1-beta.3 and earlier for any 429
_map_cache: dict = {}


class BudgetSpent(Exception):
    """Today's allowance is used up. `searches_only` means plain lookups can continue.
    `cooling` means RapidAPI asked to slow down: lookups carry on in a few minutes, not tomorrow."""
    def __init__(self, searches_only: bool = False, cooling: bool = False):
        super().__init__("OpenCritic asked to slow down" if cooling else "OpenCritic daily limit reached")
        self.searches_only = searches_only
        self.cooling = cooling


def configured() -> bool:
    return bool(settings.get("opencritic_key"))


# ------------------------------------------------------------------ daily budget

def _today() -> str:
    # RapidAPI's daily quotas reset on UTC days.
    return datetime.datetime.now(datetime.timezone.utc).date().isoformat()


def _meta() -> dict:
    return {r["key"]: r["value"] for r in db.query("SELECT key, value FROM meta WHERE key LIKE 'oc_%'")}


def _save(**values) -> None:
    with db.tx() as c:
        for k, v in values.items():
            c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (f"oc_{k}", str(v)))


def _budget() -> dict:
    m = _meta()
    if m.get("oc_day") != _today():
        return {"day": _today(), "requests": 0, "searches": 0, "stop": "", "stop_searches": ""}
    stop = m.get("oc_stop", "")
    if stop == LEGACY_STOP:
        stop = ""  # older versions paused the whole day on any "too many requests", even a "slow down"
    return {"day": m["oc_day"], "requests": int(m.get("oc_requests", 0)), "searches": int(m.get("oc_searches", 0)),
            "stop": stop, "stop_searches": m.get("oc_stop_searches", "")}


def cooling_until() -> float:
    """When lookups may resume after RapidAPI asked to slow down (0 = they may now)."""
    try:
        t = float(_meta().get("oc_cool_until") or 0)
    except ValueError:
        t = 0
    return t if t > time.time() else 0


_spend_lock = __import__("threading").Lock()  # a sync and a single-game refresh can look up at once


def _spend(search: bool = False) -> None:
    """Count a request before it's sent; refuse if today's allowance is (nearly) used."""
    with _spend_lock:
        _spend_locked(search)


def _spend_locked(search: bool) -> None:
    b = _budget()
    if b["stop"] or b["requests"] >= DAILY_REQUESTS:
        raise BudgetSpent()
    if cooling_until():
        raise BudgetSpent(cooling=True)
    if search and (b["stop_searches"] or b["searches"] >= DAILY_SEARCHES):
        raise BudgetSpent(searches_only=True)
    new_day = _meta().get("oc_day") != b["day"]
    b["requests"] += 1
    if search:
        b["searches"] += 1
    if new_day:
        # A new (UTC) day starts with a clean slate: yesterday's "paused" note must not carry over.
        # (It used to: the day changed, the old note stayed, and lookups stopped after the first one.)
        _save(day=b["day"], requests=b["requests"], searches=b["searches"], stop="", stop_searches="")
    else:
        _save(day=b["day"], requests=b["requests"], searches=b["searches"])


def _obey_headers(headers) -> None:
    """RapidAPI reports what's left (x-ratelimit-<quota>-remaining / -limit). Stop early when low."""
    if headers is None:
        return
    h = {k.lower(): v for k, v in headers.items()}
    seen = {}
    for k, v in h.items():
        m = re.match(r"x-ratelimit-(.+)-remaining$", k)
        if not m or not str(v).strip().lstrip("-").isdigit():
            continue
        name = m.group(1)
        remaining = int(v)
        limit = h.get(f"x-ratelimit-{name}-limit")
        limit = int(limit) if limit and str(limit).isdigit() else None
        seen[name] = (remaining, limit)
        low = remaining <= (limit * KEEP_FREE if limit else 10)
        if low:
            if "search" in name:
                _save(stop_searches=f"{name}: {remaining} of {limit or '?'} left")
            else:
                _save(stop=f"{name}: {remaining} of {limit or '?'} left")
    if seen:
        _save(quota="; ".join(f"{n} {r}/{l or '?'}" for n, (r, l) in seen.items()))


def budget_left() -> dict:
    b = _budget()
    m = _meta()
    left_r = 0 if b["stop"] else max(DAILY_REQUESTS - b["requests"], 0)
    left_s = 0 if (b["stop"] or b["stop_searches"]) else max(DAILY_SEARCHES - b["searches"], 0)
    return {"requests": left_r, "searches": left_s, "used_today": b["requests"],
            "rapidapi": m.get("oc_quota") if m.get("oc_day") == _today() else None,
            "paused": bool(b["stop"]), "cooling_until": int(cooling_until()) or None}


def _get(path: str, params: dict | None = None, search: bool = False):
    _spend(search)
    _limiter.wait()
    url = f"https://{HOST}{path}"
    try:
        # No automatic retries: every attempt counts against the plan.
        resp = net.request("GET", url, params=params, retries=0,
                           headers={"x-rapidapi-key": settings.get("opencritic_key"), "x-rapidapi-host": HOST})
        _obey_headers(resp.headers)
        return resp.json()
    except net.HttpError as e:
        if e.status == 429:
            # RapidAPI says 429 both for "that's all for today" and for "too many at once". Only the first
            # pauses until tomorrow; the second waits a few minutes. (Not retried either way.)
            body = (e.body or b"")[:400].decode("utf-8", "replace")
            _save(last_429=f"{int(time.time())} {body}")
            if re.search(r"daily|quota|month", body, re.I) and not re.search(r"per (second|minute|hour)|rate limit", body, re.I):
                _save(stop=f"RapidAPI: {body[:120] or 'limit reached'}")
                raise BudgetSpent() from None
            _save(cool_until=str(int(time.time() + COOL_DOWN)))
            raise BudgetSpent(cooling=True) from None
        if e.status in (401, 403):
            raise RuntimeError("OpenCritic (RapidAPI) rejected the key, or you're not subscribed to the "
                               "OpenCritic API's Basic plan yet.") from None
        if e.status == 404:
            return None
        raise


# ------------------------------------------------------------------ finding games

# Wikidata asks for a descriptive client name (generic or browser-like ones get refused).
WIKIDATA_UA = f"{brand.NAME}/1.0 (personal game library app running locally; https://www.wikidata.org/wiki/Property:P2864)"


def id_map() -> dict:
    """{"steam": {appid: oc_id}, "igdb": {slug: oc_id}, "error": str|None} from Wikidata, cached for a day."""
    if _map_cache.get("at", 0) > time.time() - 86400 and (_map_cache.get("steam") or _map_cache.get("igdb")):
        return _map_cache
    steam, igdb, error = {}, {}, None
    try:
        data = net.request(
            "POST", WIKIDATA, data={"query": SPARQL, "format": "json"},
            headers={"Accept": "application/sparql-results+json", "User-Agent": WIKIDATA_UA},
            timeout=40, retries=1,
        ).json()
        for b in ((data or {}).get("results") or {}).get("bindings") or []:
            m = re.match(r"(\d+)", (b.get("oc") or {}).get("value", ""))
            if not m:
                continue
            oc = int(m.group(1))
            if (b.get("steam") or {}).get("value", "").isdigit():
                steam.setdefault(int(b["steam"]["value"]), oc)
            if (b.get("igdb") or {}).get("value"):
                igdb.setdefault(b["igdb"]["value"].strip().lower(), oc)
    except net.HttpError as e:
        error = f"HTTP {e.status}" if e.status else "no response"
    except ValueError:
        error = "unexpected response"
    _map_cache.update(steam=steam, igdb=igdb, error=error, at=time.time())
    return _map_cache


def igdb_slug(igdb_url: str | None) -> str | None:
    m = re.search(r"igdb\.com/games/([^/?#]+)", igdb_url or "")
    return m.group(1).lower() if m else None


# OpenCritic started in 2015 and covers few older games; demos, playtests and the like aren't on it.
FIRST_YEAR_TS = 1388534400  # 1 Jan 2014: a little before, for games reviewed when it launched
NOT_ON_OC = re.compile(r"\b(demo|playtest|test server|public test|beta|soundtrack|OST|dedicated server|"
                       r"benchmark|season pass|expansion pass)\b", re.I)


def worth_searching(title: str, release_ts: int | None) -> bool:
    """Whether a search (15 a day) could plausibly find this game. Matches from Wikidata cost no search."""
    if release_ts and release_ts < FIRST_YEAR_TS:
        return False
    return not NOT_ON_OC.search(title or "")


def search(title: str) -> int | None:
    data = _get("/game/search", {"criteria": db.clean_display_title(title)}, search=True) or []
    target = db.normalize_title(title)
    best, best_sim = None, 0.0
    for item in data:
        sim = difflib.SequenceMatcher(None, target, db.normalize_title(item.get("name") or "")).ratio()
        if sim > best_sim:
            best, best_sim = item, sim
    return int(best["id"]) if best and best_sim >= 0.9 else None


def scores(oc_id: int) -> dict | None:
    g = _get(f"/game/{int(oc_id)}")
    if not g:
        return None

    def num(v):
        return round(float(v), 1) if isinstance(v, (int, float)) and v >= 0 else None

    return {
        "score": num(g.get("topCriticScore")),
        "recommend": num(g.get("percentRecommended")),
        "tier": g.get("tier") or None,
        "count": g.get("numReviews") or None,
        "url": g.get("url") or f"https://opencritic.com/game/{int(oc_id)}/-",
    }


def test() -> str:
    left = budget_left()
    if left["paused"] or not left["requests"]:
        return (f"Key saved. {brand.NAME} has paused OpenCritic lookups for today to stay inside the free plan; "
                "they resume tomorrow (UTC).")
    hit = scores(463)  # one lookup of a well-known game, to prove the key works
    if hit is None:
        return "Connected, but a test lookup returned nothing."
    left = budget_left()
    extra = f" RapidAPI reports: {left['rapidapi']}." if left.get("rapidapi") else ""
    return f"Connected to OpenCritic. {brand.NAME} will use at most {left['requests']} more lookups today.{extra}"
