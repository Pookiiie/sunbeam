"""HowLongToBeat, via the community `howlongtobeatpy` package.

HowLongToBeat has no official API and changes its site from time to time, so
this is optional: the launcher tries to install the package once, and if it
isn't available (or breaks) GameShelf falls back to IGDB's time-to-beat data.
"""

from __future__ import annotations

import difflib
import re

from .. import db, settings

try:  # optional dependency
    from howlongtobeatpy import HowLongToBeat  # type: ignore
except Exception:  # pragma: no cover - depends on the user's machine
    HowLongToBeat = None


def available() -> bool:
    return HowLongToBeat is not None and settings.get("hltb_enabled", True) is not False


def _spellings(title: str) -> list[str]:
    name = re.sub(r"\s+", " ", db.clean_display_title(title or "")).strip()
    plain = re.sub(r"\s+", " ", re.sub(r"[^\w\s'&]+", " ", name)).strip()
    return [x for i, x in enumerate([name, plain]) if x and x.lower() not in (y.lower() for y in [name, plain][:i])]


def _base(title: str) -> str:
    """The title without its subtitle: "Snipperclips: Cut It Out, Together!" → "Snipperclips"."""
    name = db.clean_display_title(title or "")
    base = re.split(r"\s*(?::| - | – | — )\s*", name, maxsplit=1)[0].strip()
    return base if base and base != name and len(base) >= 4 else ""


_NICHE = {"arcade", "mobile", "ios", "android", "browser", "pinball"}


def _score(r, targets: set[str], year: int | None, pc_or_console: bool) -> float:
    """How well a HowLongToBeat entry fits: its name (or alias) against ours, nudged by
    release year and platform, so "The Walking Dead" (2012, Telltale) beats the 2017
    arcade cabinet of the same name."""
    names = [db.normalize_title(r.game_name or "")]
    names += [db.normalize_title(a) for a in re.split(r"[,\n]", getattr(r, "game_alias", None) or "") if a.strip()]
    names = [n for n in names if n]
    sim = max((difflib.SequenceMatcher(None, t, n).ratio() for t in targets for n in names), default=0)
    ry = getattr(r, "release_world", None)
    try:
        ry = int(ry) if ry else None
    except (TypeError, ValueError):
        ry = None
    same_year = bool(year and ry and abs(year - ry) <= 1)
    if year and ry:
        sim += 0.1 if same_year else (-0.25 if abs(year - ry) >= 3 else 0)
    # Same year and its name starts with ours: "The Walking Dead - A Telltale Games Series".
    if same_year and any(n.startswith(t + " ") for t in targets for n in names):
        sim = max(sim, 0.85)
    plats = {str(p).strip().lower() for p in re.split(r",", getattr(r, "profile_platforms", None) or "") if str(p).strip()} \
        if isinstance(getattr(r, "profile_platforms", None), str) else \
        {str(p).strip().lower() for p in (getattr(r, "profile_platforms", None) or [])}
    if pc_or_console and plats and plats <= _NICHE:
        sim -= 0.3  # an arcade cabinet or phone game, not the one you have
    return sim


def _search(queries: list[str], targets: set[str], year: int | None = None, pc_or_console: bool = True):
    best, best_sim = None, 0.0
    for q in queries:
        try:
            results = HowLongToBeat().search(q) or []
        except Exception:
            continue
        for r in results:
            sim = _score(r, targets, year, pc_or_console)
            if sim > best_sim:
                best, best_sim = r, sim
        if best_sim >= 1.0:
            break
    return best, best_sim


def lookup(title: str, other_names: list[str] | tuple = (), year: int | None = None,
           pc_or_console: bool = True) -> dict | None:
    """Times for a game. Tries the title, a spelling without punctuation, and the names
    the stores use for it; if none is on HowLongToBeat, the title without its subtitle,
    which then has to match exactly (so a sequel never borrows the first game's times)."""
    if not available():
        return None
    queries: list[str] = []
    for t in [title, *other_names]:
        for v in _spellings(t):
            if v.lower() not in (q.lower() for q in queries):
                queries.append(v)
    targets = {db.normalize_title(q) for q in queries if db.normalize_title(q)}
    best, best_sim = _search(queries[:6], targets, year, pc_or_console)
    if not best or best_sim < 0.8:
        bases = [b for b in {_base(t) for t in [title, *other_names]} if b]
        best, best_sim = _search(bases[:2], {db.normalize_title(b) for b in bases}, year, pc_or_console)
        if not best or best_sim < 0.98:
            return None

    def hours(v):
        return round(float(v), 1) if v and float(v) > 0 else None

    main = hours(getattr(best, "main_story", None))
    extra = hours(getattr(best, "main_extra", None))
    complete = hours(getattr(best, "completionist", None))
    if not (main or extra or complete):
        main = hours(getattr(best, "all_styles", None))  # some entries only have one "Solo" time
    if not (main or extra or complete):
        return None
    return {"main": main, "extra": extra, "complete": complete, "id": getattr(best, "game_id", None)}
