"""Nintendo Switch games from a screen recording of Play Activity.

In the Nintendo Switch app (your user page › Play Activity), tapping a game opens
a card with its first and last played dates, days played, time played and a
day-by-day list. Swiping sideways moves to the next game. A recording of that
swipe-through is read like this:

1. Find the moments the screen is still (a card has settled between swipes).
2. Grab those frames at full size and read them with Windows' built-in OCR.
3. Read each card: title, dates, totals, the daily list, and the console badge
   (the red Switch logo, with or without a "2").
4. Merge repeats of the same card, then preview before anything is added.

Frames are pulled out with ffmpeg: the one on PATH, or the copy that comes with
the imageio-ffmpeg Python package (the launcher installs it).
"""

from __future__ import annotations

import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from . import db, switch_import
from .paths import DATA_DIR

MAX_BYTES = 2 * 1024 ** 3  # 2 GB
FEW_MIN = 5                # "A few min." counts as five minutes

_lock = threading.Lock()
_job: dict = {"state": "idle"}


class VideoError(Exception):
    pass


# ------------------------------------------------------------------ ffmpeg

def ffmpeg_path() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg  # type: ignore
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _install_ffmpeg() -> str | None:
    """One-time download of the imageio-ffmpeg package (a self-contained ffmpeg)."""
    try:
        from . import desktop
        from pathlib import Path
        # The Windows download's own Python only looks in its Lib\site-packages folder.
        where = ["--target", str(Path(sys.prefix) / "Lib" / "site-packages")] if desktop.packaged() else ["--user"]
        subprocess.run([sys.executable, "-m", "pip", "install", *where, "--quiet",
                        "--disable-pip-version-check", "imageio-ffmpeg"],
                       capture_output=True, timeout=600,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        return None
    import importlib
    import site
    try:
        user_site = site.getusersitepackages()
        if user_site not in sys.path:
            sys.path.append(user_site)
    except Exception:
        pass
    importlib.invalidate_caches()
    return ffmpeg_path()


def _run(args: list[str], timeout: int = 300) -> bytes:
    proc = subprocess.run(args, capture_output=True, timeout=timeout,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise VideoError("Couldn't read the video" + (f": {err[-1]}" if err else "."))
    return proc.stdout


def still_moments(ff: str, path: str, fps: int = 10) -> list[float]:
    """Times (seconds) where the picture holds still: one per pause between swipes."""
    w, h = 64, 128
    raw = _run([ff, "-v", "error", "-i", path, "-an", "-vf", f"fps={fps},scale={w}:{h},format=gray",
                "-f", "rawvideo", "-"], timeout=900)
    size = w * h
    frames = [raw[i:i + size] for i in range(0, len(raw) - size + 1, size)]
    if not frames:
        raise VideoError("The video has no frames GameShelf can read.")
    diffs = [0.0] + [sum(abs(a - b) for a, b in zip(frames[i], frames[i - 1])) / size
                     for i in range(1, len(frames))]
    groups, cur = [], []
    for i, d in enumerate(diffs):
        if d < 0.8:
            cur.append(i)
        elif cur:
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    return [(g[len(g) // 2] + 0.5) / fps for g in groups]


def grab(ff: str, path: str, t: float, out_dir: str, n: int) -> tuple[bytes, bytes, int]:
    """Full-size PNG of the frame at t (for OCR), plus a quarter-size RGB copy of
    its top 40% (for the console badge). Returns (png, rgb, rgb_width)."""
    png = os.path.join(out_dir, f"f{n}.png")
    rgb = os.path.join(out_dir, f"f{n}.rgb")
    qw = 300
    _run([ff, "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", path,
          "-filter_complex", f"[0:v]split=2[a][b];[b]crop=iw:ih*0.4:0:0,scale={qw}:-2[c]",
          "-map", "[a]", "-frames:v", "1", png,
          "-map", "[c]", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", rgb])
    with open(png, "rb") as f:
        png_bytes = f.read()
    with open(rgb, "rb") as f:
        rgb_bytes = f.read()
    return png_bytes, rgb_bytes, qw


# ------------------------------------------------------------------ console badge

def badge(rgb: bytes, width: int) -> str | None:
    """'switch2', 'switch' or None. The badge is a small red square; Switch 2's has a
    white "2" on its right side, the original Switch's logo sits in the middle."""
    height = len(rgb) // (3 * width)
    if not height:
        return None

    def px(x, y):
        i = (y * width + x) * 3
        return rgb[i], rgb[i + 1], rgb[i + 2]

    red = [[False] * width for _ in range(height)]
    for y in range(height):
        row = red[y]
        base = y * width * 3
        for x in range(width):
            r, g, b = rgb[base + 3 * x], rgb[base + 3 * x + 1], rgb[base + 3 * x + 2]
            row[x] = r > 170 and g < 90 and b < 90
    seen = [[False] * width for _ in range(height)]
    best = None
    for y0 in range(height):
        for x0 in range(width):
            if not red[y0][x0] or seen[y0][x0]:
                continue
            stack, xs, ys, n = [(x0, y0)], [], [], 0
            seen[y0][x0] = True
            while stack:
                x, y = stack.pop()
                xs.append(x)
                ys.append(y)
                n += 1
                for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if 0 <= nx < width and 0 <= ny < height and red[ny][nx] and not seen[ny][nx]:
                        seen[ny][nx] = True
                        stack.append((nx, ny))
            bw, bh = max(xs) - min(xs) + 1, max(ys) - min(ys) + 1
            # Badge: about 6% of the screen width, square, solid red. Game icons are far bigger.
            if not (0.03 * width <= bw <= 0.10 * width and 0.75 <= bw / bh <= 1.33 and n >= 0.45 * bw * bh):
                continue
            if best is None or min(xs) < best[0]:
                best = (min(xs), min(ys), bw, bh)
    if not best:
        return None
    bx, by, bw, bh = best
    total = white = 0
    for y in range(by + int(0.1 * bh), by + max(int(0.6 * bh), 1)):
        for x in range(bx + int(0.78 * bw), bx + max(int(0.97 * bw), int(0.78 * bw) + 1)):
            if x < width and y < height:
                total += 1
                r, g, b = px(x, y)
                white += r > 200 and g > 150 and b > 150
    return "switch2" if total and white / total > 0.08 else "switch"


# ------------------------------------------------------------------ reading a card

DATE = re.compile(r"^(\d{1,4})[/.\-](\d{1,2})[/.\-](\d{1,4})$")
LABELS = {"first": "first", "last": "last", "days": "days", "time": "time"}


def _words(page: dict) -> list[dict]:
    raw = page.get("lines") or []
    if isinstance(raw, dict):
        raw = [raw]
    out = []
    for ln in raw:
        ws = ln.get("words") or []
        if isinstance(ws, dict):
            ws = [ws]
        out += [w for w in ws if str(w.get("t", "")).strip()]
    return out


def _rows(words: list[dict]) -> list[list[dict]]:
    """Group words into visual rows by their vertical centre."""
    if not words:
        return []
    hs = sorted(w["h"] for w in words)
    tol = 0.55 * hs[len(hs) // 2]
    rows: list[list[dict]] = []
    for w in sorted(words, key=lambda w: w["y"] + w["h"] / 2):
        c = w["y"] + w["h"] / 2
        if rows and abs(c - rows[-1][-1]["_c"]) <= tol:
            rows[-1].append({**w, "_c": c})
        else:
            rows.append([{**w, "_c": c}])
    for r in rows:
        r.sort(key=lambda w: w["x"])
    return rows


def _text(ws) -> str:
    return " ".join(str(w["t"]) for w in ws)


def _digits(s: str) -> str:
    return s.translate(str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1", "|": "1", "S": "5", "B": "8"}))


def _date(s: str):
    """(a, b, c) numbers of a date token, or None."""
    m = DATE.match(_digits(s.strip(" .,")))
    return tuple(int(x) for x in m.groups()) if m else None


def _duration(s: str) -> int | None:
    """Minutes from '2 hr. 15 min.', '45 min.', 'A few min.'."""
    t = _digits(s.lower()).replace(" ", "")
    if "few" in t:
        return FEW_MIN
    h = re.search(r"(\d+)hr", t)
    m = re.search(r"(\d+)min", t)
    if not h and not m:
        return None
    return (int(h.group(1)) if h else 0) * 60 + (int(m.group(1)) if m else 0)


def parse_card(page: dict) -> dict | None:
    words = _words(page)
    width = page.get("width") or 1000
    rows = _rows(words)
    labels: dict[str, list[dict]] = {}
    for r in rows:
        for i, w in enumerate(r[:-1]):
            k = str(w["t"]).strip().lower()
            if k in LABELS and str(r[i + 1]["t"]).strip().lower().startswith("play") and k not in labels:
                labels[k] = r
                break
    if "first" in labels and "time" in labels:
        left = min(w["x"] for k in labels for w in labels[k] if str(w["t"]).lower() in LABELS)
    else:
        # The card stopped a little off-centre and the label starts are cut off ("st played"):
        # the four "played" rows still give it away, in order.
        played = []
        for r in rows:
            for w in r:
                if str(w["t"]).strip().lower().startswith("played") and w["x"] < 0.4 * width:
                    played.append((r, w))
                    break
        if len(played) < 4:
            return None
        gaps = [played[i + 1][1]["y"] - played[i][1]["y"] for i in range(len(played) - 1)]
        start = next((i for i in range(len(played) - 3)
                      if max(gaps[i:i + 3]) < 1.3 * min(gaps[i:i + 3])), None)
        if start is None:
            return None
        labels = {k: played[start + i][0] for i, k in enumerate(("first", "last", "days", "time"))}
        left = played[start][1]["x"] - 0.071 * width
    right = left + 0.88 * width   # the next card's edge starts beyond this

    def value(k):
        r = labels.get(k)
        if not r:
            return ""
        return _text([w for w in r if w["x"] > left + 0.35 * width and w["x"] + w["w"] <= right])

    first_y = labels["first"][0]["y"]
    lh = statistics.median(w["h"] for k in labels for w in labels[k])

    # Title: the text right of the game icon, above the dates. Lines that sit close together
    # belong to it; the console badge above is separated by a gap.
    cand = [[w for w in r if w["x"] >= left + 0.26 * width and w["x"] + w["w"] <= right]
            for r in rows if r[0]["y"] < first_y - 0.8 * lh]
    cand = [r for r in cand if r]
    title_rows: list[list[dict]] = []
    for r in reversed(cand):
        top = min(w["y"] for w in r)
        bottom = max(w["y"] + w["h"] for w in r)
        th = statistics.median(w["h"] for w in r)
        txt = _text(r)
        if title_rows:
            prev_top = min(w["y"] for w in title_rows[0])
            if prev_top - bottom > 0.8 * th:
                break
        elif first_y - bottom > 9 * lh:
            break
        if th < 0.6 * lh:  # the tiny "NINTENDO SWITCH" under the console badge
            continue
        if not re.search(r"[A-Za-z]{2}", txt):
            if title_rows:
                break
            continue
        title_rows.insert(0, r)
        if len(title_rows) >= 4:
            break
    title = switch_import._clean(" ".join(_text(r) for r in title_rows))
    title = re.sub(r"\s*(…|\.\.\.)\s*$", "", title)
    if not title:
        return None

    first, last = _date(value("first").split(" ")[-1] if value("first") else ""), \
        _date(value("last").split(" ")[-1] if value("last") else "")
    days = re.search(r"\d+", _digits(value("days")))
    tp = value("time")
    over = re.search(r"(\d[\d,]*)\s*h", _digits(tp), re.I)
    total_min = int(over.group(1).replace(",", "")) * 60 if over else (FEW_MIN if "few" in tp.lower() else None)

    # Daily list below the "Play Activity" heading.
    pa_y = None
    for r in rows:
        if r[0]["y"] > first_y and re.search(r"play\s*activ", _text(r), re.I) and abs(r[0]["x"] - left) < 0.06 * width:
            pa_y = r[0]["y"]
            break
    sessions = []
    if pa_y is not None:
        for r in rows:
            if r[0]["y"] <= pa_y + lh:
                continue
            ws = [w for w in r if w["x"] + w["w"] <= right and w["x"] >= left - 0.03 * width]
            if not ws:
                continue
            d = _date(str(ws[0]["t"]))
            mins = _duration(_text(ws[1:]))
            if d and mins is not None and abs(ws[0]["x"] - left) < 0.06 * width:
                sessions.append((d, mins))
    return {"title": title, "first": first, "last": last,
            "days": int(days.group()) if days else None, "total_min": total_min,
            "over": bool(over), "sessions": sessions}


def _resolve_dates(cards: list[dict]):
    """Work out the date order (M/D/Y, D/M/Y or Y/M/D) from every date seen, then
    turn them into timestamps."""
    trip = [c[k] for c in cards for k in ("first", "last") if c[k]] + [s[0] for c in cards for s in c["sessions"]]
    if any(a > 31 for a, _, _ in trip):
        order = "ymd"
    elif any(a > 12 for a, _, _ in trip):
        order = "dmy"
    else:
        order = "mdy"

    def ts(t):
        if not t:
            return None
        a, b, c = t
        y, m, d = (a, b, c) if order == "ymd" else (c, a, b) if order == "mdy" else (c, b, a)
        if y < 100:
            y += 2000
        try:
            return int(datetime(y, m, d, 12).timestamp())
        except ValueError:
            return None

    def day(t):
        s = ts(t)
        return datetime.fromtimestamp(s).strftime("%Y-%m-%d") if s else None

    for c in cards:
        c["first_ts"], c["last_ts"] = ts(c["first"]), ts(c["last"])
        c["play_days"] = {day(d): m for d, m in c["sessions"] if day(d)}


def merge(cards: list[dict]) -> list[dict]:
    """One item per game: the same card often shows up in several still moments."""
    _resolve_dates(cards)
    out: dict[str, dict] = {}
    for c in cards:
        title = switch_import.display_title(c["title"])
        k = db.normalize_title(title)
        if not k:
            continue
        it = out.get(k)
        if not it:
            it = out[k] = {"key": k, "title": title, "raw_title": c["title"], "platform": None,
                           "total_min": None, "over": False, "first_played": None, "last_played": None,
                           "days": None, "play_days": {}, "_votes": {}}
        if len(c["title"]) > len(it["raw_title"]):  # a longer read is usually the complete one
            it["raw_title"], it["title"] = c["title"], title
        if c.get("platform"):
            it["_votes"][c["platform"]] = it["_votes"].get(c["platform"], 0) + 1
        if c["total_min"] is not None and (it["total_min"] is None or c["total_min"] > it["total_min"]):
            it["total_min"], it["over"] = c["total_min"], c["over"]
        for f, pick in (("first_played", min), ("last_played", max)):
            v = c["first_ts" if f == "first_played" else "last_ts"]
            if v:
                it[f] = pick(filter(None, [it[f], v]))
        if c["days"] is not None:
            it["days"] = max(it["days"] or 0, c["days"])
        it["play_days"].update(c["play_days"])
    items = []
    for it in out.values():
        votes = it.pop("_votes")
        it["platform"] = max(votes, key=votes.get) if votes else "switch2"
        listed = sum(it["play_days"].values())
        # "Over 90 hours" is a minimum; the daily list can add up to more.
        minutes = max(it["total_min"] or 0, listed)
        it["hours"] = round(minutes / 60, 2)
        it["little"] = minutes < 60 and not it["over"]
        it["sessions"] = sorted(([d, m] for d, m in it.pop("play_days").items()), reverse=True)
        items.append(it)
    return items


# ------------------------------------------------------------------ the job

def status() -> dict:
    with _lock:
        return dict(_job)


def mark_applied():
    with _lock:
        if _job.get("state") == "done":
            _job["applied"] = True


def _set(**kw):
    with _lock:
        _job.update(kw)


def start(path: str, name: str = "") -> bool:
    with _lock:
        if _job.get("state") in ("starting", "setup", "scanning", "reading"):
            return False
        _job.clear()
        _job.update(state="starting", name=name, done=0, total=0, items=None, problems=[], started=time.time())
    threading.Thread(target=_run_job, args=(path,), daemon=True, name="switch-video").start()
    return True


def _run_job(path: str):
    tmp = tempfile.mkdtemp(prefix="frames-", dir=str(DATA_DIR))
    try:
        ff = ffmpeg_path()
        if not ff:
            _set(state="setup")
            ff = _install_ffmpeg()
        if not ff:
            raise VideoError("GameShelf needs a small video tool (ffmpeg) to read recordings and couldn't download it. "
                             "Check your internet connection and try again, or use screenshots instead.")
        _set(state="scanning")
        moments = still_moments(ff, path)[:400]
        _set(state="reading", total=len(moments))
        cards, problems = [], []

        def one(arg):
            n, t = arg
            png, rgb, qw = grab(ff, path, t, tmp, n)
            page = switch_import.ocr_image(png)
            card = parse_card(page)
            if card:
                card["platform"] = badge(rgb, qw)
            return card

        with ThreadPoolExecutor(max_workers=4) as ex:
            for card in ex.map(lambda a: _safe(one, a, problems), enumerate(moments)):
                if card:
                    cards.append(card)
                with _lock:
                    _job["done"] = _job.get("done", 0) + 1
        items = merge(cards)
        if not items and not problems:
            problems.append("No games found. Is it a recording of Play Activity with a game's card open?")
        _set(state="done", items=switch_import.annotate(items), problems=problems[:5])
    except (VideoError, switch_import.OCRError) as e:
        _set(state="error", error=str(e))
    except Exception as e:  # never kill the server
        _set(state="error", error=f"Something went wrong reading the video: {e}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            os.remove(path)
        except OSError:
            pass


def _safe(fn, arg, problems):
    try:
        return fn(arg)
    except switch_import.OCRError as e:
        problems.append(str(e))
    except VideoError as e:
        problems.append(str(e))
    return None
