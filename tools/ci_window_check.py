"""Release build check (Windows): the packaged app opens its window, and opening it again
brings that window forward instead of starting a second one. Run with the bundled Python
from the build folder."""

import subprocess
import sys
import time
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
from gameshelf import desktop  # noqa: E402

app = subprocess.Popen([str(root / "runtime" / "python.exe"), "-m", "gameshelf", "--app", "--port", "8791"], cwd=root)
try:
    for _ in range(60):
        if desktop._find_window():
            break
        time.sleep(1)
    first = desktop._find_window(every=True)
    print("windows after start:", len(first))
    if len(first) != 1:
        sys.exit("the app window didn't open")
    # The title bar should take the page's dark theme colour (meta theme-color).
    time.sleep(6)
    import ctypes
    from ctypes import wintypes
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    hwnd = first[0]
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    user32.SetForegroundWindow(hwnd)
    time.sleep(1)
    dc = user32.GetDC(0)
    def at(x, y):
        px = gdi32.GetPixel(dc, x, y)
        return px & 0xFF, (px >> 8) & 0xFF, (px >> 16) & 0xFF
    bar = at(rect.left + 300, rect.top + 12)
    page = at(rect.left + 300, rect.top + 80)  # the app's own header, to show sampling works
    user32.ReleaseDC(0, dc)
    print(f"title bar {bar}, page header {page}, window {rect.left},{rect.top},{rect.right},{rect.bottom}")
    Path(root / "titlebar.txt").write_text(",".join(str(v) for v in bar))
    Path(root / "titlebar-info.txt").write_text(f"bar {bar} page {page} rect {rect.left},{rect.top},{rect.right},{rect.bottom}")
    desktop.open_window("http://localhost:8791/")  # what the tray icon does
    time.sleep(8)
    again = desktop._find_window(every=True)
    print("windows after opening again:", len(again))
    if len(again) != 1:
        sys.exit("opening again made another window")
finally:
    app.kill()
    subprocess.run(["taskkill", "/F", "/IM", "msedge.exe"], capture_output=True)
