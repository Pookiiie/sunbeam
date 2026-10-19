"""GOG: from the GOG Galaxy app on this PC when it's installed, else (or as well) from your
GOG account on the web.

GOG Galaxy (Windows) keeps your library in a local database
(ProgramData\\GOG.com\\Galaxy\\storage\\galaxy-2.0.db): every GOG game you own with its title,
cover, hours played and the day you last played it. Sunbeam only reads it (a copy, so Galaxy
can keep running), and needs no sign-in for it.

The web route uses the same sign-in as GOG Galaxy and the open-source Heroic launcher: open
GOG's sign-in page from Settings, sign in, and paste the address you land on (it carries a
one-time code). The code is swapped for a refresh token, kept in data/settings.json and renewed
on every sync. GOG's web services list what you own but not your hours played, so those come
from Galaxy only.

With both, the account's list is the full list, and Galaxy adds hours and last played.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from .. import db, net, settings

# GOG Galaxy's public client (also used by Heroic, lgogdownloader and others).
CLIENT_ID = "46899977096215655"
CLIENT_SECRET = "9d85c43b1482497dbbce61f6e4aa173a433796eeae2ca8c5f6129f2dc4de46d9"
REDIRECT = "https://embed.gog.com/on_login_success?origin=client"
LOGIN_URL = ("https://auth.gog.com/auth?client_id=46899977096215655"
             "&redirect_uri=https%3A%2F%2Fembed.gog.com%2Fon_login_success%3Forigin%3Dclient"
             "&response_type=code&layout=client2")
TOKEN = "https://auth.gog.com/token"
LIBRARY = "https://galaxy-library.gog.com/users/{user}/releases"
GAMESDB = "https://gamesdb.gog.com/platforms/gog/external_releases/{id}"

notice: str | None = None  # set by fetch() when part of the picture is missing (shown in the sync log)


class GogError(Exception):
    pass


# ------------------------------------------------------------------ GOG Galaxy on this PC

def galaxy_db_path() -> Path | None:
    base = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "GOG.com" / "Galaxy" / "storage" / "galaxy-2.0.db"
    return base if base.exists() else None


def has_galaxy() -> bool:
    return galaxy_db_path() is not None


def _fmt(url: str | None) -> str | None:
    """GOG's image addresses come as templates: ..._{formatter}.{ext}."""
    if not url:
        return None
    return url.replace("{formatter}", "").replace("{ext}", "jpg")


def _ts(s) -> int | None:
    if s in (None, ""):
        return None
    if isinstance(s, (int, float)) or str(s).strip().isdigit():
        n = int(s)
        if n > 10_000_000_000:  # milliseconds
            n //= 1000
        return n if n > 946684800 else None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    ts = int(d.timestamp())
    return ts if ts >= 946684800 else None


def read_galaxy(path: Path) -> dict[str, dict]:
    """GOG games in Galaxy's database → {product id: {name, minutes, last, cover, dlc}}."""
    # Galaxy keeps the file open; read a copy so neither side gets in the other's way.
    tmp = Path(tempfile.mkdtemp(prefix="sunbeam-gog-"))
    try:
        for suffix in ("", "-wal", "-shm"):
            src = Path(str(path) + suffix)
            if src.exists():
                shutil.copy2(src, tmp / (path.name + suffix))
        con = sqlite3.connect(f"file:{tmp / path.name}?mode=ro", uri=True)
        try:
            return _galaxy_rows(con)
        finally:
            con.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _tables(con) -> set[str]:
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}


def _cols(con, table: str) -> set[str]:
    return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}


def _galaxy_rows(con) -> dict[str, dict]:
    t = _tables(con)
    # What you own: LibraryReleases (else the purchase dates, or the OwnedGames view, as some versions have).
    owned = next(((tb, col) for tb, col in (("LibraryReleases", "releaseKey"), ("ProductPurchaseDates", "gameReleaseKey"),
                                            ("OwnedGames", "releaseKey")) if tb in t and col in _cols(con, tb)), None)
    if not owned or "GamePieces" not in t:
        raise GogError("GOG Galaxy's library file looks different from what Sunbeam knows. Sign in to GOG in Settings instead.")
    tb, col = owned
    # The signed-in Galaxy user (0 marks rows that belong to nobody; if there are several, the one owning the most).
    user = None
    if "userId" in _cols(con, tb):
        users = [r[0] for r in con.execute(f"SELECT userId FROM {tb} WHERE userId<>0 GROUP BY userId ORDER BY COUNT(*) DESC")]
        user = users[0] if users else None
    keys = [r[0] for r in con.execute(
        f"SELECT {col} FROM {tb} WHERE {col} LIKE 'gog\\_%' ESCAPE '\\'"
        + (" AND userId=?" if user is not None else ""), (user,) if user is not None else ())]
    if not keys:
        return {}

    # GamePieces: one JSON value per (release, piece type): title, originalImages, meta…
    types = {r[1]: r[0] for r in con.execute("SELECT id, type FROM GamePieceTypes")} if "GamePieceTypes" in t else {}
    wanted = {k: types[k] for k in ("title", "originalTitle", "originalImages", "images", "meta", "originalMeta", "dlcs")
              if k in types}
    pieces: dict[str, dict] = {}
    if wanted:
        q = ",".join("?" * len(wanted))
        has_user = "userId" in _cols(con, "GamePieces")
        inv = {v: k for k, v in wanted.items()}
        rows = con.execute(f"SELECT releaseKey, gamePieceTypeId, value{', userId' if has_user else ', NULL'} FROM GamePieces "
                           f"WHERE gamePieceTypeId IN ({q}) AND releaseKey LIKE 'gog\\_%' ESCAPE '\\'", list(wanted.values()))
        mine: set[tuple] = set()
        for key, tid, value, uid in rows:
            # Your own row wins over a shared one (userId 0) or another user's.
            slot = (key, inv[tid])
            if slot in mine or (user is not None and uid not in (user, 0, None)):
                continue
            try:
                pieces.setdefault(key, {})[inv[tid]] = json.loads(value) if value else {}
            except ValueError:
                continue
            if user is not None and uid == user:
                mine.add(slot)

    minutes: dict[str, int] = {}
    if "GameTimes" in t:
        cols = _cols(con, "GameTimes")
        col = "minutesInGame" if "minutesInGame" in cols else None
        if col:
            sql = f"SELECT releaseKey, {col} FROM GameTimes" + (" WHERE userId=?" if user is not None and "userId" in cols else "")
            for key, m in con.execute(sql, (user,) if user is not None and "userId" in cols else ()):
                minutes[key] = max(minutes.get(key, 0), int(m or 0))
    last: dict[str, int] = {}
    if "LastPlayedDates" in t:
        cols = _cols(con, "LastPlayedDates")
        col = "lastPlayedDate" if "lastPlayedDate" in cols else None
        if col:
            sql = f"SELECT gameReleaseKey, {col} FROM LastPlayedDates" if "gameReleaseKey" in cols \
                else f"SELECT releaseKey, {col} FROM LastPlayedDates"
            if user is not None and "userId" in cols:
                sql += " WHERE userId=?"
            for key, d in con.execute(sql, (user,) if user is not None and "userId" in cols else ()):
                ts = _ts(d)
                if ts:
                    last[key] = max(last.get(key, 0), ts)

    # DLC: Galaxy has no clear flag everywhere. ReleaseProperties.isDlc where it exists, and any
    # release named in another game's "dlcs" list (by key or by title).
    dlc: set[str] = set()
    if "ReleaseProperties" in t and "isDlc" in _cols(con, "ReleaseProperties"):
        dlc = {r[0] for r in con.execute("SELECT releaseKey FROM ReleaseProperties WHERE isDlc=1")}
    dlc_names: set[str] = set()
    for key, p in pieces.items():
        lst = (p.get("dlcs") or {}).get("dlcs") if isinstance(p.get("dlcs"), dict) else p.get("dlcs")
        for d in lst or []:
            name = d if isinstance(d, str) else (d.get("title") or d.get("releaseKey") or "") if isinstance(d, dict) else ""
            if name:
                dlc.add(name) if str(name).startswith("gog_") else dlc_names.add(db.normalize_title(name))

    out: dict[str, dict] = {}
    for key in dict.fromkeys(keys):
        p = pieces.get(key, {})
        title = (p.get("title") or {}).get("title") or (p.get("originalTitle") or {}).get("title")
        if not title:
            continue
        imgs = p.get("originalImages") or p.get("images") or {}
        meta = p.get("meta") or p.get("originalMeta") or {}
        kind = str(meta.get("type") or meta.get("productType") or "").lower()
        pid = key[4:]
        out[pid] = {"name": title.strip(), "minutes": minutes.get(key, 0), "last": last.get(key),
                    "cover": _fmt(imgs.get("verticalCover")),
                    "dlc": key in dlc or kind in ("dlc", "extra") or db.normalize_title(title) in dlc_names}
    return out


# ------------------------------------------------------------------ your GOG account (web)

def _token(params: dict) -> dict:
    try:
        resp = net.request("GET", TOKEN, params={"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, **params}, retries=1)
    except net.HttpError as e:
        if e.status in (400, 401):
            raise GogError("GOG didn't accept the sign-in. Get a fresh code in Settings (a code works once, "
                           "for a few minutes).") from None
        raise
    tok = resp.json() or {}
    if not tok.get("access_token"):
        raise GogError("GOG sign-in failed.")
    settings.update({"gog_refresh": tok.get("refresh_token") or "", "gog_user": str(tok.get("user_id") or ""), "gog_code": ""})
    return tok


def code_from(raw: str) -> str:
    """The pasted code: the bare code, or the whole on_login_success address that carries it."""
    raw = (raw or "").strip().strip('"')
    m = re.search(r"[?&]code=([^&#\s]+)", raw)
    return m.group(1) if m else raw


def _session() -> dict | None:
    raw = (settings.get("gog_code") or "").strip()
    if raw:
        try:
            return _token({"grant_type": "authorization_code", "code": code_from(raw), "redirect_uri": REDIRECT})
        except GogError:
            settings.update({"gog_code": ""})  # used or expired: don't try it again at every sync
            if not settings.get("gog_refresh"):
                raise
    refresh = settings.get("gog_refresh")
    if not refresh:
        return None
    return _token({"grant_type": "refresh_token", "refresh_token": refresh})


def signed_in() -> bool:
    return bool(settings.get("gog_refresh") or settings.get("gog_code"))


def _releases(tok: dict) -> list[dict]:
    user = tok.get("user_id") or settings.get("gog_user")
    hdr = {"Authorization": f"Bearer {tok['access_token']}"}
    out, page = [], None
    for _ in range(100):
        data = net.get_json(LIBRARY.format(user=user), params={"page_token": page}, headers=hdr) or {}
        out += [i for i in data.get("items") or [] if i.get("platform_id") == "gog" and i.get("external_id")]
        page = data.get("next_page_token")
        if not page:
            break
    return out


def _gamesdb(item: dict, token: str) -> dict | None:
    hdr = {"Authorization": f"Bearer {token}"}
    if item.get("certificate"):
        hdr["X-GOG-Library-Cert"] = item["certificate"]
    try:
        return net.get_json(GAMESDB.format(id=item["external_id"]), headers=hdr, retries=2)
    except net.HttpError:
        return None


def _web_games(tok: dict) -> dict[str, dict]:
    items = _releases(tok)
    known = {str(r["external_id"]): r["name"] for r in db.query(
        "SELECT external_id, name FROM entries WHERE platform='gog'")}

    def info(item):
        pid = str(item["external_id"])
        if pid in known:
            return pid, {"name": known[pid], "known": True}
        d = _gamesdb(item, tok["access_token"])
        if not d:
            return pid, None
        if d.get("type") not in ("game", "mod", None) or (d.get("game") or {}).get("visible_in_library") is False:
            return pid, {"skip": True}
        title = ((d.get("title") or {}).get("*") or ((d.get("game") or {}).get("title") or {}).get("*") or "").strip()
        cover = _fmt(((d.get("game") or {}).get("vertical_cover") or {}).get("url_format"))
        return pid, {"name": title, "cover": cover}

    with ThreadPoolExecutor(max_workers=6) as ex:
        pairs = list(ex.map(info, items))
    added = {str(i["external_id"]): _ts(i.get("date_created")) for i in items}
    out = {}
    for pid, d in pairs:
        if d and not d.get("skip") and d.get("name"):
            out[pid] = {**d, "added": added.get(pid)}
    return out


# ------------------------------------------------------------------ together

def fetch() -> list[dict]:
    global notice
    notice = None
    galaxy: dict[str, dict] | None = None
    galaxy_err = None
    path = galaxy_db_path()
    if path:
        try:
            galaxy = read_galaxy(path)
        except (sqlite3.Error, OSError, GogError) as e:
            galaxy_err = str(e)
    tok = _session() if signed_in() else None
    web = _web_games(tok) if tok else None

    if galaxy is None and web is None:
        if galaxy_err:
            raise GogError(f"Couldn't read GOG Galaxy's library ({galaxy_err}). Sign in to GOG in Settings instead.")
        raise GogError("Install GOG Galaxy on this PC, or sign in to GOG in Settings.")
    if galaxy is None and path:
        notice = "Couldn't read GOG Galaxy, so hours played weren't updated."

    galaxy = galaxy or {}
    ids = list(web) if web is not None else [pid for pid, g in galaxy.items() if not g["dlc"]]
    if web is not None:
        ids += [pid for pid, g in galaxy.items() if pid not in web and not g["dlc"] and g["minutes"]]  # played, the account missed it
    out = []
    for pid in dict.fromkeys(ids):
        g, w = galaxy.get(pid, {}), (web or {}).get(pid, {})
        name = w.get("name") or g.get("name")
        if not name:
            continue
        out.append({
            "platform": "gog", "external_id": pid, "name": name,
            "playtime_min": g.get("minutes") or 0, "last_played": g.get("last"), "first_played": None,
            "ownership": None, "psn_image": None if w.get("known") else (w.get("cover") or g.get("cover")),
        })
    return out


def test() -> str:
    games = fetch()
    hours = sum(g["playtime_min"] for g in games) / 60
    via = []
    if has_galaxy():
        via.append("GOG Galaxy on this PC")
    if settings.get("gog_refresh"):
        via.append("your GOG account")
    tail = f", {hours:,.0f} hours played" if has_galaxy() else " (hours played come from GOG Galaxy, which isn't on this PC)"
    return f"Found {len(games)} GOG games through {' and '.join(via) or 'GOG'}{tail}."
