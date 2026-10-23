"""CI (Windows): sign-ins are written to settings.json encrypted with DPAPI and read back intact."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
from gameshelf import settings  # noqa: E402

assert settings.available(), "Windows data protection (DPAPI) isn't available"
secret = "ci-check-secret-0123456789"
settings.update({"steam_api_key": secret, "steam_id": "76561190000000000", "hide_vr": True})
raw = settings.CONFIG_PATH.read_text("utf-8")
assert secret not in raw and "7656119" not in raw, "a secret was written in plain text"
assert '"steam_api_key": "dpapi:' in raw, raw
settings._plain_cache.clear()
assert settings.get("steam_api_key") == secret, "couldn't read the secret back"
assert settings.get("hide_vr") is True
assert settings.unreadable() == []
settings.update({"steam_api_key": "", "steam_id": "", "hide_vr": None})
print("settings encrypted ok")
