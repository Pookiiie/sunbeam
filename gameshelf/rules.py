"""Rules that decide how a game is shown, shared by the server and the page.

open_ended(): games without a real ending (MMOs, sandboxes, sports, live-service
multiplayer...). The page shows ∞ instead of a progress bar for them, and
finished-game detection leaves them alone. The library sends this as
`endless_auto`, so the page and the server always agree.
"""

from __future__ import annotations

import re


def _has(items, pattern: str) -> bool:
    rx = re.compile(pattern, re.I)
    return any(rx.search(x or "") for x in items or [])


def blend_ttb(g: dict) -> float | None:
    """Hours to beat, matched to how you play (see the page's blendTtb)."""
    if g.get("ttb_custom"):
        return g["ttb_custom"]
    base = g.get("ttb_extra") or g.get("ttb_main")
    full = g.get("ttb_complete")
    played = (g.get("playtime_min") or 0) / 60
    if not base:
        return full or None
    if full and full > base and played > base * 1.25:
        return base + min(0.5 * (full - base), 0.5 * base)
    return base


def open_ended(g: dict) -> bool:
    modes, genres, themes = g.get("modes") or [], g.get("genres") or [], g.get("themes") or []
    story = _has(genres, r"role-playing|adventure")
    # Always-online and battle royale games, MOBAs, 4X strategy.
    if _has(modes, r"massively multiplayer|battle royale"):
        return True
    if _has(genres, r"^moba$") or _has(themes, r"^4x"):
        return True
    # Sports and fighting games, unless they're also adventures or RPGs (Cairn, Yakuza).
    if _has(genres, r"^sport$") and not story and not _has(genres, r"platform"):
        return True
    if _has(genres, r"^fighting$") and not story:
        return True
    narrative = _has(themes, r"comedy|drama|mystery|horror|thriller|romance")
    if _has(themes, r"sandbox") and _has(genres, r"simulator"):
        # Builders and toys (Factorio, Cities: Skylines, Garry's Mod), but not ones with a story to tell.
        if not narrative and not story and not _has(genres, r"shooter|platform|puzzle"):
            return True
        # Grand strategy (Crusader Kings III).
        if not narrative and _has(genres, r"^strategy$") and not _has(genres, r"adventure|puzzle"):
            return True
        # Open-world survival sandboxes (Minecraft, Terraria, No Man's Sky).
        if _has(themes, r"survival") and _has(themes, r"open world"):
            return True
        # Life sims (The Sims, Stardew Valley).
        if _has(themes, r"romance"):
            return True
    # Management and driving simulators (Euro Truck Simulator, Football Manager), not puzzle-level games.
    if _has(genres, r"simulator") and _has(themes, r"business|non-fiction") and not story and not _has(genres, r"puzzle"):
        return True
    # Party games played in rounds with friends: trivia and drawing collections (Jackbox, Drawful),
    # and board and card games (Mario Party, Monopoly, Uno).
    if re.search(r"jackbox", g.get("title") or "", re.I):
        return True
    if _has(themes, r"^party$") and _has(modes, r"multiplayer") and _has(genres, r"quiz|card & board") \
            and not _has(genres, r"role-playing|adventure|platform"):
        return True
    main, extra, complete = g.get("ttb_main"), g.get("ttb_extra"), g.get("ttb_complete")
    # Time-to-beat numbers no story could produce (players logging endless play).
    if (main and main > 400) or (extra and extra > 600):
        return True
    # Multiplayer-only shooters, fighters, racers and sports (Counter-Strike, ARC Raiders, The Finals).
    competitive = _has(genres, r"shooter|tactical|fighting|moba|racing|sport")
    if modes and not _has(modes, r"single player") and competitive:
        return True
    # Live-service multiplayer: a "100%" time many times the main one (Overwatch, Destiny 2, Deep Rock).
    if main and complete and complete >= 8 * main and _has(modes, r"multiplayer") and competitive \
            and not _has(themes, r"stealth"):
        return True
    ttb, played = blend_ttb(g), (g.get("playtime_min") or 0) / 60
    # Played far beyond the story's length: the multiplayer is clearly the main event.
    if _has(modes, r"multiplayer") and ttb and played >= 20 and played > ttb * 3:
        return True
    # Otherwise a known time to beat means there's an ending.
    if ttb:
        return False
    # Multiplayer-only with no time to beat at all.
    return bool(modes) and not _has(modes, r"single player") and _has(modes, r"multiplayer|co-op")


def is_open_ended(g: dict) -> bool:
    """Your choice on the game's page wins over the automatic rule."""
    if g.get("endless") is not None:
        return bool(g["endless"])
    return open_ended(g)
