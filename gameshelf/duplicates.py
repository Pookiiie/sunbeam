"""Find games that are really the same game listed more than once (editions,
store versions, tutorials, expansions) and combine them.

Two kinds of evidence:
- IGDB says one is an edition (version_parent) or add-on (parent_game) of another.
- Titles: once edition words are removed, one title is the start of the other
  ("Disco Elysium" / "Disco Elysium: The Final Cut", "The Ship" / "The Ship Tutorial").
Nothing is combined without you confirming it.

After a sync brings in new games, the ones that look like a game you already have are
remembered (meta "dup_review") and the page opens the duplicates window by itself, with
those groups first. Each group still needs your OK (Combine, or Not duplicates).
"""

from __future__ import annotations

import json
import re

from . import db
from .meta import igdb

# Words that mark an edition, version or side-app of the same game.
EDITION = re.compile(
    r"\b(the )?(final cut|definitive (edition|experience)|game of the year( edition)?|goty( edition)?|"
    r"complete( edition)?|legacy edition|deluxe( edition)?|gold( edition)?|ultimate( edition)?|"
    r"enhanced( edition)?|special edition|anniversary( edition)?|director'?s cut|remaster(ed)?|"
    r"new steam version|steam edition|single ?player|multi ?player|tutorial|beta|demo|"
    r"standard edition|premium edition|collector'?s edition|digital deluxe|reloaded|redux|"
    r"ps4|ps5|pc|edition)\b"
)


def _strip(title: str) -> str:
    t = db.normalize_title(title)
    t = EDITION.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


def _dismissed() -> set[frozenset]:
    row = db.query("SELECT value FROM meta WHERE key='dup_dismissed'")
    try:
        return {frozenset(x) for x in json.loads(row[0]["value"])} if row else set()
    except (ValueError, TypeError):
        return set()


def dismiss(game_ids: list[int]) -> None:
    pairs = [sorted(p) for p in _dismissed()] + [sorted(int(i) for i in game_ids)]
    with db.tx() as c:
        c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('dup_dismissed', ?)", (json.dumps(pairs),))


def review_ids() -> set[int]:
    """New games waiting for you to look at them in the duplicates window."""
    try:
        return {int(x) for x in json.loads(db.get_meta("dup_review") or "[]")}
    except (ValueError, TypeError):
        return set()


def flag_new(after_id: int) -> int:
    """After a sync: remember new games (id > after_id) that look like a game already
    listed, so the page asks about them. Returns how many groups need a look."""
    groups = [g for g in find() if any(x["id"] > after_id for x in g["games"])]
    if groups:
        ids = review_ids() | {x["id"] for g in groups for x in g["games"] if x["id"] > after_id}
        db.set_meta("dup_review", json.dumps(sorted(ids)))
    return len(groups)


def reviewed() -> None:
    db.set_meta("dup_review", "")


def find() -> list[dict]:
    games = [dict(r) for r in db.query(LIBRARY)]
    by_id = {g["id"]: g for g in games}
    by_igdb = {g["igdb_id"]: g for g in games if g["igdb_id"]}
    links: dict[int, dict[int, str]] = {}  # game -> {other game: reason}
    children: set[int] = set()              # games IGDB calls an edition/add-on of another you have

    def link(a, b, reason):
        if a == b:
            return
        links.setdefault(a, {}).setdefault(b, reason)
        links.setdefault(b, {}).setdefault(a, reason)

    # 1. IGDB: editions and add-ons of a game you also have.
    if igdb.configured() and by_igdb:
        ids = list(by_igdb)
        try:
            for i in range(0, len(ids), 200):
                chunk = ",".join(str(x) for x in ids[i:i + 200])
                for r in igdb.q("games", f"fields version_parent,parent_game; where id = ({chunk}); limit 500;"):
                    me = by_igdb.get(r["id"])
                    for field, why in (("version_parent", "Edition"), ("parent_game", "Add-on")):
                        other = by_igdb.get(r.get(field))
                        if me and other and me["id"] != other["id"]:
                            link(me["id"], other["id"], why)
                            children.add(me["id"])
        except Exception:
            pass

    # 2. Titles: same game once edition words are gone, or one title starts the other.
    stripped = {g["id"]: _strip(g["title"]) for g in games}
    raw = {g["id"]: db.normalize_title(g["title"]) for g in games}
    ordered = sorted(games, key=lambda g: len(stripped[g["id"]]))
    for i, a in enumerate(ordered):
        sa = stripped[a["id"]]
        if len(sa) < 4:
            continue
        for b in ordered[i + 1:]:
            sb = stripped[b["id"]]
            if sa == sb:
                link(a["id"], b["id"], "Same game")
            elif sb.startswith(sa + " ") and (raw[a["id"]] != sa or raw[b["id"]] != sb):
                # One of them carries edition words ("MGSV: Definitive Experience" / "MGSV: The Phantom Pain"),
                # but a numbered sequel is its own game ("Company of Heroes" / "Company of Heroes 2").
                rest = sb[len(sa):].strip()
                if not re.match(r"(\d+|[ivx]+)\b", rest):
                    link(a["id"], b["id"], "Similar title")

    # Group connected games together.
    dismissed = _dismissed()
    seen, groups = set(), []
    for gid in links:
        if gid in seen:
            continue
        stack, members = [gid], set()
        while stack:
            x = stack.pop()
            if x in members:
                continue
            members.add(x)
            stack.extend(links.get(x, {}))
        seen |= members
        if len(members) < 2 or frozenset(members) in dismissed:
            continue
        # Keep the base game: the one IGDB calls the parent, else the plainest title, else the most played.
        def rank(x):
            g = by_id[x]
            return (x in children, raw[x] != stripped[x], -g["playtime_min"], len(g["title"]))
        keep = min(members, key=rank)
        groups.append({
            "keep": keep,
            "games": [{
                "id": x, "title": by_id[x]["title"],
                "platforms": sorted(set((by_id[x]["platforms"] or "").split(","))),
                "hours": round(by_id[x]["playtime_min"] / 60, 1),
                "cover": by_id[x]["cover_url"] or by_id[x]["igdb_cover"] or by_id[x]["psn_image"],
                "reason": next(iter(links[x].values())) if x != keep else "",
            } for x in sorted(members, key=lambda m: (m != keep, by_id[m]["title"]))],
        })
    # Groups with games that just arrived (and wait for a look) come first, marked "fresh".
    fresh = review_ids()
    for g in groups:
        for x in g["games"]:
            x["fresh"] = x["id"] in fresh
        g["fresh"] = any(x["fresh"] for x in g["games"])
    groups.sort(key=lambda g: (not g["fresh"], by_id[g["keep"]]["title"].lower()))
    return groups


LIBRARY = """
SELECT g.id, g.title, g.igdb_id, g.cover_url, g.igdb_cover, g.psn_image,
       COALESCE(SUM(e.playtime_min), 0) AS playtime_min, GROUP_CONCAT(DISTINCT e.platform) AS platforms
FROM games g JOIN entries e ON e.game_id = g.id
WHERE COALESCE(e.ownership, '') NOT IN ('catalog', 'wishlist')
GROUP BY g.id
"""


def combine(keep: int, others: list[int]) -> dict:
    with db.tx() as c:
        for o in others:
            if int(o) != int(keep):
                db.merge_games(c, int(keep), int(o))
    return db.game_detail(int(keep))
