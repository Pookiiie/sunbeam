"""Tiny HTTP client on top of urllib (no third-party packages needed)."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"
)


class HttpError(Exception):
    def __init__(self, status: int, body: bytes, url: str):
        self.status = status
        self.body = body
        self.url = url
        snippet = body[:300].decode("utf-8", "replace") if body else ""
        super().__init__(f"HTTP {status} from {urllib.parse.urlsplit(url).netloc}: {snippet}")


class Response:
    def __init__(self, status: int, headers, body: bytes, url: str):
        self.status = status
        self.headers = headers  # email.message.Message: case-insensitive .get()
        self.body = body
        self.url = url

    def json(self):
        return json.loads(self.body.decode("utf-8")) if self.body else None

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: D401 - urllib hook
        return None


_follow = urllib.request.build_opener()
_manual = urllib.request.build_opener(_NoRedirect)


def request(
    method: str,
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    data=None,
    json_body=None,
    timeout: float = 30,
    follow_redirects: bool = True,
    retries: int = 3,
) -> Response:
    if params:
        clean = {k: v for k, v in params.items() if v is not None}
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(clean)
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    hdrs.update(headers or {})
    body = None
    if json_body is not None:
        body = json.dumps(json_body).encode()
        hdrs["Content-Type"] = "application/json"
    elif isinstance(data, dict):
        body = urllib.parse.urlencode(data).encode()
        hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
    elif isinstance(data, str):
        body = data.encode()
    elif data is not None:
        body = data

    opener = _follow if follow_redirects else _manual
    attempt = 0
    while True:
        attempt += 1
        req = urllib.request.Request(url, data=body, headers=hdrs, method=method.upper())
        try:
            with opener.open(req, timeout=timeout) as r:
                return Response(r.status, r.headers, r.read(), url)
        except urllib.error.HTTPError as e:
            raw = b""
            try:
                raw = e.read() or b""
            except Exception:
                pass
            if not follow_redirects and 300 <= e.code < 400:
                return Response(e.code, e.headers, raw, url)
            if (e.code == 429 or e.code >= 500) and attempt <= retries:
                wait = e.headers.get("Retry-After") if e.headers else None
                time.sleep(min(float(wait), 30) if wait and wait.isdigit() else 1.5 * attempt)
                continue
            raise HttpError(e.code, raw, url) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt <= retries:
                time.sleep(1.5 * attempt)
                continue
            raise HttpError(0, str(e).encode(), url) from None


def get_json(url: str, **kw):
    return request("GET", url, **kw).json()


class RateLimiter:
    """Allow at most one call every `interval` seconds across threads."""

    def __init__(self, per_second: float):
        self.interval = 1.0 / per_second
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self):
        with self._lock:
            now = time.monotonic()
            if now < self._next:
                time.sleep(self._next - now)
                now = self._next
            self._next = now + self.interval
