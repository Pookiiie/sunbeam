"""Spot finished games from your Steam achievements and PlayStation trophies.

A game counts as finished when you've earned something that only comes from
reaching the end: a Platinum, every achievement, or an achievement/trophy whose
name or description talks about finishing the story, rolling the credits or
beating the final boss. Each detection keeps its evidence so you can see why.
"""

from __future__ import annotations

import re
from datetime import datetime

from .. import db, net, settings
from ..sources import playstation, steam

TROPHY = "https://m.np.playstation.com/api/trophy/v1"

# Wording that marks the end of a game. Checked against the name and the description
# separately. The thing finished has to be the whole game or story, said plainly:
# "Complete the story", not "Complete a story case" (L.A. Noire) or "Beat a campaign"
# (one of Left 4 Dead's several).
_OBJ = r"(?:the |your )?(?:main |entire |whole )?(?:game|story|main story|campaign|main campaign|storyline|adventure|journey)"
_AFTER = r"(?=\s*$|\s*[.!,;:(\[]|\s+(?:on|in|at|for|once|and|with|as|playing|by)\b)"
ENDING = re.compile(
    r"\b(?:"
    r"(?:complete|completed|completing|finish|finished|finishing|beat|beaten|beating|clear|cleared|clearing) " + _OBJ + _AFTER +
    r"|(?:roll|rolled|see|saw|watch|watched|reach|reached) the (?:end )?credits"
    r"|credits roll"
    r"|(?:reach|reached|see|saw|witness|witnessed|achieve|achieved|get|got|unlock|unlocked) (?:the|an|any|a) "
    r"(?:true |good |final |real |bad |secret |normal )?ending\b"
    r"|(?:defeat|defeated|kill|killed|beat|beaten|destroy|destroyed) the final boss"
    r")",
    re.I,
)
# A name or description that is only "Story complete", "Game Over... The End" style.
ENDING_ALONE = re.compile(r"^(?:(?:the end|fin)[.!]?$|(?:main )?(?:game|story|campaign) (?:complete|completed|cleared|beaten|finished)\b)", re.I)
# Wording that looks like the above but isn't simply reaching the end.
NOT_ENDING = re.compile(
    r"\b(tutorial|prologue|demo|trial|first (chapter|act|level|mission|area)|chapter (1|one|i)\b|act (1|one|i)\b|"
    r"level 1\b|episode (1|one)\b|in under|under \d|without (dying|taking|being|using|saving|killing)|speedrun|"
    r"(on|in) (hard|nightmare|insane|extreme|ultra|brutal|legendary|veteran|realistic|grounded|survivor|expert|master)\b|"
    r"new game ?\+|ng\+|all (endings|chapters|missions)|every ending|twice|three times|\d+ times|"
    r"co-?op|online|ranked|daily|weekly|dlc|expansion|star rating|\d+ stars?|perfect|rating|"
    r"case|side ?quest|optional)",
    re.I,
)


def _ending(name: str, desc: str) -> bool:
    name, desc = (name or "").strip(), (desc or "").strip()
    hit = ENDING.search(name) or ENDING.search(desc) or ENDING_ALONE.search(name) or ENDING_ALONE.search(desc)
    if not hit:
        return False
    return not NOT_ENDING.search(f"{name}. {desc}")


def still_valid(evidence: str) -> bool:
    """Re-check an earlier detection ("Earned the achievement “X”: Y") against today's rules."""
    if not evidence:
        return False
    if re.match(r"Earned (the Platinum trophy|every trophy|all \d+ achievements)", evidence):
        return True
    m = re.match(r"Earned the (?:achievement|trophy) “(.*?)”(?:: (.*))?$", evidence, re.S)
    return bool(m) and _ending(m.group(1), m.group(2) or "")


def configured_steam() -> bool:
    return bool(settings.get("steam_api_key") and settings.get("steam_id"))


def configured_psn() -> bool:
    return bool(settings.get("psn_npsso"))


# ------------------------------------------------------------------ Steam

class SteamAch:
    def __init__(self):
        self.key = settings.get("steam_api_key")
        self.steam_id = steam.resolve_steam_id()

    def check(self, appid: int) -> dict | None:
        """Evidence dict {"at": ts, "why": text} if the game looks finished, else None."""
        try:
            data = net.get_json(
                f"{steam.API}/ISteamUserStats/GetPlayerAchievements/v1/",
                params={"key": self.key, "steamid": self.steam_id, "appid": appid, "l": "english"},
                retries=0,
            )
        except net.HttpError:
            return None  # game without achievements, or stats hidden
        ach = ((data or {}).get("playerstats") or {}).get("achievements") or []
        got = [a for a in ach if a.get("achieved")]
        if not got:
            return None
        if len(got) == len(ach) and len(ach) >= 5:
            last = max(a.get("unlocktime") or 0 for a in got) or None
            return {"at": last, "why": f"Earned all {len(ach)} achievements"}
        hits = [a for a in got if _ending(a.get("name") or "", a.get("description") or "")]
        if not hits:
            return None
        first = min(hits, key=lambda a: a.get("unlocktime") or 0)
        return {"at": first.get("unlocktime") or None, "why": _why(first.get("name"), first.get("description"))}


# ------------------------------------------------------------------ PlayStation

def _iso(s: str | None) -> int | None:
    if not s:
        return None
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


class PsnTrophies:
    def __init__(self):
        self.headers = {"Authorization": f"Bearer {playstation._access_token()}"}
        self.titles: dict[str, list[dict]] = {}  # normalized name -> trophy titles
        offset = 0
        while True:
            data = net.get_json(f"{TROPHY}/users/me/trophyTitles", params={"limit": 800, "offset": offset},
                                headers=self.headers) or {}
            batch = data.get("trophyTitles") or []
            for t in batch:
                self.titles.setdefault(db.normalize_title(t.get("trophyTitleName") or ""), []).append(t)
            nxt = data.get("nextOffset")
            if not batch or nxt is None or nxt <= offset:
                break
            offset = nxt

    def find(self, title: str) -> list[dict]:
        key = db.normalize_title(title)
        if key in self.titles:
            return self.titles[key]
        # Trophy lists often carry a platform tag or trademark-free spelling.
        for k, v in self.titles.items():
            if k and (k.startswith(key + " ") or key.startswith(k + " ")) and min(len(k), len(key)) >= 6:
                return v
        return []

    def check(self, title: str) -> dict | None:
        for t in self.find(title):
            ev = self._check_title(t)
            if ev:
                return ev
        return None

    def _check_title(self, t: dict) -> dict | None:
        earned_n = t.get("earnedTrophies") or {}
        if not sum(earned_n.values() if isinstance(earned_n, dict) else [0]):
            return None
        comm, svc = t.get("npCommunicationId"), t.get("npServiceName") or "trophy2"
        params = {"npServiceName": svc}
        try:
            defs = net.get_json(f"{TROPHY}/npCommunicationIds/{comm}/trophyGroups/all/trophies",
                                params=params, headers=self.headers) or {}
            mine = net.get_json(f"{TROPHY}/users/me/npCommunicationIds/{comm}/trophyGroups/all/trophies",
                                params=params, headers=self.headers) or {}
        except net.HttpError:
            return None
        info = {d.get("trophyId"): d for d in defs.get("trophies") or []}
        got = [m for m in mine.get("trophies") or [] if m.get("earned")]
        plat = [m for m in got if (info.get(m.get("trophyId")) or {}).get("trophyType") == "platinum"
                or m.get("trophyType") == "platinum"]
        if plat:
            return {"at": _iso(plat[0].get("earnedDateTime")), "why": "Earned the Platinum trophy"}
        if t.get("progress") == 100:
            last = max((_iso(m.get("earnedDateTime")) or 0 for m in got), default=0) or None
            return {"at": last, "why": "Earned every trophy"}
        hits = []
        for m in got:
            d = info.get(m.get("trophyId")) or {}
            if _ending(d.get("trophyName") or "", d.get("trophyDetail") or ""):
                hits.append((_iso(m.get("earnedDateTime")) or 0, d))
        if not hits:
            return None
        at, d = min(hits, key=lambda h: h[0])
        return {"at": at or None, "why": _why(d.get("trophyName"), d.get("trophyDetail"), "trophy")}


def _why(name, desc, kind="achievement") -> str:
    name, desc = (name or "").strip(), (desc or "").strip()
    if name and desc:
        return f"Earned the {kind} “{name}”: {desc}"
    return f"Earned the {kind} “{name or desc}”"
