"""Rename the app: python tools/rename.py "New Name" [--repo Owner/NewRepo]

Updates gameshelf/brand.py (the one place the app reads its name from) and the name in
README.md and RELEASE_NOTES.md. Everything else (window, page, setup, tray icon,
shortcuts, launcher, release download) follows brand.py by itself. Existing shortcuts
are renamed the next time the app starts.

If you rename the GitHub repository too, pass --repo so updates keep finding it
(GitHub also redirects the old address for a while).
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRAND = ROOT / "gameshelf" / "brand.py"
DOCS = [ROOT / "README.md", ROOT / "RELEASE_NOTES.md"]


def current(field: str) -> str:
    m = re.search(rf'^{field} = "([^"]*)"', BRAND.read_text("utf-8"), re.M)
    return m.group(1) if m else ""


def main():
    ap = argparse.ArgumentParser(description="Rename the app")
    ap.add_argument("name", help="the new name, e.g. Sunbeam")
    ap.add_argument("--repo", help="the GitHub repository, if it was renamed (Owner/Name)")
    ap.add_argument("--from", dest="old", help="the old name in the docs (default: the name in brand.py)")
    args = ap.parse_args()
    old_name, old_repo = args.old or current("NAME"), current("REPO")
    new_repo = args.repo or old_repo

    text = BRAND.read_text("utf-8")
    text = re.sub(r'^NAME = "[^"]*"', f'NAME = "{args.name}"', text, flags=re.M)
    text = re.sub(r'^REPO = "[^"]*"', f'REPO = "{new_repo}"', text, flags=re.M)
    BRAND.write_text(text, "utf-8")

    for doc in DOCS:
        if not doc.exists():
            continue
        t = doc.read_text("utf-8")
        if old_repo and new_repo != old_repo:
            t = t.replace(old_repo, new_repo)
        if old_name and old_name != args.name:
            t = re.sub(rf"\b{re.escape(old_name)}\b", args.name, t)
        doc.write_text(t, "utf-8")
    print(f"Renamed {old_name or '(unnamed)'} to {args.name}" + (f"; repository {new_repo}" if new_repo != old_repo else ""))


if __name__ == "__main__":
    main()
