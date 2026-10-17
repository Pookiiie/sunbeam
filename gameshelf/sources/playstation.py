"""PlayStation via Sony's mobile-app endpoints (unofficial, widely used).

Auth: your NPSSO token. Sign in at https://www.playstation.com, then open
https://ca.account.sony.com/api/v1/ssocookie and copy the "npsso" value.
It lasts about two months; the app will tell you when it needs replacing.

Returns PS4 and PS5 games you have *played* (with playtime and last played).
Games you own but never launched aren't included by this endpoint.
"""

from __future__ import annotations

import re
import urllib.parse
from datetime import datetime

from .. import net, settings

AUTH = "https://ca.account.sony.com/api/authz/v3/oauth"
CLIENT_ID = "09515159-7237-4370-9b40-3806e67c0891"
REDIRECT = "com.scee.psxandroid.scecompcall://redirect"
BASIC = "Basic MDk1MTUxNTktNzIzNy00MzcwLTliNDAtMzgwNmU2N2MwODkxOnVjUGprYTV0bnRCMktxc1A="
GAMES = "https://m.np.playstation.com/api/gamelist/v2/users/me/titles"


class PSNError(Exception):
    pass


def _access_token() -> str:
    npsso = settings.get("psn_npsso")
    if not npsso:
        raise PSNError("Add your PlayStation NPSSO token in Settings.")
    # Accept either the bare token or the whole {"npsso":"..."} JSON pasted in.
    m = re.search(r'"npsso"\s*:\s*"([^"]+)"', npsso)
    npsso = m.group(1) if m else npsso.strip().strip('"')

    resp = net.request(
        "GET",
        f"{AUTH}/authorize",
        params={
            "access_type": "offline",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT,
            "response_type": "code",
            "scope": "psn:mobile.v2.core psn:clientapp",
        },
        headers={"Cookie": f"npsso={npsso}"},
        follow_redirects=False,
        retries=1,
    )
    location = resp.headers.get("Location") or resp.headers.get("location") or ""
    if "?code=" not in location:
        raise PSNError(
            "PlayStation didn't accept the NPSSO token (it may have expired). "
            "Get a fresh one in Settings and sync again."
        )
    code = urllib.parse.parse_qs(urllib.parse.urlsplit(location).query).get("code", [""])[0]
    tok = net.request(
        "POST",
        f"{AUTH}/token",
        headers={"Authorization": BASIC},
        data={
            "code": code,
            "redirect_uri": REDIRECT,
            "grant_type": "authorization_code",
            "token_format": "jwt",
        },
    ).json()
    if not tok or not tok.get("access_token"):
        raise PSNError("PlayStation sign-in failed while exchanging the code for a token.")
    return tok["access_token"]


_DURATION = re.compile(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?")


def _minutes(iso: str | None) -> int:
    m = _DURATION.fullmatch(iso or "")
    if not m:
        return 0
    d, h, mi, s = (float(x) if x else 0 for x in m.groups())
    return int(d * 1440 + h * 60 + mi + s / 60)


def _ts(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        ts = int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())
        return ts if ts >= 1072915200 else None
    except ValueError:
        return None


def _best_image(title: dict) -> str | None:
    images = ((title.get("concept") or {}).get("media") or {}).get("images") or []
    order = ["MASTER", "GAMEHUB_COVER_ART", "PORTRAIT_BANNER", "FOUR_BY_THREE_BANNER"]
    for kind in order:
        for img in images:
            if img.get("type") == kind and img.get("url"):
                return img["url"]
    return title.get("imageUrl")


def fetch() -> list[dict]:
    token = _access_token()
    headers = {"Authorization": f"Bearer {token}"}
    titles: list[dict] = []
    offset = 0
    while True:
        data = net.get_json(
            GAMES,
            params={"categories": "ps4_game,ps5_native_game", "limit": 200, "offset": offset},
            headers=headers,
        ) or {}
        batch = data.get("titles") or []
        titles.extend(batch)
        nxt = data.get("nextOffset")
        if not batch or nxt is None or nxt <= offset:
            break
        offset = nxt

    # PS4 and PS5 versions of one game share a "concept": combine them.
    grouped: dict[str, dict] = {}
    for t in titles:
        concept = t.get("concept") or {}
        key = str(concept.get("id") or t.get("titleId"))
        name = concept.get("name") or t.get("localizedName") or t.get("name") or "Unknown"
        item = grouped.get(key)
        minutes = _minutes(t.get("playDuration"))
        last = _ts(t.get("lastPlayedDateTime"))
        first = _ts(t.get("firstPlayedDateTime"))
        if item is None:
            grouped[key] = {
                "platform": "playstation",
                "external_id": key,
                "name": name,
                "playtime_min": minutes,
                "last_played": last,
                "first_played": first,
                "ownership": "ps_plus" if t.get("service") == "ps_plus" else None,
                "psn_image": _best_image(t),
            }
        else:
            item["playtime_min"] += minutes
            item["last_played"] = max(filter(None, [item["last_played"], last]), default=None)
            item["first_played"] = min(filter(None, [item["first_played"], first]), default=None)
            if t.get("service") != "ps_plus":
                item["ownership"] = None
    return list(grouped.values())


def test() -> str:
    games = fetch()
    hours = sum(g["playtime_min"] for g in games) / 60
    return f"Connected: {len(games)} played PS4/PS5 games, {hours:,.0f} hours."
