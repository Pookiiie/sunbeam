"""Switch 2 games from screenshots of Play Activity (Nintendo Switch app on a
phone, or the user page on the Switch 2 itself).

Each game there is a title (one or two lines) followed by
"Played for N hour(s) or more" or "Played for a little while".
Text is read with the OCR engine built into Windows (no install needed), then
grouped by position: only words in the same column as the "Played for" lines
count, which keeps the game thumbnails' artwork out of the titles.
"""

from __future__ import annotations

import base64
import json
import os
import re
import statistics
import subprocess
import tempfile

from . import db
from .paths import APP_DIR

OCR_SCRIPT = APP_DIR / "windows_ocr.ps1"

PLAYED = re.compile(r"played\s*for\s*(?:(\d[\d,]*)\s*h\s*o\s*u\s*r|(a\s*little\s*while))", re.I)
# Nintendo's own launchers in Play Activity; games played inside them get their own rows.
SYSTEM_APP = re.compile(
    r"nintendo classics|nintendo switch online|^nintendo (entertainment system|64|gamecube)\b|"
    r"^super nintendo entertainment system|^game boy( advance| color)?\b|^sega (mega drive|genesis)|"
    r"^gamechat|^nintendo switch 2 welcome tour|^nintendo eshop",
    re.I,
)
LITTLE_WHILE_HOURS = 0.5


class OCRError(Exception):
    pass


# ------------------------------------------------------------------ reading text

def ocr_image(data: bytes) -> dict:
    """Run Windows OCR on one image. Returns {"width", "height", "lines": [{"words": [{t,x,y,w,h}]}]}."""
    if os.name != "nt":
        raise OCRError("Reading screenshots uses Windows' built-in text recognition, so it only works on Windows.")
    fd, path = tempfile.mkstemp(suffix=".png")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(OCR_SCRIPT), "-Path", path],
            capture_output=True, timeout=90,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    out = proc.stdout.decode("utf-8", "replace").strip()
    if proc.returncode != 0 or not out.startswith("{"):
        err = (proc.stderr.decode("utf-8", "replace") or out).strip().splitlines()
        raise OCRError("Windows couldn't read the screenshot" + (f": {err[-1]}" if err else "."))
    data = json.loads(out)
    if data.get("error"):
        raise OCRError(data["error"])
    return data


# ------------------------------------------------------------------ making sense of it

def _clean(text: str) -> str:
    t = text.replace("—", "–").replace(" - ", " – ")
    t = re.sub(r"\s+", " ", t).strip(" |·•")
    return t


def parse(page: dict) -> list[dict]:
    """Turn OCR lines into [{"title", "hours", "little"}], top to bottom."""
    width = page.get("width") or 1000
    lines = []
    raw_lines = page.get("lines") or []
    if isinstance(raw_lines, dict):  # PowerShell unwraps one-item lists
        raw_lines = [raw_lines]
    for ln in raw_lines:
        ws = ln.get("words") or []
        if isinstance(ws, dict):
            ws = [ws]
        words = [w for w in ws if str(w.get("t", "")).strip()]
        if words:
            lines.append(words)
    if not lines:
        return []

    def text(words):
        return " ".join(w["t"] for w in words)

    played = [ws for ws in lines if PLAYED.search(text(ws))]
    if not played:
        return []
    col_x = statistics.median(min(w["x"] for w in ws) for ws in played)
    tol = 0.035 * width
    line_h = statistics.median(max(w["h"] for w in ws) for ws in played) or 30

    # Keep only words in the text column (thumbnails sit to the left of it).
    rows = []
    for ws in lines:
        kept = [w for w in ws if w["x"] >= col_x - tol]
        if not kept:
            continue
        kept.sort(key=lambda w: w["x"])
        rows.append({
            "text": text(kept),
            "x": min(w["x"] for w in kept),
            "y": min(w["y"] for w in kept),
            "b": max(w["y"] + w["h"] for w in kept),
        })
    rows.sort(key=lambda r: r["y"])

    items, buf = [], []
    for r in rows:
        m = PLAYED.search(r["text"])
        if m:
            # Title lines sit just above their "Played for" line, left-aligned with it.
            title_rows = [b for b in buf if abs(b["x"] - col_x) <= tol]
            if title_rows and r["y"] - title_rows[-1]["b"] <= 2.6 * line_h:
                title = _clean(" ".join(b["text"] for b in title_rows))
                if m.group(1):
                    hours = float(m.group(1).replace(",", ""))
                    items.append({"title": title, "hours": hours, "little": False})
                else:
                    items.append({"title": title, "hours": LITTLE_WHILE_HOURS, "little": True})
            buf = []
            continue
        # A big gap means the previous text (header, status bar) isn't part of this title.
        if buf and r["y"] - buf[-1]["b"] > 1.8 * line_h:
            buf = []
        buf.append(r)
        buf = buf[-3:]
    return items


def display_title(title: str) -> str:
    t = re.sub(r"\s*[–-]\s*Nintendo Switch\s*2\s*Edition\s*$", "", title, flags=re.I)
    t = re.sub(r"\s*[–-]\s*Nintendo Switch Edition\s*$", "", t, flags=re.I)
    return t.strip()


# ------------------------------------------------------------------ preview and import

def _key(title: str) -> str:
    return db.normalize_title(display_title(title))


def preview(images_b64: list[str]) -> dict:
    """Read every screenshot, merge duplicates (overlapping scrolls), compare with the library."""
    found: dict[str, dict] = {}
    problems = []
    for i, b64 in enumerate(images_b64):
        raw = base64.b64decode(b64.split(",", 1)[-1])
        try:
            page = ocr_image(raw)
        except OCRError as e:
            problems.append(f"Screenshot {i + 1}: {e}")
            continue
        items = parse(page)
        if not items:
            problems.append(f"Screenshot {i + 1}: no games found. Is it the Play Activity list?")
        for it in items:
            k = _key(it["title"])
            if not k:
                continue
            if k not in found or it["hours"] > found[k]["hours"]:
                found[k] = {**it, "key": k}
    out = [{"key": k, "title": display_title(it["title"]), "raw_title": it["title"], "hours": it["hours"],
            "little": it["little"], "platform": "switch2"} for k, it in found.items()]
    return {"items": annotate(out), "problems": problems}


DEMO = re.compile(r"\b(demo|trial|playtest)\b", re.I)


def annotate(items: list[dict]) -> list[dict]:
    """Mark each found game as new, already in the library, or joining a game you have elsewhere."""
    for row in items:
        k = row["key"]
        row.update(system=bool(SYSTEM_APP.search(row["title"])), demo=bool(DEMO.search(row["title"])),
                   status="new", current_hours=None, game_title=None, current_platform=None)
        existing = _existing(k)
        if existing:
            row["status"] = "update"
            row["current_hours"] = round(existing["playtime_min"] / 60, 1)
            row["game_title"] = existing["game_title"]
            row["current_platform"] = existing["platform"]
        else:
            g = db.query("SELECT title FROM games WHERE norm_title=?", (k,))
            if g:
                row["status"] = "join"  # you already have it on another platform
                row["game_title"] = g[0]["title"]
    return items


def _existing(key: str):
    rows = db.query(
        """SELECT e.id, e.playtime_min, e.platform, e.external_id, e.first_played, e.last_played,
                  g.title AS game_title FROM entries e JOIN games g ON g.id = e.game_id
           WHERE e.platform IN ('switch2', 'switch') AND (e.external_id=? OR g.norm_title=?)
           ORDER BY e.external_id=? DESC, e.manual DESC LIMIT 1""",
        (f"nsw:{key}", key, f"nsw:{key}"),
    )
    return rows[0] if rows else None


def apply(items: list[dict]) -> dict:
    """Add new games and update known ones. Playtime only ever goes up (Nintendo shows
    "or more"); dates and the day-by-day list come from screen recordings."""
    added, updated, new_ids = 0, 0, []
    with db.tx() as c:
        for it in items:
            title = display_title(str(it.get("title") or "").strip())
            if not title:
                continue
            key = db.normalize_title(title)
            minutes = int(round(float(it.get("hours") or 0) * 60))
            platform = it.get("platform") if it.get("platform") in ("switch", "switch2") else None
            first, last = _int(it.get("first_played")), _int(it.get("last_played"))
            existing = _existing(key)
            if existing:
                eid, changed = existing["id"], False
                if minutes > existing["playtime_min"]:
                    c.execute("UPDATE entries SET playtime_min=?, updated_at=? WHERE id=?", (minutes, db.now(), eid))
                    changed = True
                if first and (not existing["first_played"] or first < existing["first_played"]):
                    c.execute("UPDATE entries SET first_played=? WHERE id=?", (first, eid))
                    changed = True
                if last and (not existing["last_played"] or last > existing["last_played"]):
                    c.execute("UPDATE entries SET last_played=? WHERE id=?", (last, eid))
                    changed = True
                # Copies the app imported can move between Switch and Switch 2 when the recording says so.
                if platform and platform != existing["platform"] and existing["external_id"].startswith("nsw:"):
                    clash = c.execute("SELECT 1 FROM entries WHERE platform=? AND external_id=?",
                                      (platform, existing["external_id"])).fetchone()
                    if not clash:
                        c.execute("UPDATE entries SET platform=? WHERE id=?", (platform, eid))
                        changed = True
                updated += changed
            else:
                before = c.execute("SELECT COALESCE(MAX(id), 0) m FROM games").fetchone()["m"]
                gid = db.upsert_entry(c, platform=platform or "switch2", external_id=f"nsw:{key}", name=title,
                                      playtime_min=minutes, last_played=last, first_played=first, manual=True)
                eid = c.execute("SELECT id FROM entries WHERE platform=? AND external_id=?",
                                (platform or "switch2", f"nsw:{key}")).fetchone()["id"]
                added += 1
                if gid > before:
                    new_ids.append(gid)
            if it.get("days"):
                c.execute("UPDATE entries SET days_played=MAX(COALESCE(days_played, 0), ?) WHERE id=?",
                          (int(it["days"]), eid))
            for day, mins in it.get("sessions") or []:
                if isinstance(day, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                    c.execute("INSERT OR REPLACE INTO play_days(entry_id, day, minutes) VALUES (?,?,?)",
                              (eid, day, max(0, int(mins))))
        db.log_playtime(c)
    return {"added": added, "updated": updated, "new_ids": new_ids}


def _int(v):
    try:
        return int(v) if v else None
    except (TypeError, ValueError):
        return None
