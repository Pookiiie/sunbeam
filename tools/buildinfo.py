"""For the release build: prints version=, name= and repo= lines (from gameshelf/__init__.py and
gameshelf/brand.py), and stops the build if a version tag doesn't match the code's version."""

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
version = re.search(r'__version__ = "(.+?)"', (ROOT / "gameshelf" / "__init__.py").read_text()).group(1)
brand = (ROOT / "gameshelf" / "brand.py").read_text()
name = re.search(r'^NAME = "(.+?)"', brand, re.M).group(1)
repo = re.search(r'^REPO = "(.+?)"', brand, re.M).group(1)
if os.environ.get("GITHUB_REF_TYPE") == "tag" and os.environ.get("GITHUB_REF_NAME") != f"v{version}":
    sys.exit(f"Tag {os.environ.get('GITHUB_REF_NAME')} doesn't match the code's version ({version})")
prerelease = "true" if re.search(r"-beta\.\d+$", version) else "false"
if not re.fullmatch(r"\d+\.\d+\.\d+(-beta\.\d+)?", version):
    sys.exit(f"Version {version} should look like 1.3.0 or 1.3.0-beta.1")
print(f"version={version}\nname={name}\nrepo={repo}\nprerelease={prerelease}")
