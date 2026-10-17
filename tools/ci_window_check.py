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
    desktop.open_window("http://localhost:8791/")  # what the tray icon does
    time.sleep(8)
    again = desktop._find_window(every=True)
    print("windows after opening again:", len(again))
    if len(again) != 1:
        sys.exit("opening again made another window")
finally:
    app.kill()
    subprocess.run(["taskkill", "/F", "/IM", "msedge.exe"], capture_output=True)
