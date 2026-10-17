"""Library importers. Each module exposes `fetch() -> list[dict]` and `test() -> str`.

Each dict: platform, external_id, name, playtime_min, last_played, first_played,
ownership, plus optional steam_appid / psn_image.
"""

PLATFORMS = {
    "steam": "Steam",
    "playstation": "PlayStation",
    "switch2": "Switch 2",
    "switch": "Switch",
    "xbox": "Xbox",
    "epic": "Epic",
    "battlenet": "Battle.net",
    "ubisoft": "Ubisoft Connect",
    "gog": "GOG",
    "other": "Other",
}
