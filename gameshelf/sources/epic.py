"""Epic Games Store, through the same web services the Epic launcher uses
(the approach of the open-source Legendary and Heroic launchers).

Sign-in: open the login link in Settings while signed in to epicgames.com, copy
the "authorizationCode" it shows, and paste it into the app. That one-time
code is swapped for a refresh token, kept in data/settings.json and renewed on
every sync, so you only paste a code again if you don't sync for a few weeks.

Gives every game you own (giveaways included), DLC and Unreal Engine assets left
out, with playtime from Epic's playtime service and the store's tall cover art.
"""

from __future__ import annotations

import base64
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from .. import db, net, settings

# The Epic launcher's public client (also used by Legendary and Heroic).
CLIENT_ID = "34a02cf8f4414e29b15921876da36f9a"
CLIENT_SECRET = "daafbccc737745039dffe53d94fc76cf"
OAUTH = "https://account-public-service-prod03.ol.epicgames.com/account/api/oauth/token"
LIBRARY = "https://library-service.live.use1a.on.epicgames.com/library/api/public"
CATALOG = "https://catalog-public-service-prod06.ol.epicgames.com/catalog/api/shared/namespace"
UA = "UELauncher/11.0.1-14907503+++Portal+Release-Live Windows/10.0.19041.1.256.64bit"

class EpicError(Exception):
    pass


def _basic() -> str:
    return "Basic " + base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()


def _token(data: dict) -> dict:
    try:
        resp = net.request("POST", OAUTH, data={**data, "token_type": "eg1"}, retries=1,
                           headers={"Authorization": _basic(), "User-Agent": UA})
    except net.HttpError as e:
        if e.status in (400, 401):
            raise EpicError("Epic didn't accept the sign-in. Get a fresh code in Settings (codes only work once, "
                            "for a few minutes).") from None
        raise
    tok = resp.json() or {}
    if not tok.get("access_token"):
        raise EpicError("Epic sign-in failed.")
    # Keep the new refresh token: each one replaces the last.
    settings.update({"epic_refresh": tok.get("refresh_token") or "", "epic_account": tok.get("account_id") or "",
                     "epic_name": tok.get("displayName") or settings.get("epic_name") or "", "epic_code": ""})
    return tok


def _session() -> dict:
    """An access token: from a freshly pasted code, else from the saved refresh token."""
    raw = (settings.get("epic_code") or "").strip()
    if raw:
        # Accept the bare code or the whole {"authorizationCode": "..."} page pasted in.
        m = re.search(r'"authorizationCode"\s*:\s*"([^"]+)"', raw)
        code = m.group(1) if m else raw.strip('"')
        try:
            return _token({"grant_type": "authorization_code", "code": code})
        except EpicError:
            settings.update({"epic_code": ""})  # used or expired: don't try it again at every sync
            if not settings.get("epic_refresh"):
                raise
    refresh = settings.get("epic_refresh")
    if not refresh:
        raise EpicError("Sign in to Epic in Settings first.")
    return _token({"grant_type": "refresh_token", "refresh_token": refresh})


def _get(url: str, token: str, params: dict | None = None):
    return net.get_json(url, params=params, headers={"Authorization": f"bearer {token}", "User-Agent": UA})


def _records(token: str) -> list[dict]:
    out, cursor = [], None
    for _ in range(50):
        data = _get(f"{LIBRARY}/items", token, {"includeMetadata": "true", "cursor": cursor}) or {}
        out += data.get("records") or []
        cursor = (data.get("responseMetadata") or {}).get("nextCursor")
        if not cursor:
            break
    return out


def _catalog(token: str, ns: str, item: str) -> dict | None:
    try:
        data = _get(f"{CATALOG}/{ns}/bulk/items", token,
                    {"id": item, "includeDLCDetails": "true", "includeMainGameDetails": "true",
                     "country": "US", "locale": "en"}) or {}
    except net.HttpError:
        return None
    return data.get(item)


def _is_game(info: dict) -> bool:
    cats = {c.get("path") for c in info.get("categories") or []}
    if info.get("mainGameItem") or "addons" in cats or any(str(c).startswith("addons/") for c in cats):
        return False  # DLC
    if cats and "games" not in cats and "applications" in cats:
        return False  # tools and apps
    return True


def _cover(info: dict) -> str | None:
    imgs = {i.get("type"): i.get("url") for i in info.get("keyImages") or [] if i.get("url")}
    for kind in ("DieselGameBoxTall", "OfferImageTall", "Thumbnail", "DieselGameBox", "OfferImageWide"):
        if imgs.get(kind):
            return imgs[kind]
    return None


def _ts(s) -> int | None:
    if not s:
        return None
    try:
        ts = int(datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp())
        return ts if ts >= 1072915200 else None
    except ValueError:
        return None


def fetch() -> list[dict]:
    tok = _session()
    token, account = tok["access_token"], tok.get("account_id") or settings.get("epic_account")
    records = [r for r in _records(token)
               if r.get("namespace") not in ("ue", None)  # Unreal Engine marketplace assets
               and r.get("recordType", "APPLICATION") == "APPLICATION" and r.get("catalogItemId")]

    # Titles we already know don't need the catalog again.
    known = {r["external_id"]: r for r in db.query(
        "SELECT external_id, name FROM entries WHERE platform='epic'")}

    def info_for(r):
        if r["catalogItemId"] in known:
            return r, {"title": known[r["catalogItemId"]]["name"], "_known": True}
        return r, _catalog(token, r["namespace"], r["catalogItemId"])

    with ThreadPoolExecutor(max_workers=6) as ex:
        pairs = list(ex.map(info_for, records))

    playtime: dict[str, dict] = {}
    try:
        for p in _get(f"{LIBRARY}/playtime/account/{account}/all", token) or []:
            playtime[p.get("artifactId")] = p
    except net.HttpError:
        pass  # playtime is a bonus; the library still imports

    games: dict[str, dict] = {}
    for r, info in pairs:
        if not info or (not info.get("_known") and not _is_game(info)):
            continue
        title = info.get("title") or r.get("appName")
        if not title:
            continue
        pt = playtime.get(r.get("appName")) or {}
        minutes = int((pt.get("totalTime") or 0) / 60)
        last = _ts(pt.get("lastPlayedTime") or pt.get("lastPlayed") or pt.get("lastUpdated"))
        key = r["catalogItemId"]
        item = games.get(key)
        if item:  # the same game listed twice (e.g. two sandboxes)
            item["playtime_min"] += minutes
            item["last_played"] = max(filter(None, [item["last_played"], last]), default=None)
            continue
        games[key] = {
            "platform": "epic",
            "external_id": key,
            "name": title,
            "playtime_min": minutes,
            "last_played": last,
            "first_played": None,
            "ownership": None,
            "psn_image": None if info.get("_known") else _cover(info),  # store art, used when nothing better is found
        }
    return list(games.values())


def test() -> str:
    games = fetch()
    hours = sum(g["playtime_min"] for g in games) / 60
    name = settings.get("epic_name") or "your account"
    return f"Connected as {name}: {len(games)} games, {hours:,.0f} hours played."
