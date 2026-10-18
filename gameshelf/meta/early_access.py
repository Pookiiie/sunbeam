"""Early access: games that are still being made. (Also Steam's "VR Only" flag, read in the same call.)

Steam says so for every game with a Steam version (IStoreBrowseService/GetItems:
is_early_access, plus release_from_early_access_date once the full release is announced;
after it, the store's release date is the full release). Games without a Steam version use
IGDB's game status (4 = early access), when IGDB is set up. Xbox names them "… (Game
Preview)" (in its library and the XGP list), which counts too.

Runs at the end of every sync, and shortly after start-up when the last check is over a day old.

Stored on the game as early_access (1 / 0 / NULL not known) and ea_full_date. The page tags
these games, keeps them out of automatic "completed" marks and, if you ask in Settings,
out of the library and Curator.
"""

from __future__ import annotations

import json
import threading

from .. import db, net
from . import igdb

STEAM_API = "https://api.steampowered.com"
IGDB_EARLY_ACCESS = 4
STEAM_VR_ONLY = 54  # Steam store feature category "VR Only" (53 is "VR Supported": a flat game with a VR mode)
PREVIEW = "%(Game Preview)%"  # Xbox's word for early access


def _steam(appids: list[int]) -> dict[int, tuple[int, int | None, int]]:
    """{appid: (early access, announced full release, VR only)}"""
    out: dict[int, tuple[int, int | None, int]] = {}
    for i in range(0, len(appids), 50):
        req = {"ids": [{"appid": a} for a in appids[i:i + 50]], "context": {"language": "english", "country_code": "CA"},
               "data_request": {"include_release": True}}
        data = net.get_json(f"{STEAM_API}/IStoreBrowseService/GetItems/v1/",
                            params={"input_json": json.dumps(req, separators=(",", ":"))}, retries=1) or {}
        for it in (data.get("response") or {}).get("store_items") or []:
            appid = it.get("appid") or it.get("id")
            if not appid or it.get("success") not in (None, 1):
                continue  # not on the store (here): leave what we knew
            rel = it.get("release") or {}
            ea = bool(it.get("is_early_access") or rel.get("is_early_access"))
            feats = (it.get("categories") or {}).get("feature_categoryids")
            vr = None if feats is None else int(STEAM_VR_ONLY in feats)  # no categories: let IGDB's guess stand
            out[int(appid)] = (int(ea), (rel.get("release_from_early_access_date") or None) if ea else None, vr)
    return out


def _igdb(ids: list[int]) -> dict[int, int]:
    out: dict[int, int] = {}
    for i in range(0, len(ids), 500):
        chunk = ",".join(str(x) for x in ids[i:i + 500])
        for r in igdb.q("games", f"fields game_status,status; where id = ({chunk}); limit 500;"):
            st = r.get("game_status", r.get("status"))
            if isinstance(st, dict):
                st = st.get("id")
            out[int(r["id"])] = int(st == IGDB_EARLY_ACCESS)
    return out


def refresh() -> int:
    """Check every game in the library (and on your wishlists). Returns how many are in early access."""
    games = [dict(r) for r in db.query("SELECT id, steam_appid, igdb_id FROM games WHERE id IN (SELECT game_id FROM entries)")]
    found: dict[int, tuple] = {}  # game id -> (early access, full release, VR only or None)
    by_app: dict[int, list[int]] = {}
    for g in games:
        if g["steam_appid"]:
            by_app.setdefault(int(g["steam_appid"]), []).append(g["id"])
    if by_app:
        for appid, val in _steam(sorted(by_app)).items():
            for gid in by_app.get(appid, []):
                found[gid] = val
    rest = {g["igdb_id"]: g["id"] for g in games if g["id"] not in found and g["igdb_id"]}
    if rest and igdb.configured():
        try:
            for igdb_id, ea in _igdb(sorted(rest)).items():
                if igdb_id in rest:
                    found[rest[igdb_id]] = (ea, None, None)
        except Exception:
            pass  # IGDB didn't answer: those games keep what they had
    # Xbox's "(Game Preview)" in the game's name or a copy's name: early access whatever else says.
    for r in db.query("""SELECT DISTINCT g.id FROM games g JOIN entries e ON e.game_id = g.id
                         WHERE g.title LIKE ? OR e.name LIKE ?""", (PREVIEW, PREVIEW)):
        prev = found.get(r["id"]) or (0, None, None)
        found[r["id"]] = (1, prev[1], prev[2])
    now = db.now()
    with db.tx() as c:
        for gid, (ea, full, vr) in found.items():
            c.execute("UPDATE games SET early_access=?, ea_full_date=?, vr_only=COALESCE(?, vr_only), ea_checked_at=? WHERE id=?",
                      (ea, full, vr, now, gid))
    return sum(1 for v in found.values() if v[0])


def refresh_soon() -> None:
    """At start-up: check again in the background if the last check is over a day old (or never ran)."""
    row = db.query("SELECT MAX(ea_checked_at) t FROM games")
    last = row[0]["t"] if row else None
    if last and db.now() - last < 20 * 3600:
        return

    def run():
        try:
            refresh()
            from .. import sync
            sync.recheck_auto_completions()  # undo automatic "completed" on games now known to be in early access
        except Exception as e:
            print(f"  Couldn't check for early access games: {e}")
    t = threading.Timer(20, run)
    t.daemon = True  # never keeps the app from quitting
    t.start()
