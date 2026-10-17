"""Where GameShelf keeps its data: a `data` folder next to the app, so the whole
thing stays portable (copy the folder and your library comes with it)."""

from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent
STATIC_DIR = APP_DIR / "static"
DATA_DIR = ROOT_DIR / "data"
DB_PATH = DATA_DIR / "library.db"
CONFIG_PATH = DATA_DIR / "settings.json"

DATA_DIR.mkdir(parents=True, exist_ok=True)
