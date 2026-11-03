"""Your Nintendo wish list (Switch and Switch 2), read from nintendo.com the way the site reads it.

Sign in once: Settings › Nintendo Switch › "Sign in to nintendo.com" opens an ordinary Edge window
with an empty profile of Sunbeam's own (data/nintendo-web, separate from your everyday browser; Edge
keeps its cookies encrypted for your Windows account). Sunbeam doesn't touch that window: you sign
in, see your wish list and close it. Then, and at every sync, Sunbeam opens the same page in a
window out of sight and reads the list the page loads (cdp.py). Nothing is sent anywhere else.
If Nintendo's site turns that window away (its protection against automated browsers), Sunbeam
says so and stops reading by itself: it doesn't try to get past it.

Prices: the list comes with each game's Canadian eShop price and sale. Between reads, and when the
sign-in has ended, prices are refreshed from Nintendo's public price service (by eShop id).
"""

from __future__ import annotations

import base64
import datetime
import json
import re
import shutil
import threading
import time
import urllib.parse

from .. import db, net, settings
from ..paths import DATA_DIR

WISH_URL = "https://www.nintendo.com/en-ca/wish-list/"
STORE_URL = "https://www.nintendo.com/en-ca/store/products/{key}/"
PRICE_API = "https://api.ec.nintendo.com/v1/price"
GRAPH = "https://graph.nintendo.com"        # where the page asks for your wish list
ACCOUNTS = "accounts.nintendo.com"          # where it sends you to sign in
EXTRA_ARGS: list[str] = []
PROFILE = DATA_DIR / "nintendo-web"
SIGNIN_MINUTES = 20

_lock = threading.Lock()   # one window on the profile at a time
_signin: dict = {"state": "idle"}


class SignedOut(Exception):
    """Nintendo's sign-in in Sunbeam's window has ended: sign in again."""


class Unavailable(Exception):
    pass


def linked() -> bool:
    return bool(settings.get("nin_web"))


def _exe() -> str | None:
    from .. import desktop
    return desktop._browser() if desktop.is_windows() else None


# ------------------------------------------------------------------ reading the page

class Blocked(Exception):
    """Nintendo's site turned Sunbeam's window away (its protection against automated browsers)."""


BLOCKED_TEXT = ("Nintendo's site turned away the window Sunbeam reads your wish list in (its protection against "
                "automated browsers), so Sunbeam can't read your wish list by itself.")


def _where(url) -> str:
    """Host and path only, for the log (never the query: it can carry sign-in codes)."""
    u = urllib.parse.urlsplit(str(url or ""))
    return f"{u.netloc}{u.path}"[:100]


def _refused(resp: dict) -> bool:
    """A refusal from Nintendo's site itself (403: what its "Reference Code" error page answers)."""
    host = urllib.parse.urlsplit(str(resp.get("url") or "")).netloc
    return resp.get("status") == 403 and host.endswith("nintendo.com")


def _read(timeout: float) -> dict:
    """Open the wish list page with Sunbeam's Nintendo profile, in a window out of sight, and return
    the page's own answer: {"items": [...], "more": bool}. Raises SignedOut when the page wants you
    to sign in, and Blocked when Nintendo's site turns the window away (Sunbeam doesn't try to get
    past that: it stops reading by itself until you try again in Settings)."""
    from ..cdp import Browser, CDPError
    exe = _exe()
    if not exe:
        raise Unavailable("Microsoft Edge (or Chrome) wasn't found on this PC.")
    if not _lock.acquire(blocking=False):
        raise Unavailable("The Nintendo window is already open.")
    b = None
    seen = {"page": None, "status": None, "server": None, "data": 0}  # for the log

    def note(what: str) -> None:
        print(f"  Nintendo wish list: {what} (page {seen['page'] or 'none'}, HTTP {seen['status']}, "
              f"server {seen['server'] or '?'}, {seen['data']} answers from Nintendo's data service)", flush=True)
    try:
        b = Browser(exe, PROFILE, visible=False, size=(1100, 900), extra_args=EXTRA_ARGS)
        b.call("Network.enable", {"maxPostDataSize": 262144})
        b.call("Page.enable")
        main = ((b.call("Page.getFrameTree").get("frameTree") or {}).get("frame") or {}).get("id")
        b.call("Page.navigate", {"url": WISH_URL})
        graph: set[str] = set()
        signin_at = None  # when the page last showed it wants you signed in
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not b.alive():
                raise Unavailable("The window was closed.")
            try:
                ev = b.event(1.0)
            except CDPError:
                if not b.alive():
                    raise Unavailable("The window was closed.") from None
                raise
            if not ev:
                if signin_at and time.time() - signin_at > 8:
                    note("not signed in")
                    raise SignedOut()
                continue
            m, p = ev["method"], ev.get("params") or {}
            if m == "Page.frameNavigated" and not (p.get("frame") or {}).get("parentId"):
                url = (p.get("frame") or {}).get("url") or ""
                signin_at = time.time() if ACCOUNTS in url else None
            elif m == "Network.responseReceived":
                resp = p.get("response") or {}
                if p.get("type") == "Document" and (p.get("frameId") == main or not main):
                    headers = {str(k).lower(): str(v) for k, v in (resp.get("headers") or {}).items()}
                    seen.update(page=_where(resp.get("url")), status=resp.get("status"),
                                server=headers.get("server", "").replace("\n", " / ")[:40])
                    if _refused(resp):
                        note("turned away")
                        raise Blocked()
                elif p.get("requestId") in graph:
                    seen["data"] += 1
                    if _refused(resp):
                        seen.update(status=resp.get("status"))
                        note("data service turned it away")
                        raise Blocked()
                    if resp.get("status") == 401:
                        signin_at = signin_at or time.time()
            elif m == "Network.requestWillBeSent":
                req = p.get("request") or {}
                if req.get("url", "").startswith(GRAPH):
                    graph.add(p["requestId"])
            elif m == "Network.loadingFinished" and p.get("requestId") in graph:
                try:
                    r = b.call("Network.getResponseBody", {"requestId": p["requestId"]})
                except CDPError:
                    continue
                text = base64.b64decode(r["body"]).decode("utf-8", "replace") if r.get("base64Encoded") else r.get("body", "")
                if '"wishList"' not in text:
                    if re.search(r'"customer"\s*:\s*null', text):
                        signin_at = signin_at or time.time()  # Nintendo's data service doesn't know who you are
                    continue
                try:
                    data = json.loads(text)
                except ValueError:
                    continue
                wl = ((data.get("data") or {}).get("customer") or {}).get("wishList")
                if isinstance(wl, dict) and isinstance(wl.get("items"), list):
                    print(f"  Nintendo wish list: read {len(wl['items'])} games.", flush=True)
                    return {"items": wl["items"], "more": bool(wl.get("hasNextPage"))}
        if signin_at:
            note("not signed in")
            raise SignedOut()
        note("no wish list in time")
        raise Unavailable("Nintendo's wish list page didn't show your wish list in time.")
    finally:
        if b:
            b.close()
        _lock.release()


# ------------------------------------------------------------------ turning it into wishlist rows

def _ts(s) -> int | None:
    if not s:
        return None
    try:
        return int(datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _money(v) -> str | None:
    if not isinstance(v, (int, float)):
        return None
    return "Free" if v == 0 else f"C${v:,.2f}"


def items(raw: list[dict]) -> list[dict]:
    now = db.now()
    out = []
    for it in raw:
        p = it.get("product") or {}
        name = (p.get("name") or "").strip()
        if not name:
            continue
        shop = p.get("eshopDetails") or {}
        if shop.get("isPurchased"):
            continue  # yours already
        pid = str(p.get("nsuid") or p.get("sku") or it.get("id") or "")
        if not pid:
            continue
        prices = p.get("prices") or {}
        final, regular = prices.get("finalPrice"), prices.get("regularPrice")
        on_sale = bool(prices.get("discounted")) and isinstance(final, (int, float)) and isinstance(regular, (int, float)) and regular > final
        rel_ts = _ts(p.get("releaseDate"))
        display = (p.get("releaseDateDisplay") or "").strip()
        precise = bool(re.search(r"\b\d{1,2}, \d{4}\b|\b\d{4}-\d{2}-\d{2}\b", display))
        img = (p.get("productImageSquare") or {}).get("url")
        if not img and (p.get("productImage") or {}).get("publicId"):
            img = "https://assets.nintendo.com/image/upload/c_fill,w_400/" + p["productImage"]["publicId"]
        out.append({
            "store": "nintendo", "item_id": pid, "title": name, "image": img,
            "url": STORE_URL.format(key=p["urlKey"]) if p.get("urlKey") else WISH_URL,
            "added_at": _ts(it.get("createdAt")),
            "release_ts": rel_ts, "release_text": None if precise or not display else display,
            "precision": "day" if (rel_ts and (precise or not display)) else None,
            "released": 1 if rel_ts and rel_ts <= now else (0 if rel_ts or display else None),
            "price_text": _money(final), "price_cents": round(final * 100) if isinstance(final, (int, float)) else None,
            "base_text": _money(regular) if on_sale else None,
            "base_cents": round(regular * 100) if on_sale else None,
            "discount_pct": round(100 * (1 - final / regular)) if on_sale else None,
            "sale_ends": _ts(shop.get("discountPriceEnd")) if on_sale else None,
        })
    return out


def fetch() -> list[dict]:
    """For wishlist.refresh(): your wish list, read in a window out of sight."""
    if not linked():
        return []
    if settings.get("nin_web_state") == "blocked":
        raise Unavailable("Nintendo's site turned away Sunbeam's window last time, so Sunbeam doesn't try by itself "
                          "(prices still update). Settings › Nintendo Switch › Wish list has Try again.")
    try:
        data = _read(timeout=60)
    except SignedOut:
        settings.update({"nin_web_state": "signed_out"})
        raise Unavailable("Nintendo's sign-in has ended, so your wish list wasn't updated (prices still were). "
                          "Sign in again in Settings › Nintendo Switch.") from None
    except Blocked:
        settings.update({"nin_web_state": "blocked"})
        raise Unavailable(BLOCKED_TEXT + " Prices still update.") from None
    settings.update({"nin_web_state": "ok", "nin_web_at": str(db.now())})
    return items(data["items"])


# ------------------------------------------------------------------ prices (no sign-in)

def refresh_prices() -> int:
    """Today's eShop prices for the Nintendo wish list games, from Nintendo's public price service."""
    try:
        rows = db.query("SELECT item_id FROM wishlist WHERE store='nintendo'")
    except Exception:
        return 0
    ids = [r["item_id"] for r in rows if str(r["item_id"]).isdigit()]
    done = 0
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        try:
            data = net.get_json(PRICE_API, params={"country": "CA", "lang": "en", "ids": ",".join(chunk)}, retries=1) or {}
        except net.HttpError:
            continue
        with db.tx() as c:
            for pr in data.get("prices") or []:
                pid = str(pr.get("title_id") or "")
                reg = (pr.get("regular_price") or {}).get("raw_value")
                disc = pr.get("discount_price") or {}
                if pid not in chunk or reg is None:
                    continue
                reg = float(reg)
                sale = float(disc["raw_value"]) if disc.get("raw_value") is not None else None
                on_sale = sale is not None and sale < reg
                final = sale if on_sale else reg
                c.execute("""UPDATE wishlist SET price_text=?, price_cents=?, base_text=?, base_cents=?, discount_pct=?,
                             sale_ends=? WHERE store='nintendo' AND item_id=?""",
                          (_money(final), round(final * 100), _money(reg) if on_sale else None,
                           round(reg * 100) if on_sale else None,
                           round(100 * (1 - final / reg)) if on_sale and reg else None,
                           _ts(disc.get("end_datetime")) if on_sale else None, pid))
                done += 1
    return done


# ------------------------------------------------------------------ signing in (an ordinary window)

def _plain_window(exe: str):
    """Edge (or Chrome) on the wish list page with Sunbeam's Nintendo profile, as an ordinary
    window: nothing attached to it while you sign in. Returns the process."""
    import os
    import subprocess
    PROFILE.mkdir(parents=True, exist_ok=True)
    args = [exe, f"--user-data-dir={PROFILE}", "--no-first-run", "--no-default-browser-check", "--disable-sync",
            "--new-window", "--window-size=1000,860", *EXTRA_ARGS, WISH_URL]
    return subprocess.Popen(args, creationflags=0x08000000 if os.name == "nt" else 0, close_fds=True)


def _fresh_profile() -> None:
    """Every sign-in starts from an empty profile: nothing left from an earlier try (Nintendo's
    sign-in can keep refusing a browser profile it refused once)."""
    shutil.rmtree(PROFILE, ignore_errors=True)
    if PROFILE.exists():
        raise Unavailable("Edge is still closing Sunbeam's Nintendo window. Wait a moment and try again.")


def start_signin() -> dict:
    """Sign in once. The window is an ordinary browser window, on an empty profile, that Sunbeam
    doesn't touch; once you close it, Sunbeam reads your wish list the way it does at every sync."""
    if _signin.get("state") in ("waiting", "checking"):
        return dict(_signin)
    exe = _exe()
    if not exe:
        raise Unavailable("Microsoft Edge (or Chrome) wasn't found on this PC.")
    if not _lock.acquire(blocking=False):
        raise Unavailable("Sunbeam is reading your Nintendo wish list right now. Try again in a minute.")
    try:
        _fresh_profile()
    except Exception:
        _lock.release()
        raise
    print("  Nintendo wish list: sign-in window opened (empty profile).", flush=True)
    _signin.clear()
    _signin.update(state="waiting", started=int(time.time()))

    def run():
        try:
            try:
                proc = _plain_window(exe)
                try:
                    proc.wait(timeout=SIGNIN_MINUTES * 60)
                except Exception:
                    proc.kill()
                    raise Unavailable("The Nintendo window was open a long time, so Sunbeam closed it. Try again.") from None
            finally:
                _lock.release()
            _signin.update(state="checking")
            try:
                data = _read(timeout=60)
            except SignedOut:
                raise Unavailable("Sunbeam couldn't see you signed in to nintendo.com in that window, so your wish "
                                  "list wasn't read. Choose Sign in to nintendo.com to try again.") from None
            except Blocked:
                settings.update({"nin_web_state": "blocked"})
                raise Unavailable(BLOCKED_TEXT) from None
            rows = items(data["items"])
            from . import wishlist
            wishlist._store("nintendo", rows)
            wishlist.link()
            settings.update({"nin_web": str(db.now()), "nin_web_state": "ok", "nin_web_at": str(db.now())})
            _signin.update(state="done", count=len(rows))
        except Exception as e:
            print(f"  Nintendo wish list: sign-in didn't finish ({e})", flush=True)
            _signin.update(state="error", error=str(e))
    threading.Thread(target=run, daemon=True, name="nintendo-signin").start()
    return dict(_signin)


def signin_status() -> dict:
    return dict(_signin)


def forget() -> None:
    """Sign out: delete Sunbeam's Nintendo browser profile and the Nintendo wish list."""
    if not _lock.acquire(timeout=5):
        raise Unavailable("Close the Nintendo window first, then try again.")
    try:
        shutil.rmtree(PROFILE, ignore_errors=True)
    finally:
        _lock.release()
    settings.update({"nin_web": "", "nin_web_state": "", "nin_web_at": ""})
    with db.tx() as c:
        c.execute("DELETE FROM wishlist WHERE store='nintendo'")
    from . import wishlist
    wishlist.link()
