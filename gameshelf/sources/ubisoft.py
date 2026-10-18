"""Ubisoft Connect, through the account service Ubisoft's own launcher and website use
(the approach of GOG Galaxy's Ubisoft plugin, github.com/FriendsOfGalaxy/galaxy-integration-uplay).

Signing in: since 2026 Ubisoft's sign-in service only answers real browsers (bot
protection; scripts get "429 Too many requests"), so the app can't sign in by
itself. Instead, a "Send to <app>" bookmark does it from your own browser: on
connect.ubisoft.com, while signed in (two-step codes work as usual there), it
reads your games and their stats cards and opens the app with them, along with
Ubisoft's "stay signed in" pass. Later syncs try to refresh straight from Ubisoft
with that pass; if Ubisoft refuses, the last list sent by the bookmark is used.

Gives the PC games you own, with hours played and last played from each game's
Ubisoft stats card (most Ubisoft games keep one). On this PC, the Ubisoft Connect
app's save-game folders also tell when you last played, which fills in games
without a stats card.
"""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from .. import brand, db, net, settings

APP_ID = "b8fde481-327d-4031-85ce-7c10a202a700"  # Ubisoft Connect website
SESSIONS = "https://public-ubiservices.ubi.com/v3/profiles/sessions"
GRAPHQL = "https://public-ubiservices.ubi.com/v1/profiles/me/uplay/graphql"
STATS = "https://public-ubiservices.ubi.com/v1/profiles/{user}/statscard"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
BASE_HEADERS = {"Ubi-AppId": APP_ID, "User-Agent": UA, "Origin": "https://connect.ubisoft.com",
                "Referer": "https://connect.ubisoft.com/", "Accept": "application/json"}


def _base() -> dict:
    """Ubisoft passes belong to the site that issued them: use the bookmark's app id."""
    return {**BASE_HEADERS, "Ubi-AppId": settings.get("ubi_app") or APP_ID}

OWNED_QUERY = (
    "query AllGames { viewer { id ...ownedGamesList } }"
    " fragment gameProps on Game { id spaceId name }"
    " fragment ownedGameProps on Game { ...gameProps viewer { meta { id ownedPlatformGroups { id name type } } } }"
    " fragment ownedGamesList on User { ownedGames: games(filterBy: {isOwned: true}) { totalCount nodes { ...ownedGameProps } } }"
)


class UbisoftError(Exception):
    pass


# ------------------------------------------------------------------ signing in

def _save_session(j: dict) -> dict:
    if not j.get("ticket"):
        raise UbisoftError("Ubisoft didn't send a sign-in pass.")
    expires = int(time.time()) + 3 * 3600
    try:
        exp = datetime.fromisoformat(j["expiration"].replace("Z", "+00:00"))
        srv = datetime.fromisoformat(j["serverTime"].replace("Z", "+00:00"))
        expires = int(time.time() + (exp - srv).total_seconds())
    except (KeyError, ValueError, AttributeError):
        pass
    settings.update({
        "ubi_ticket": j["ticket"], "ubi_session": j.get("sessionId") or "",
        "ubi_remember": j.get("rememberMeTicket") or settings.get("ubi_remember") or "",
        "ubi_user": j.get("userId") or settings.get("ubi_user") or "",
        "ubi_name": j.get("nameOnPlatform") or settings.get("ubi_name") or "",
        "ubi_expires": expires,
    })
    return j


def _post_session(auth: str, extra: dict | None = None) -> dict:
    try:
        resp = net.request("POST", SESSIONS, json_body={"rememberMe": True}, retries=0, timeout=30,
                           headers={**_base(), "Authorization": auth, **(extra or {})})
    except net.HttpError as e:
        if e.status in (400, 401, 403, 429):
            raise UbisoftError("Ubisoft didn't accept the request.") from None
        if e.status == 0:
            raise UbisoftError("Couldn't reach Ubisoft. Check your internet connection and try again.") from None
        raise
    return resp.json() or {}


def _session_headers() -> dict:
    """A valid ticket: the saved one, renewed with the remember-me pass when it's old."""
    ticket = settings.get("ubi_ticket")
    if not ticket or int(settings.get("ubi_expires") or 0) < time.time() + 120:
        remember = settings.get("ubi_remember")
        if not remember:
            raise UbisoftError(f"Link Ubisoft Connect in Settings first (the Send to {brand.NAME} bookmark).")
        try:
            _save_session(_post_session(f"rm_v1 t={remember}"))
        except UbisoftError:
            raise UbisoftError(f"Ubisoft didn't let {brand.NAME} refresh by itself.") from None
    return {**_base(), "Authorization": f"Ubi_v1 t={settings.get('ubi_ticket')}",
            "Ubi-SessionId": settings.get("ubi_session") or ""}


# ------------------------------------------------------------------ your games

def _owned(headers: dict) -> list[dict]:
    resp = net.request("POST", GRAPHQL, json_body={"operationName": "AllGames", "variables": {"owned": True},
                                                   "query": OWNED_QUERY}, headers=headers, retries=1, timeout=60)
    data = resp.json() or {}
    if data.get("errors") and not data.get("data"):
        raise UbisoftError(f"Ubisoft's game list answered with an error: {data['errors'][0].get('message', '')}")
    nodes = ((((data.get("data") or {}).get("viewer") or {}).get("ownedGames") or {}).get("nodes")) or []
    games = []
    for n in nodes:
        groups = (((n.get("viewer") or {}).get("meta") or {}).get("ownedPlatformGroups")) or []
        types = set()
        for g in groups:  # a list of lists in practice; tolerate a flat list too
            for p in (g if isinstance(g, list) else [g]):
                if isinstance(p, dict):
                    types.add(str(p.get("type") or "").upper())
        if types and "PC" not in types:
            continue  # owned on a console through a linked account
        if n.get("name"):
            games.append(n)
    return games


def _minutes(card: dict) -> float | None:
    try:
        v = float(card.get("value") or 0)
    except (TypeError, ValueError):
        return None
    return {"Hours": v * 60, "Minutes": v, "Seconds": v / 60, "Miliseconds": v / 60000,
            "Milliseconds": v / 60000}.get(card.get("unit"))


def _times(cards: list[dict]) -> tuple[int, int | None]:
    """(minutes played, last played) from a stats card, like GOG Galaxy's plugin reads it."""
    last = None
    spans = []
    for c in cards:
        try:
            ts = int(datetime.fromisoformat(str(c.get("lastModified")).replace("Z", "+00:00")).timestamp())
            last = max(last or 0, ts) if ts >= 1072915200 else last
        except (ValueError, TypeError):
            pass
        if c.get("format") == "LongTimespan":
            spans.append(c)
    picked = []
    names = ("playtime", "time played", "play time", "total play time", "total playtime")
    if len(spans) == 1:
        picked = spans
    elif spans:
        picked = [c for c in spans if str(c.get("displayName", "")).lower() in names][:1]
        if not picked:
            totals = [c for c in spans if any(w in (str(c.get("displayName", "")) + str(c.get("statName", ""))).lower()
                                              for w in ("all", "total", "absolute"))]
            picked = totals[:1] or (spans if len(spans) == 2 else [])  # e.g. solo + co-op timers
    mins = sum(m for m in (_minutes(c) for c in picked) if m)
    return int(mins), last


def _stats(headers: dict, user: str, space_id: str) -> tuple[int, int | None]:
    try:
        resp = net.request("GET", STATS.format(user=user), params={"spaceId": space_id}, retries=0, timeout=30,
                           headers={**headers, "Ubi-RequestedPlatformType": "uplay", "Ubi-LocaleCode": "en-US"})
    except net.HttpError:
        return 0, None  # no stats card for this game
    return _times((resp.json() or {}).get("Statscards") or [])


# ------------------------------------------------------------------ this PC

def _launcher_dir() -> Path | None:
    try:
        import winreg
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Ubisoft\Launcher", 0,
                                    winreg.KEY_READ | view) as k:
                    p = Path(winreg.QueryValueEx(k, "InstallDir")[0])
                    if p.exists():
                        return p
            except OSError:
                continue
    except ImportError:
        pass
    default = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Ubisoft" / "Ubisoft Game Launcher"
    return default if default.exists() else None


def _install_names() -> dict[str, str]:
    """{launcher product id: game folder name} for games installed on this PC."""
    out = {}
    try:
        import winreg
    except ImportError:
        return out
    for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Ubisoft\Launcher\Installs", 0,
                                winreg.KEY_READ | view) as k:
                for i in range(winreg.QueryInfoKey(k)[0]):
                    pid = winreg.EnumKey(k, i)
                    try:
                        with winreg.OpenKey(k, pid) as sub:
                            d = winreg.QueryValueEx(sub, "InstallDir")[0]
                            out[pid] = Path(str(d).rstrip("\\/")).name
                    except OSError:
                        pass
        except OSError:
            continue
    return out


def local_last_played() -> dict[str, int]:
    """{normalized title: unix time}, from when each installed game last wrote a save."""
    root = _launcher_dir()
    user = settings.get("ubi_user")
    names = _install_names()
    if not root or not user or not names:
        return {}
    saves = root / "savegames" / user
    out = {}
    for pid, name in names.items():
        folder = saves / pid
        if not folder.is_dir():
            continue
        try:
            newest = max((f.stat().st_mtime for f in folder.rglob("*") if f.is_file()), default=0)
        except OSError:
            continue
        if newest >= 1072915200:
            out[db.normalize_title(name)] = int(newest)
    return out


# ------------------------------------------------------------------ import

SNAPSHOT = "ubisoft.json"  # in the data folder: the last list the bookmark (or a sync) got
notice: str | None = None    # read by the sync log after fetch()


def _snapshot_path() -> Path:
    from ..paths import DATA_DIR
    return DATA_DIR / SNAPSHOT


def _items(games: list[dict], times: list[tuple[int, int | None]]) -> list[dict]:
    return [{"platform": "ubisoft", "external_id": str(g.get("id") or g.get("spaceId")), "name": g["name"],
             "playtime_min": mins, "last_played": last, "first_played": None, "ownership": None}
            for g, (mins, last) in zip(games, times)]


def _save_snapshot(items: list[dict], source: str) -> None:
    _snapshot_path().write_text(json.dumps({"at": int(time.time()), "source": source, "games": items}), "utf-8")


def _load_snapshot() -> dict | None:
    try:
        return json.loads(_snapshot_path().read_text("utf-8"))
    except (OSError, ValueError):
        return None


def link(payload: dict) -> dict:
    """What the Send to <app> bookmark sends from connect.ubisoft.com."""
    if not isinstance(payload, dict) or not isinstance(payload.get("games"), list):
        raise UbisoftError(f"That didn't come from the Send to {brand.NAME} bookmark.")
    if not payload.get("user"):
        payload["user"] = settings.get("ubi_user") or "unknown"  # only needed for later syncs and save folders
    expires = int(time.time()) + 2 * 3600
    try:
        if payload.get("expiration"):
            expires = int(datetime.fromisoformat(str(payload["expiration"]).replace("Z", "+00:00")).timestamp())
    except ValueError:
        pass
    settings.update({"ubi_app": str(payload.get("app") or ""), "ubi_user": str(payload["user"]), "ubi_name": str(payload.get("name") or ""),
                     "ubi_ticket": str(payload.get("ticket") or ""), "ubi_session": str(payload.get("session") or ""),
                     "ubi_remember": str(payload.get("remember") or settings.get("ubi_remember") or ""),
                     "ubi_expires": expires})
    games = [g for g in payload["games"] if isinstance(g, dict) and g.get("name") and
             (not g.get("types") or "PC" in [str(t).upper() for t in g["types"]])]
    items = _items(games, [_times(g.get("cards") or []) for g in games])
    _save_snapshot(items, "bookmark")
    return {"games": len(items), "name": settings.get("ubi_name") or ""}


def fetch() -> list[dict]:
    global notice
    notice = None
    try:
        headers = _session_headers()
        user = settings.get("ubi_user")
        games = _owned(headers)
        with ThreadPoolExecutor(max_workers=4) as ex:
            times = list(ex.map(lambda g: _stats(headers, user, g["spaceId"]) if g.get("spaceId") else (0, None), games))
        items = _items(games, times)
        _save_snapshot(items, "sync")
    except Exception:
        snap = _load_snapshot()
        if not snap:
            raise UbisoftError("Link Ubisoft Connect first: Settings › Ubisoft Connect explains the "
                               f"Send to {brand.NAME} bookmark.") from None
        items = snap["games"]
        age = (time.time() - snap.get("at", 0)) / 86400
        if age >= 1:
            notice = (f"Ubisoft hours are from {int(age)} day{'s' if int(age) != 1 else ''} ago (Ubisoft only lets "
                      f"a browser sign in). Click the Send to {brand.NAME} bookmark on connect.ubisoft.com to update them.")
    local = local_last_played()
    for it in items:
        saved = local.get(db.normalize_title(it["name"]))
        if saved and (not it["last_played"] or saved > it["last_played"]):
            it["last_played"] = saved
    return items


def after_import(c) -> None:
    """A Ubisoft game you bought on Steam or Epic also shows up here, and both stores count
    the same hours. The first time that happens, the copy with fewer hours is left out of
    the total (its "Counts" box on the game's page puts it back)."""
    done = set(filter(None, (db.get_meta("ubi_dupes_checked") or "").split(",")))
    rows = c.execute("""SELECT u.id, u.playtime_min AS um, o.id AS oid, o.playtime_min AS om FROM entries u
                        JOIN entries o ON o.game_id = u.game_id AND o.platform IN ('steam', 'epic') AND o.playtime_min > 0
                        WHERE u.platform = 'ubisoft' AND u.excluded = 0 AND o.excluded = 0""").fetchall()
    new = set()
    for r in rows:
        if str(r["id"]) in done or str(r["id"]) in new:
            continue
        c.execute("UPDATE entries SET excluded=1 WHERE id=?", (r["oid"] if r["um"] > r["om"] else r["id"],))
        new.add(str(r["id"]))
    if new:
        c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('ubi_dupes_checked', ?)", (",".join(done | new),))


def test() -> str:
    games = fetch()
    hours = sum(g["playtime_min"] for g in games) / 60
    who = settings.get("ubi_name") or "your account"
    snap = _load_snapshot() or {}
    when = "" if snap.get("source") == "sync" else f" (from the bookmark; Ubisoft didn't let {brand.NAME} refresh by itself)"
    return f"Linked as {who}: {len(games)} games, {hours:,.0f} hours played{when}."


# ------------------------------------------------------------------ the Ubisoft Connect app on this PC

def _local_app_dir() -> Path | None:
    """LOCALAPPDATA / Ubisoft Game Launcher: where the launcher keeps its own sign-in."""
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return None
    p = Path(base) / "Ubisoft Game Launcher"
    return p if p.is_dir() else None


def probe() -> dict:
    """Diagnostics (no secrets): what the launcher keeps locally, and whether Ubisoft's
    services answer a request made from this PC with the launcher's pass."""
    import re
    out: dict = {"launcher_dir": str(_launcher_dir() or ""), "local_dir": str(_local_app_dir() or ""), "files": [],
                 "candidates": [], "renew": None}
    d = _local_app_dir()
    if d:
        for f in sorted(d.rglob("*")):
            if f.is_file() and f.stat().st_size < 5_000_000:
                rel = str(f.relative_to(d))
                out["files"].append([rel, f.stat().st_size])
                if f.suffix.lower() in (".json", ".yml", ".yaml", ".ini", ".txt", ".dat", ".db", "") and f.stat().st_size < 200_000:
                    try:
                        raw = f.read_bytes()
                    except OSError:
                        continue
                    txt = raw.decode("utf-8", "ignore")
                    hits = sorted(set(re.findall(r"(rememberMe|ticket|sessionId|userId|refreshToken|nameOnPlatform|rm_v1|Ubi_v1)", txt, re.I)))
                    if hits:
                        out["candidates"].append([rel, hits, len(raw)])
    # Does Ubisoft accept a scripted renewal from this PC at all? (a bogus pass: 401 = reachable, 429 = bot wall)
    try:
        net.request("POST", SESSIONS, json_body={"rememberMe": True}, retries=0, timeout=20,
                    headers={**BASE_HEADERS, "Authorization": "rm_v1 t=probe"})
        out["renew"] = "ok?"
    except net.HttpError as e:
        out["renew"] = f"HTTP {e.status}"
    except Exception as e:
        out["renew"] = f"error: {e}"
    return out
