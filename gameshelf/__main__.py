"""Start the app: `python -m gameshelf` (the Windows launcher and shortcuts add --app)."""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
import webbrowser

from . import __version__, brand, db, desktop
from .server import serve

DEFAULT_PORT = 8765


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def main():
    parser = argparse.ArgumentParser(prog="gameshelf")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true", help="don't open a window or browser tab")
    parser.add_argument("--app", action="store_true",
                        help="desktop mode: own window, tray icon, quits a minute after the window closes")
    parser.add_argument("--wait-port", action="store_true", help="wait for the previous copy to stop (after an update)")
    args = parser.parse_args()
    app = args.app
    desktop.APP_MODE = app
    if app and (sys.stdout is None or desktop.packaged()):
        desktop.log_to_file()
    if app:
        desktop.unblock_files()
        desktop.rename_shortcuts()
        desktop.remove_phone_snapshot()

    url = f"http://localhost:{args.port}/"
    if args.wait_port:
        for _ in range(100):
            if not _port_in_use(args.port):
                break
            time.sleep(0.2)
    if _port_in_use(args.port):
        # Already running (e.g. opened twice): just show the window.
        print(f"{brand.NAME} is already running at {url}")
        if not args.no_browser:
            desktop.open_window(url) if app else webbrowser.open(url)
        return

    db.query("SELECT 1")  # create the database on first run
    db.ensure_baseline()  # start the yearly-playtime record
    try:
        from . import sync
        undone = sync.recheck_auto_completions()
        if undone:
            print(f"  Unmarked {undone} game(s) that had been marked completed by mistake.")
    except Exception as e:  # never stop the app from starting
        print(f"  Couldn't re-check finished games: {e}")
    try:
        from .meta import early_access
        early_access.refresh_soon()
    except Exception as e:
        print(f"  Couldn't schedule the early access check: {e}")
    httpd = serve(args.port)
    print(f"\n  {brand.NAME} {__version__} is running at {url}")

    if not app:
        print("  Keep this window open while you use it. Close it to quit.\n")
        if not args.no_browser:
            threading.Timer(0.6, lambda: webbrowser.open(url)).start()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            httpd.server_close()
            sys.exit(0)

    # Desktop mode: the server runs in the background; the tray icon (if any) holds the main thread.
    from . import sync
    server = threading.Thread(target=httpd.serve_forever, daemon=True, name="server")
    server.start()
    if not args.no_browser:
        threading.Timer(0.4, lambda: desktop.open_window(url)).start()
    desktop.watch_and_quit(httpd, lambda: bool(sync.status().get("running")))
    if not desktop.run_tray(url, httpd):
        server.join()
    server.join(timeout=10)
    httpd.server_close()
    print(f"  {brand.NAME} stopped.")


if __name__ == "__main__":
    main()
