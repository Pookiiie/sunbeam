"""Your Nintendo wish list (Switch and Switch 2), read from nintendo.com the way the site reads it.

Sign in once: Settings › Nintendo Switch › "Sign in to nintendo.com" opens an ordinary Edge window
with a profile of Sunbeam's own (data/nintendo-web, separate from your everyday browser; Edge
keeps its cookies encrypted for your Windows account). Sunbeam doesn't touch that window: you sign
in, see your wish list and close it. Then, and at every sync, Sunbeam opens the same page in a
window out of sight and reads the list the page loads (cdp.py). Nothing is sent anywhere else.

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

def _read(visible: bool, timeout: float) -> dict:
    """Open the wish list page in Sunbeam's Nintendo profile and return the page's own answer:
    {"items": [...], "more": bool}. Hidden: raises SignedOut when the page sends you to sign in."""
    from ..cdp import Browser, CDPError
    exe = _exe()
    if not exe:
        raise Unavailable("Microsoft Edge (or Chrome) wasn't found on this PC.")
    if not _lock.acquire(blocking=False):
        raise Unavailable("The Nintendo window is already open.")
    b = None
    try:
        b = Browser(exe, PROFILE, visible=visible, size=(600, 820) if visible else (1100, 900), extra_args=EXTRA_ARGS)
        b.call("Network.enable", {"maxPostDataSize": 262144})
        b.call("Page.enable")
        b.call("Page.navigate", {"url": WISH_URL})
        graph: set[str] = set()
        signin_at = None
        was_signing_in = False
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
                if not visible and signin_at and time.time() - signin_at > 8:
                    raise SignedOut()
                continue
            m, p = ev["method"], ev.get("params") or {}
            if m == "Page.frameNavigated" and not (p.get("frame") or {}).get("parentId"):
                url = (p.get("frame") or {}).get("url") or ""
                if ACCOUNTS in url:
                    signin_at, was_signing_in = time.time(), True
                else:
                    signin_at = None
                    if visible and was_signing_in and url.startswith("https://www.nintendo.com/") and "wish-list" not in url:
                        # Signed in, and Nintendo brought you back somewhere else: on to your wish list.
                        was_signing_in = False
                        b.call("Page.navigate", {"url": WISH_URL})
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
                    continue
                try:
                    data = json.loads(text)
                except ValueError:
                    continue
                wl = ((data.get("data") or {}).get("customer") or {}).get("wishList")
                if isinstance(wl, dict) and isinstance(wl.get("items"), list):
                    return {"items": wl["items"], "more": bool(wl.get("hasNextPage"))}
        if signin_at and not visible:
            raise SignedOut()
        raise Unavailable("Nintendo's wish list page didn't load in time.")
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
    try:
        data = _read(visible=False, timeout=60)
    except SignedOut:
        settings.update({"nin_web_state": "signed_out"})
        raise Unavailable("Nintendo's sign-in has ended, so your wish list wasn't updated (prices still were). "
                          "Sign in again in Settings › Nintendo Switch.") from None
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


# ------------------------------------------------------------------ signing in (the visible window)

def _plain_window(exe: str):
    """Edge (or Chrome) on the wish list page with Sunbeam's Nintendo profile, as an ordinary
    window: nothing attached to it while you sign in. Returns the process."""
    import os
    import subprocess
    PROFILE.mkdir(parents=True, exist_ok=True)
    args = [exe, f"--user-data-dir={PROFILE}", "--no-first-run", "--no-default-browser-check", "--disable-sync",
            "--new-window", "--window-size=1000,860", *EXTRA_ARGS, WISH_URL]
    return subprocess.Popen(args, creationflags=0x08000000 if os.name == "nt" else 0, close_fds=True)


def start_signin() -> dict:
    """Sign in once. The window is an ordinary browser window that Sunbeam doesn't touch (Nintendo's
    sign-in turns away a browser that's being driven); once you close it, Sunbeam reads your wish
    list the way it does at every sync."""
    if _signin.get("state") in ("waiting", "checking"):
        return dict(_signin)
    exe = _exe()
    if not exe:
        raise Unavailable("Microsoft Edge (or Chrome) wasn't found on this PC.")
    if not _lock.acquire(blocking=False):
        raise Unavailable("Sunbeam is reading your Nintendo wish list right now. Try again in a minute.")
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
                data = _read(visible=False, timeout=60)
            except SignedOut:
                raise Unavailable("Sunbeam couldn't see you signed in to nintendo.com in that window. Choose Sign in "
                                  "again, sign in until your wish list shows, then close the window.") from None
            rows = items(data["items"])
            from . import wishlist
            wishlist._store("nintendo", rows)
            wishlist.link()
            settings.update({"nin_web": str(db.now()), "nin_web_state": "ok", "nin_web_at": str(db.now())})
            _signin.update(state="done", count=len(rows))
        except Exception as e:
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
