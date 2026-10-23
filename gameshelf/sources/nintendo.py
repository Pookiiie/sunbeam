"""Nintendo Switch and Switch 2, from your Nintendo Account's Play Activity: the play
history the Nintendo Store app shows (every game, its total play time, days played,
first and last played, and the last week day by day).

Sign-in (once, lasts about two years): Settings › Nintendo Switch opens Nintendo's own
sign-in page. After signing in, right-click "Select this account", copy the link and
paste it into the app. Your password only ever goes to Nintendo. The link carries a
one-time code that is swapped for a sign-in (session token), kept encrypted in
data/settings.json; each sync turns it into a 15-minute pass that only lives in memory.

Nintendo doesn't publish this service: it's the one the Nintendo Store app uses (the
approach of the open-source ns-play-history and nintendo-go). Sunbeam only reads it.
Play shows up there up to a day late (the console uploads it while sleeping online).
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import threading
import time
import urllib.parse
from datetime import datetime

from .. import db, net, settings
from ..switch_import import DEMO, SYSTEM_APP, display_title

CLIENT_ID = "5c38e31cd085304b"  # the Nintendo Store app
REDIRECT = f"npf{CLIENT_ID}://auth"
SCOPE = "openid user user.mii user.email user.links[].id"
ACCOUNTS = "https://accounts.nintendo.com/connect/1.0.0"
ME = "https://api.accounts.nintendo.com/2.0.0/users/me"
PLAY = "https://app-api.znej.nintendo.com/api/v2.0/users/me/play_histories"
LOCALE = "en-US"
PLATFORMS = {"HAC": "switch", "BEE": "switch2"}  # Nintendo's codes for Switch and Switch 2
PENDING_MINUTES = 30  # how long a sign-in link from "Open Nintendo's sign-in" stays usable

_access: dict = {}           # the current 15-minute pass, in memory only
_access_lock = threading.Lock()
_last: dict | None = None    # the latest Play Activity, for after_import (day-by-day play)
notice: str | None = None


class NintendoError(Exception):
    pass


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


# ------------------------------------------------------------------ sign-in

def auth_url() -> str:
    """Nintendo's sign-in page, with a one-time check that the pasted link answers this request."""
    verifier, state = _b64(secrets.token_bytes(32)), _b64(secrets.token_bytes(36))
    settings.update({"nin_pending": json.dumps({"verifier": verifier, "state": state, "at": int(time.time())})})
    challenge = _b64(hashlib.sha256(verifier.encode()).digest())
    return f"{ACCOUNTS}/authorize?" + urllib.parse.urlencode({
        "state": state, "redirect_uri": REDIRECT, "client_id": CLIENT_ID, "scope": SCOPE,
        "response_type": "session_token_code", "session_token_code_challenge": challenge,
        "session_token_code_challenge_method": "S256", "theme": "login_form",
    })


def _link(raw: str) -> None:
    """Swap the pasted "Select this account" link for a sign-in that lasts about two years."""
    settings.update({"nin_link": ""})  # one use only, whatever happens
    frag = urllib.parse.parse_qs(urllib.parse.urlsplit(raw.strip()).fragment or raw.split("#", 1)[-1])
    code = (frag.get("session_token_code") or [""])[0]
    if not code:
        raise NintendoError('That isn\'t the right link. On Nintendo\'s page, right-click "Select this account", '
                            'choose Copy link (or Copy link address) and paste that.')
    try:
        pending = json.loads(settings.get("nin_pending") or "{}")
    except ValueError:
        pending = {}
    if not pending.get("verifier") or time.time() - pending.get("at", 0) > PENDING_MINUTES * 60:
        raise NintendoError("That sign-in has expired. Choose Open Nintendo's sign-in again and paste the new link.")
    if (frag.get("state") or [""])[0] != pending.get("state"):
        raise NintendoError("That link is from an earlier sign-in. Choose Open Nintendo's sign-in again "
                            "and paste the link from that page.")
    try:
        tok = net.request("POST", f"{ACCOUNTS}/api/session_token", retries=0, data={
            "client_id": CLIENT_ID, "session_token_code": code, "session_token_code_verifier": pending["verifier"],
        }).json() or {}
    except net.HttpError as e:
        if e.status in (400, 401):
            raise NintendoError("Nintendo didn't accept that link (each one works once, for a few minutes). "
                                "Choose Open Nintendo's sign-in again.") from None
        raise
    if not tok.get("session_token"):
        raise NintendoError("Nintendo didn't hand over a sign-in. Try again.")
    settings.update({"nin_session": tok["session_token"], "nin_pending": ""})
    with _access_lock:
        _access.clear()


def _token() -> str:
    """A 15-minute pass from the saved sign-in (kept in memory, never written down)."""
    raw = (settings.get("nin_link") or "").strip()
    if raw:
        _link(raw)
    session = settings.get("nin_session")
    if not session:
        raise NintendoError("Sign in to your Nintendo Account in Settings › Nintendo Switch first.")
    with _access_lock:
        if _access.get("token") and _access.get("until", 0) > time.time() + 60 and _access.get("for") == hashlib.sha256(session.encode()).hexdigest():
            return _access["token"]
        try:
            tok = net.request("POST", f"{ACCOUNTS}/api/token", retries=1, json_body={
                "client_id": CLIENT_ID, "session_token": session,
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer-session-token",
            }).json() or {}
        except net.HttpError as e:
            if e.status in (400, 401):
                raise NintendoError("Your Nintendo sign-in has ended (it lasts about two years, and signing out "
                                    "of all devices on Nintendo's site ends it too). Sign in again in Settings.") from None
            raise
        if not tok.get("access_token"):
            raise NintendoError("Nintendo didn't hand over a pass. Try again later.")
        _access.update(token=tok["access_token"], until=time.time() + int(tok.get("expires_in") or 900),
                       **{"for": hashlib.sha256(session.encode()).hexdigest()})
        return _access["token"]


def _nickname(token: str) -> str:
    try:
        me = net.get_json(ME, headers={"Authorization": f"Bearer {token}"}, retries=1) or {}
        return str(me.get("nickname") or "")
    except (net.HttpError, ValueError):
        return ""


# ------------------------------------------------------------------ play activity

def _ts(s) -> int | None:
    if not s:
        return None
    try:
        return int(datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def play_activity() -> dict:
    token = _token()
    try:
        data = net.get_json(PLAY, retries=1, headers={
            "Authorization": f"Bearer {token}", "Accept": "application/json", "gentry-locale": LOCALE})
    except net.HttpError as e:
        if e.status == 401:
            with _access_lock:
                _access.clear()
            raise NintendoError("Nintendo turned the sign-in down. Sign in again in Settings › Nintendo Switch.") from None
        raise
    if not isinstance(data, dict) or not isinstance(data.get("playHistories"), list):
        raise NintendoError("Nintendo's Play Activity answered in an unexpected way (it may have changed).")
    return data


def _platform(t: dict) -> str:
    return PLATFORMS.get(str(t.get("platform") or t.get("deviceType") or "").upper(), "switch")


def ext_id(t: dict) -> str:
    return f"nin:{t.get('titleId')}"


def _skip(name: str) -> bool:
    return bool(SYSTEM_APP.search(name))


def fetch() -> list[dict]:
    global _last, notice
    notice = None
    data = play_activity()
    _last = data
    items = []
    for t in data["playHistories"]:
        name = display_title(str(t.get("titleName") or "").strip())
        if not name or not t.get("titleId") or _skip(name):
            continue
        items.append({
            "platform": _platform(t), "external_id": ext_id(t), "name": name,
            "playtime_min": max(int(t.get("totalPlayedMinutes") or 0), 0),
            "last_played": _ts(t.get("lastPlayedAt")), "first_played": _ts(t.get("firstPlayedAt")),
            "ownership": None, "psn_image": t.get("imageUrl") or None,
            "days": int(t.get("totalPlayedDays") or 0),
        })
    _adopt_recorded(items)
    return items


def _adopt_recorded(items: list[dict]) -> None:
    """Switch copies imported from screen recordings (or added by hand) become the synced
    copies, so nothing is counted twice and their game keeps its status, art and shelves."""
    import difflib
    with db.tx() as c:
        taken = {r["external_id"] for r in c.execute(
            "SELECT external_id FROM entries WHERE platform IN ('switch', 'switch2') AND external_id LIKE 'nin:%'")}
        old = [dict(r) for r in c.execute(
            """SELECT e.id, e.platform, e.external_id, e.name, g.norm_title FROM entries e JOIN games g ON g.id = e.game_id
               WHERE e.platform IN ('switch', 'switch2') AND e.external_id NOT LIKE 'nin:%'""")]
        if not old:
            return
        # Most played first: when a game has a Switch and a Switch 2 entry, the bigger one gets the old copy.
        for it in sorted(items, key=lambda x: -x["playtime_min"]):
            if it["external_id"] in taken or not old:
                continue
            key = db.normalize_title(it["name"])

            def score(o):
                names = {o["norm_title"] or "", db.normalize_title(display_title(o["name"] or ""))}
                best = max(difflib.SequenceMatcher(None, key, n).ratio() for n in names if n) if any(names) else 0
                return best + (0.02 if o["platform"] == it["platform"] else 0)

            match = max(old, key=score)
            if score(match) < 0.9:
                continue
            clash = c.execute("SELECT 1 FROM entries WHERE platform=? AND external_id=?",
                              (it["platform"], it["external_id"])).fetchone()
            if clash:
                continue
            c.execute("UPDATE entries SET platform=?, external_id=?, manual=0 WHERE id=?",
                      (it["platform"], it["external_id"], match["id"]))
            old.remove(match)
            taken.add(it["external_id"])


def after_import(c) -> None:
    """Days played, and the last week day by day (added to each game's Play history)."""
    if not _last:
        return
    ids = {(r["platform"], r["external_id"]): r["id"] for r in c.execute(
        "SELECT id, platform, external_id FROM entries WHERE external_id LIKE 'nin:%'")}
    for t in _last.get("playHistories") or []:
        eid = ids.get((_platform(t), ext_id(t)))
        if eid and t.get("totalPlayedDays"):
            c.execute("UPDATE entries SET days_played=? WHERE id=?", (int(t["totalPlayedDays"]), eid))
    for day in _last.get("recentPlayHistories") or []:
        d = str(day.get("playedDate") or "")[:10]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
            continue
        for t in day.get("dailyPlayHistories") or []:
            eid = ids.get((_platform(t), ext_id(t)))
            if eid is None:  # older answers leave the console out of the daily list
                eid = ids.get(("switch2", ext_id(t))) or ids.get(("switch", ext_id(t)))
            if eid:
                c.execute("INSERT OR REPLACE INTO play_days(entry_id, day, minutes) VALUES (?,?,?)",
                          (eid, d, max(0, int(t.get("totalPlayedMinutes") or 0))))


def test() -> str:
    data = play_activity()
    name = _nickname(_token())
    if name:
        settings.update({"nin_name": name})
    n = len([t for t in data["playHistories"] if t.get("titleId")])
    two = len([t for t in data["playHistories"] if _platform(t) == "switch2"])
    who = f" as {name}" if name else ""
    return (f"Signed in{who}. Nintendo's Play Activity lists {n} game{'s' if n != 1 else ''}"
            + (f" ({two} on Switch 2)" if two else "") + ". They're added at your next sync.")


def sign_out() -> None:
    settings.update({"nin_session": "", "nin_link": "", "nin_pending": "", "nin_name": ""})
    with _access_lock:
        _access.clear()
