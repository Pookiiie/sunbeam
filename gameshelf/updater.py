"""Updates from GitHub Releases (github.com/Pookiiie/GameShelf).

GameShelf asks GitHub for the latest release now and then (at most every 6 hours). When
it's newer, the page offers "Update now": the release's zip is downloaded, GameShelf's own
files are replaced (your data folder is never touched), and GameShelf restarts itself.

Only the Windows download updates itself; a copy run with your own Python just links to
the release page. A release that needs a newer bundled Python (runtime/RUNTIME_VERSION
changed) can't swap it while running, so it asks you to download the new zip instead.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

from . import __version__, db, desktop, net
from .paths import APP_DIR, DATA_DIR, ROOT_DIR

REPO = "Pookiiie/GameShelf"
LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
CHECK_EVERY = 6 * 3600
KEEP = {"data", "runtime"}  # never replaced by an update

_state: dict = {"busy": False, "error": None, "stage": None}


def _ver(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:3]) or (0,)


def runtime_version(root: Path = ROOT_DIR) -> str | None:
    f = root / "runtime" / "RUNTIME_VERSION"
    return f.read_text().strip() if f.exists() else None


def check(force: bool = False) -> dict:
    """{current, latest, newer, notes, url, can_install} (cached for a few hours)."""
    cached = db.get_meta("update_check")
    info = None
    if cached and not force:
        try:
            info = json.loads(cached)
            if time.time() - info.get("at", 0) > CHECK_EVERY:
                info = None
        except ValueError:
            info = None
    if info is None:
        try:
            rel = net.get_json(LATEST, headers={"Accept": "application/vnd.github+json"}, retries=0, timeout=15) or {}
        except Exception as e:
            return {"current": __version__, "error": str(e), "newer": False}
        asset = next((a for a in rel.get("assets") or [] if a.get("name", "").lower().endswith(".zip")), None)
        info = {"at": time.time(), "latest": (rel.get("tag_name") or "").lstrip("v"), "notes": rel.get("body") or "",
                "url": rel.get("html_url") or f"https://github.com/{REPO}/releases",
                "zip": asset and asset.get("browser_download_url"), "size": asset and asset.get("size")}
        db.set_meta("update_check", json.dumps(info))
    newer = bool(info.get("latest")) and _ver(info["latest"]) > _ver(__version__)
    return {"current": __version__, "latest": info.get("latest"), "newer": newer, "notes": info.get("notes", ""),
            "url": info.get("url"), "can_install": newer and bool(info.get("zip")) and desktop.packaged(),
            "packaged": desktop.packaged(), **{k: v for k, v in _state.items() if k != "busy"}, "busy": _state["busy"]}


def start_install(httpd) -> bool:
    if _state["busy"]:
        return False
    info = check()
    if not info.get("can_install"):
        raise RuntimeError("There's no update to install here.")
    _state.update(busy=True, error=None, stage="Downloading")
    threading.Thread(target=_install, args=(httpd,), daemon=True, name="update").start()
    return True


def _install(httpd) -> None:
    try:
        info = json.loads(db.get_meta("update_check") or "{}")
        work = DATA_DIR / "update"
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        zpath = work / "GameShelf.zip"
        resp = net.request("GET", info["zip"], headers={"Accept": "application/octet-stream"}, timeout=300, retries=1)
        zpath.write_bytes(resp.body)
        _state["stage"] = "Unpacking"
        with zipfile.ZipFile(zpath) as z:
            bad = [n for n in z.namelist() if n.startswith("/") or ".." in Path(n).parts]
            if bad:
                raise RuntimeError("The download looks damaged.")
            z.extractall(work / "new")
        # The zip holds one folder (GameShelf/) with the app inside.
        new_root = next((p.parent.parent for p in (work / "new").rglob("gameshelf/__init__.py")), None)
        if not new_root:
            raise RuntimeError("The download doesn't contain GameShelf.")
        if runtime_version(new_root) != runtime_version():
            raise RuntimeError("This update needs a fresh download (it comes with a newer Python). "
                               "Download the new zip from the release page and copy your data folder into it.")
        _state["stage"] = "Installing"
        for item in new_root.iterdir():
            if item.name in KEEP:
                continue
            dest = ROOT_DIR / item.name
            if item.is_dir():
                # copied over the running copy (Windows doesn't lock .py files); files that are gone
                # from the new version are left behind, which does no harm
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)
        shutil.rmtree(work, ignore_errors=True)
        db.set_meta("update_check", "")
        _state["stage"] = "Restarting"
        # Start the new version (it waits for this one to let go of the port), then stop.
        port = str(httpd.server_address[1])
        subprocess.Popen([desktop.pythonw(), "-m", "gameshelf", "--app", "--no-browser", "--wait-port", "--port", port],
                         cwd=str(ROOT_DIR), creationflags=desktop.NO_WINDOW | getattr(subprocess, "DETACHED_PROCESS", 0),
                         close_fds=True)
        time.sleep(0.5)
        desktop.quit_app(httpd)
    except Exception as e:
        _state.update(busy=False, error=str(e), stage=None)
