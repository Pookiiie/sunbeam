"""The app as a desktop app (Windows): its own window, a tray icon, and quitting by itself.

- The window is Microsoft Edge (or Chrome) in app mode: no address bar or tabs, its own
  taskbar entry, with a profile of its own in data/window (so its size is remembered).
  Links to other sites open in your normal browser instead (the page sends them to
  /api/open), where you're signed in to Steam, PlayStation and so on.
- The page says "still here" every 20 seconds (/api/ping). About a minute after the last
  one (the window was closed) the app quits, unless a sync or refresh is still running:
  then it quits a minute after that finishes.
- A tray icon by the clock reopens the window or quits (needs the pystray package, which
  the Windows download includes; without it there's simply no icon).
- Shortcuts on the desktop and in the Start menu, when you ask for them.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from . import brand
from .paths import APP_DIR, DATA_DIR, ROOT_DIR

IDLE_QUIT_S = 90        # no word from any open page for this long: the window was closed
FIRST_WINDOW_S = 300    # how long to wait for the first page before giving up
ICON_ICO = APP_DIR / "static" / "icon.ico"
ICON_PNG = APP_DIR / "static" / "icon-256.png"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_last_ping = 0.0
_started = time.time()
_quit = threading.Event()
_tray = None
APP_MODE = False  # started with --app (set by __main__)
_shortcuts: dict | None = None


def is_windows() -> bool:
    return os.name == "nt"


def packaged() -> bool:
    """Running from the Windows download (its own Python in the runtime folder)."""
    return (ROOT_DIR / "runtime").is_dir() and Path(sys.executable).resolve().parent == (ROOT_DIR / "runtime").resolve()


def pythonw() -> str:
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return str(w if w.exists() else exe)


# ------------------------------------------------------------------ the window

def _browser() -> str | None:
    """Edge (comes with Windows) or Chrome, for an app-mode window."""
    if not is_windows():
        return None
    roots = [os.environ.get(k) for k in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
    rels = [r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe",
            r"BraveSoftware\Brave-Browser\Application\brave.exe"]
    for rel in rels:
        for root in roots:
            if root and (Path(root) / rel).exists():
                return str(Path(root) / rel)
    return None


def _find_window(every: bool = False):
    """The app window if it's open (every=True: all of them): a Chrome/Edge window whose title is exactly the app's name
    (a normal browser tab would add " - Microsoft Edge" and the like)."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def check(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if not n or n > 200:
            return True
        title = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, title, n + 1)
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        if title.value == brand.NAME and cls.value.startswith("Chrome_WidgetWin"):
            found.append(hwnd)
            return every
        return True

    user32.EnumWindows(check, 0)
    return found if every else (found[0] if found else None)


def focus_window() -> bool:
    """Bring the open app window to the front (restoring it if minimised). False if there's none."""
    if not is_windows():
        return False
    try:
        import ctypes
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        hwnd = _find_window()
        if not hwnd:
            return False
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        else:
            user32.ShowWindow(hwnd, 5)  # SW_SHOW
        if not user32.SetForegroundWindow(hwnd):
            # Windows only lets the app that got the last click bring a window forward; a tap
            # of Alt (which does nothing by itself) lifts that lock.
            user32.keybd_event(0x12, 0, 0, 0)
            user32.keybd_event(0x12, 0, 2, 0)
            user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def open_window(url: str) -> None:
    """Show the app window: the one already open if there is one, else a new one."""
    if focus_window():
        _set_window_icon_soon()  # a window left open from before (an update restarts the app)
        return
    exe = _browser()
    if not exe:
        webbrowser.open(url)
        return
    profile = DATA_DIR / "window"
    profile.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.Popen([
            exe, f"--app={url}", f"--user-data-dir={profile}", *_window_args(),
            "--no-first-run", "--no-default-browser-check", "--disable-sync",
            # dark window frame (title bar), whatever the Windows theme, to match the app
            "--force-dark-mode",
            # keep the "still here" pings going while the window is minimised or covered
            "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
            "--disable-backgrounding-occluded-windows",
        ], creationflags=NO_WINDOW, close_fds=True)
    except OSError:
        webbrowser.open(url)
        return
    _set_window_icon_soon()


_icon_keeper: threading.Thread | None = None


def _set_window_icon_soon() -> None:
    """Give the app window Sunbeam's own .ico (all its sizes), so the taskbar and Alt+Tab
    show a sharp icon: on its own the window only has the page's small favicon, scaled up.
    The browser puts that favicon back whenever the page's address changes (switching tabs
    does), so one thread keeps watching the window for as long as the app runs and sets ours
    again within half a second."""
    global _icon_keeper
    if not is_windows() or not ICON_ICO.exists() or (_icon_keeper and _icon_keeper.is_alive()):
        return

    def run():
        try:
            import ctypes
            user32 = ctypes.windll.user32  # type: ignore[attr-defined]
            user32.LoadImageW.restype = ctypes.c_void_p
            user32.SendMessageTimeoutW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p,
                                                   ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_size_t)]
            user32.IsWindow.argtypes = [ctypes.c_void_p]
            # larger than the 32 / 16 px Windows asks for at 100%, so scaled displays get a sharp one too
            big = user32.LoadImageW(None, str(ICON_ICO), 1, 64, 64, 0x10)    # IMAGE_ICON, LR_LOADFROMFILE
            small = user32.LoadImageW(None, str(ICON_ICO), 1, 32, 32, 0x10) or big
            if not big:
                return
            want = {1: big, 0: small}  # ICON_BIG, ICON_SMALL

            def send(hwnd, msg, wparam, lparam):
                # with a timeout: a busy or hung window must not stall this thread
                out = ctypes.c_size_t(0)
                ok = user32.SendMessageTimeoutW(hwnd, msg, wparam, lparam, 0x2, 300, ctypes.byref(out))  # SMTO_ABORTIFHUNG
                return out.value if ok else None

            windows: list = []
            last_look = 0.0
            while True:
                now = time.time()
                if not windows or now - last_look > 3:
                    windows = _find_window(every=True) or []
                    last_look = now
                for hwnd in windows:
                    if not user32.IsWindow(hwnd):
                        continue
                    for which, icon in want.items():
                        if send(hwnd, 0x007F, which, 0) != icon:              # WM_GETICON
                            send(hwnd, 0x0080, which, icon)                     # WM_SETICON
                time.sleep(0.5)
        except Exception:
            pass
    _icon_keeper = threading.Thread(target=run, daemon=True, name="window-icon")
    _icon_keeper.start()


def open_external(url: str) -> None:
    """A link to another site, in your normal browser. (os.startfile from the server's threads
    can quietly do nothing on Windows: those threads haven't set up COM, which the shell's
    URL handling needs. A separate rundll32 process has.)"""
    if is_windows():
        try:
            subprocess.Popen(["rundll32.exe", "url.dll,FileProtocolHandler", url], creationflags=NO_WINDOW, close_fds=True)
            return
        except OSError:
            pass
    webbrowser.open(url)


def _window_args() -> list[str]:
    """Where the window was last time (the page reports it to /api/window), else a roomy default."""
    from . import settings
    r = settings.get("window_rect")
    try:
        if r and r.get("max"):
            return ["--start-maximized"]
        if r:
            w, h, x, y = (int(r[k]) for k in ("w", "h", "x", "y"))
            if w >= 480 and h >= 360 and -100 < x < 20000 and -100 < y < 20000:
                return [f"--window-size={w},{h}", f"--window-position={x},{y}"]
    except (TypeError, ValueError, KeyError, AttributeError):
        pass
    return ["--window-size=1440,900"]


# ------------------------------------------------------------------ quitting by itself

def ping() -> None:
    global _last_ping
    _last_ping = time.time()


def watch_and_quit(httpd, busy) -> None:
    """Stop the server once no window has been open for a while (and nothing is running)."""
    def loop():
        while not _quit.wait(5):
            now = time.time()
            if busy():
                ping()  # a sync is running: wait for it, and count the minute from when it ends
                continue
            if _window_open():
                # The window is there, even if its page has gone quiet (Windows and the browser slow
                # down or freeze a minimised window's page, so its "still here" can stop for a while).
                ping()
                continue
            if _last_ping:
                if now - _last_ping > IDLE_QUIT_S:
                    break
            elif now - _started > FIRST_WINDOW_S:
                break
        quit_app(httpd)
    threading.Thread(target=loop, daemon=True, name="watchdog").start()


def _window_open() -> bool:
    if not is_windows() or not APP_MODE:
        return False
    try:
        return bool(_find_window())
    except Exception:
        return False


def quit_app(httpd) -> None:
    _quit.set()
    if _tray is not None:
        try:
            _tray.stop()
        except Exception:
            pass
    threading.Thread(target=httpd.shutdown, daemon=True).start()


# ------------------------------------------------------------------ tray icon

def run_tray(url: str, httpd) -> bool:
    """Show the tray icon (blocks until it's removed). False when pystray isn't available."""
    global _tray
    try:
        import pystray  # type: ignore
        from PIL import Image  # type: ignore
    except Exception:
        return False
    try:
        image = Image.open(ICON_PNG)
    except Exception:
        image = Image.new("RGBA", (64, 64), (255, 92, 158, 255))
    menu = pystray.Menu(
        pystray.MenuItem(f"Open {brand.NAME}", lambda: open_window(url), default=True),
        pystray.MenuItem(f"Quit {brand.NAME}", lambda: quit_app(httpd)),
    )
    _tray = pystray.Icon("gameshelf", image, brand.NAME, menu)
    try:
        _tray.run()
    except Exception:
        return False
    return True


# ------------------------------------------------------------------ shortcuts

def shortcut_paths() -> dict[str, str]:
    """Where the desktop and Start menu shortcuts go (OneDrive-moved desktops included)."""
    if not is_windows():
        return {}
    script = ("[Environment]::GetFolderPath('Desktop'); [Environment]::GetFolderPath('Programs')")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                             capture_output=True, text=True, timeout=20, creationflags=NO_WINDOW).stdout.split("\n")
        desktop, programs = out[0].strip(), out[1].strip()
    except Exception:
        home = Path(os.environ.get("USERPROFILE", "~")).expanduser()
        desktop = str(home / "Desktop")
        programs = str(Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs")
    return {"desktop": str(Path(desktop) / f"{_safe_name()}.lnk"), "startmenu": str(Path(programs) / f"{_safe_name()}.lnk")}


def _safe_name() -> str:
    """The app's name as a file name."""
    import re
    return re.sub(r'[<>:"/\\|?*]', "", brand.NAME).strip() or "App"


def rename_shortcuts() -> None:
    """After the app is renamed (brand.py), shortcuts made under the old name get the new one."""
    if not is_windows():
        return
    from . import settings
    made = settings.get("shortcuts_made") or []
    if not made or all(Path(p).stem == _safe_name() for p in made):
        return
    try:
        keys = [k for k, v in shortcut_paths().items() if any(Path(p).parent == Path(v).parent for p in made)]
        for p in made:
            if Path(p).stem != _safe_name() and Path(p).suffix.lower() == ".lnk":  # only our own shortcuts
                try:
                    Path(p).unlink()
                except OSError:
                    pass
        make_shortcuts(keys)
    except Exception as e:
        print(f"  Couldn't rename the shortcuts: {e}")


def shortcuts_state() -> dict:
    """{"desktop": bool, "startmenu": bool}: whether each shortcut exists (looked up once)."""
    global _shortcuts
    if not is_windows():
        return {}
    if _shortcuts is None:
        _shortcuts = shortcut_paths()
    return {k: Path(v).exists() for k, v in _shortcuts.items()}


def make_shortcuts(which: list[str]) -> list[str]:
    """Create the shortcuts asked for; returns where they went."""
    if not is_windows():
        raise RuntimeError("Shortcuts are only made on Windows.")
    paths = shortcut_paths()
    made = []
    for key in which:
        lnk = paths.get(key)
        if not lnk:
            continue
        ps = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:GS_LNK); "
              "$s.TargetPath = $env:GS_TARGET; $s.Arguments = '-m gameshelf --app'; "
              "$s.WorkingDirectory = $env:GS_ROOT; $s.IconLocation = $env:GS_ICON; "
              "$s.Description = $env:GS_DESC; $s.Save()")
        env = {**os.environ, "GS_LNK": lnk, "GS_TARGET": pythonw(), "GS_ROOT": str(ROOT_DIR), "GS_ICON": str(ICON_ICO),
               "GS_DESC": f"{brand.NAME}: {brand.TAGLINE.lower()}"}
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], env=env,
                           capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW)
        if r.returncode != 0 or not Path(lnk).exists():
            raise RuntimeError(f"Couldn't create the shortcut ({(r.stderr or '').strip()[:200]})")
        made.append(lnk)
    from . import settings
    known = [p for p in settings.get("shortcuts_made") or [] if Path(p).exists() and p not in made]
    settings.update({"shortcuts_made": known + made})
    global _shortcuts
    _shortcuts = None
    return made


def unblock_files() -> None:
    """Files unzipped from a download carry Windows' "came from the internet" mark, which makes
    Windows ask before running them. The app's own files don't need it: remove it once."""
    if not is_windows():
        return
    flag = DATA_DIR / ".unblocked"
    if flag.exists():
        return
    for base in (ROOT_DIR / "runtime", APP_DIR, ROOT_DIR):
        it = base.rglob("*") if base != ROOT_DIR else base.glob("*")
        for p in it:
            if p.is_file():
                try:
                    os.remove(f"{p}:Zone.Identifier")
                except OSError:
                    pass
    try:
        flag.write_text("1")
    except OSError:
        pass


def log_to_file() -> None:
    """Without a console window (pythonw), messages go to data/gameshelf.log instead."""
    log = DATA_DIR / "gameshelf.log"
    try:
        if log.exists() and log.stat().st_size > 1_000_000:  # keep it small: start over
            log.unlink()
        f = open(log, "a", encoding="utf-8", buffering=1)
    except OSError:
        return
    f.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
    sys.stdout = sys.stderr = f
