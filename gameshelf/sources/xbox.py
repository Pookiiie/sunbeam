"""Xbox (console and PC), through OpenXBL (xbl.io), a free service that signs in
to Xbox Live for you and hands out a personal API key.

Gives the games in your Xbox title history (played on Xbox or on a PC with the
Xbox app), when you last played each, and playtime from Xbox's "MinutesPlayed"
stat, fetched 100 games per request. Games you own but never launched aren't in
the title history, so they don't appear.
"""

from __future__ import annotations

import json
import time
from datetime import datetime

from .. import net, settings

API = "https://api.xbl.io/v2"


class XboxError(Exception):
    pass


def _key() -> str:
    key = settings.get("xbl_key")
    if not key:
        raise XboxError("Add your OpenXBL API key in Settings.")
    return key


BUSY = ("Xbox Live is limiting requests right now (too many in a short time). "
        "Nothing was lost; try the Xbox sync again in a few minutes.")


def _call(method: str, path: str, body=None, _wait: float = 20):
    """One OpenXBL request. Xbox Live sometimes answers "too many requests" (429): wait a
    little and try once more before giving up."""
    try:
        return _call_once(method, path, body)
    except _Busy:
        if _wait <= 0:
            raise XboxError(BUSY) from None
        time.sleep(_wait)
        try:
            return _call_once(method, path, body)
        except _Busy:
            raise XboxError(BUSY) from None


class _Busy(Exception):
    pass


def _call_once(method: str, path: str, body=None):
    try:
        resp = net.request(method, f"{API}/{path}", json_body=body, retries=1, timeout=60,
                           headers={"X-Authorization": _key(), "Accept": "application/json"})
    except net.HttpError as e:
        if e.status in (401, 403):
            raise XboxError("OpenXBL didn't accept the API key. Check it in Settings.") from None
        if e.status == 429:  # OpenXBL's own allowance (HTTP 429), not Xbox Live's
            raise XboxError("OpenXBL's hourly limit is used up. Try again in an hour.") from None
        raise
    data = resp.json() if resp.body else None
    # Responses come wrapped as {"content": ..., "code": ...}; older ones aren't.
    if isinstance(data, dict) and "content" in data and ("code" in data or len(data) <= 3):
        code = data.get("code") or 200
        if code == 429:
            raise _Busy()
        if isinstance(code, int) and code // 100 != 2:
            raise XboxError(f"Xbox Live answered with an error ({code}).")
        content = data["content"]
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except ValueError:
                pass
        return content
    return data


def _ts(s) -> int | None:
    if not s:
        return None
    try:
        ts = int(datetime.fromisoformat(str(s).replace("Z", "+00:00")[:32]).timestamp())
        return ts if ts >= 1072915200 else None
    except ValueError:
        return None


def account() -> dict:
    data = _call("GET", "account") or {}
    users = data.get("profileUsers") or []
    if not users:
        raise XboxError("OpenXBL didn't return your Xbox profile.")
    u = users[0]
    tag = next((s.get("value") for s in u.get("settings") or [] if s.get("id") in ("Gamertag", "ModernGamertag")), None)
    settings.update({"xbl_gamertag": tag or settings.get("xbl_gamertag") or "", "xbl_xuid": str(u.get("id"))})
    return {"xuid": str(u.get("id")), "gamertag": tag}


def _playtime(xuid: str, title_ids: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for i in range(0, len(title_ids), 100):
        if i:
            time.sleep(1.5)  # spread the requests out: Xbox Live limits bursts
        chunk = title_ids[i:i + 100]
        try:
            data = _call("POST", "player/stats", {
                "xuids": [xuid], "stats": [{"name": "MinutesPlayed", "titleId": t} for t in chunk]}) or {}
        except (net.HttpError, XboxError):
            continue  # playtime is a bonus; the library still imports
        for lst in data.get("statlistscollection") or data.get("statListsCollection") or []:
            for st in lst.get("stats") or []:
                if (st.get("name") or "").lower() != "minutesplayed":
                    continue
                try:
                    out[str(st.get("titleid") or st.get("titleId"))] = int(float(st.get("value")))
                except (TypeError, ValueError):
                    pass
    return out


# Which Xbox play history to import: games that run on PC (the user's choice: PC play only, so
# no Xbox 360 or console-only titles). Xbox's history lists the devices a title runs on, not where
# you played it, so a game you only played on a Series console but that's also on PC still comes in.
# Adding Xbox Series support one day: add "xboxseries" and "scarlett" here.
IMPORT_DEVICES = {"pc", "win32"}
_imported: set[str] = set()  # this sync's title ids, so after_import() can drop the rest


def fetch(fresh: bool = False) -> list[dict]:
    # Your Xbox user id never changes: ask for it once, not on every sync.
    xuid = settings.get("xbl_xuid")
    me = account() if fresh or not xuid else {"xuid": xuid}
    data = _call("GET", "titles") or {}
    titles = data.get("titles") or []
    games = []
    for t in titles:
        if (t.get("type") or "Game").lower() != "game":
            continue  # apps (Netflix, the Xbox app itself...)
        devices = {d.lower() for d in t.get("devices") or []}
        if devices and not devices & IMPORT_DEVICES:
            continue  # Xbox 360, console-only and phone titles
        tid = str(t.get("titleId") or "")
        if not tid or not t.get("name"):
            continue
        games.append(t)
    _imported.clear()
    _imported.update(str(t["titleId"]) for t in games)
    minutes = _playtime(me["xuid"], [str(t["titleId"]) for t in games])
    out = []
    for t in games:
        tid = str(t["titleId"])
        hist = t.get("titleHistory") or {}
        out.append({
            "platform": "xbox",
            "external_id": tid,
            "name": t["name"],
            "playtime_min": minutes.get(tid, 0),
            "last_played": _ts(hist.get("lastTimePlayed")),
            "first_played": None,
            "ownership": "game_pass" if (t.get("gamePass") or {}).get("isGamePass") else None,
            "psn_image": t.get("displayImage") or None,  # store art, used when nothing better is found
        })
    return out


def after_import(c) -> None:
    """Copies imported before that this import leaves out (Xbox 360 and console-only games) go.
    XGP list entries and copies you added by hand stay."""
    if not _imported:
        return  # nothing came back: keep what we had
    marks = ",".join("?" * len(_imported))
    c.execute(f"""DELETE FROM entries WHERE platform='xbox' AND COALESCE(ownership, '') <> 'catalog'
                  AND NOT COALESCE(manual, 0) AND external_id NOT IN ({marks})""", tuple(_imported))
    from .. import db
    db.delete_orphans(c)


def test() -> str:
    games = fetch(fresh=True)
    hours = sum(g["playtime_min"] for g in games) / 60
    tag = settings.get("xbl_gamertag") or "your account"
    return f"Connected as {tag}: {len(games)} games, {hours:,.0f} hours played."
