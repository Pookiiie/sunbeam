"""SQLite storage.

Two tables:
  games    one row per *game* (merged across platforms), holds metadata, art and
           your personal status (completed / DNF), notes.
  entries  one row per *copy on a platform* (your Steam copy, your PS5 copy, a
           manually added Switch 2 copy), holds playtime and last played.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import unicodedata
from contextlib import contextmanager

from .paths import DATA_DIR, DB_PATH

BACKUP_DIR = DATA_DIR / "backups"
BACKUPS_KEPT = 6
BACKUP_EVERY = 7 * 86400

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id              INTEGER PRIMARY KEY,
    title           TEXT NOT NULL,
    norm_title      TEXT NOT NULL,
    igdb_id         INTEGER UNIQUE,
    match_state     TEXT NOT NULL DEFAULT 'pending',  -- pending | auto | manual | none
    steam_appid     INTEGER,
    psn_image       TEXT,
    summary         TEXT,
    storyline       TEXT,
    genres          TEXT,   -- JSON arrays
    themes          TEXT,
    modes           TEXT,
    perspectives    TEXT,
    developers      TEXT,
    publishers      TEXT,
    franchises      TEXT,
    igdb_platforms  TEXT,
    screenshots     TEXT,
    release_date    INTEGER,
    critic_score    REAL,
    critic_count    INTEGER,
    user_score      REAL,
    user_count      INTEGER,
    igdb_url        TEXT,
    cover_url       TEXT,
    hero_url        TEXT,
    logo_url        TEXT,
    igdb_cover      TEXT,
    igdb_artwork    TEXT,
    ttb_main        REAL,   -- hours
    ttb_extra       REAL,
    ttb_complete    REAL,
    ttb_source      TEXT,
    steam_review_pct    REAL,
    steam_review_count  INTEGER,
    steam_review_desc   TEXT,
    status          TEXT NOT NULL DEFAULT '',          -- '' | completed | dnf
    status_at       INTEGER,
    notes           TEXT NOT NULL DEFAULT '',
    added_at        INTEGER NOT NULL,
    meta_at         INTEGER,
    art_at          INTEGER,
    reviews_at      INTEGER,
    ttb_at          INTEGER
);

CREATE TABLE IF NOT EXISTS entries (
    id              INTEGER PRIMARY KEY,
    game_id         INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    platform        TEXT NOT NULL,   -- steam | playstation | switch2 | switch | xbox | epic | ubisoft | gog | other
    external_id     TEXT NOT NULL,
    name            TEXT NOT NULL,
    playtime_min    INTEGER NOT NULL DEFAULT 0,
    last_played     INTEGER,
    first_played    INTEGER,
    ownership       TEXT,            -- e.g. ps_plus, game_pass
    manual          INTEGER NOT NULL DEFAULT 0,
    updated_at      INTEGER NOT NULL,
    UNIQUE(platform, external_id)
);

CREATE INDEX IF NOT EXISTS idx_entries_game ON entries(game_id);
CREATE INDEX IF NOT EXISTS idx_games_norm ON games(norm_title);
CREATE INDEX IF NOT EXISTS idx_games_steam ON games(steam_appid);
"""

JSON_FIELDS = (
    "genres", "themes", "modes", "perspectives", "developers", "publishers",
    "franchises", "igdb_platforms", "screenshots",
)

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        _auto_backup(_conn)
        _conn.executescript(SCHEMA)
        _migrate(_conn)
    return _conn


def backup(c: sqlite3.Connection | None = None, version: str | None = None) -> str | None:
    """Copy the library to data/backups (SQLite's own backup, so it's consistent even while
    the app is running) and keep the newest few. Returns the file name."""
    from . import __version__
    c = c or _connect()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H%M")
    # named after the version of Sunbeam the library was last used with
    path = BACKUP_DIR / f"library {stamp} ({version or __version__}).db"
    with _lock:
        dest = sqlite3.connect(path)
        try:
            c.backup(dest)
        finally:
            dest.close()
        c.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('backup_at', ?)", (str(int(time.time())),))
        c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('backup_app', ?)", (__version__,))
    olds = sorted(BACKUP_DIR.glob("library *.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in olds[BACKUPS_KEPT:]:
        old.unlink(missing_ok=True)
    return path.name


def _auto_backup(c: sqlite3.Connection) -> None:
    """At start-up: a copy whenever this is a new version of the app (before its database
    changes run) and otherwise once a week. An empty or brand-new library isn't copied."""
    from . import __version__
    import logging
    try:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='games'").fetchone():
            return
        if not c.execute("SELECT 1 FROM games LIMIT 1").fetchone():
            return
        has_meta = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone()
        meta = {r[0]: r[1] for r in c.execute("SELECT key, value FROM meta WHERE key IN ('backup_at', 'backup_app')")} if has_meta else {}
        if meta.get("backup_app") != __version__:
            backup(c, meta.get("backup_app"))
        elif time.time() - int(meta.get("backup_at") or 0) > BACKUP_EVERY:
            backup(c)
    except Exception as e:  # a backup problem never stops the app
        logging.getLogger(__name__).warning("Couldn't back up the library: %s", e)


def _migrate(c: sqlite3.Connection) -> None:
    c.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    row = c.execute("SELECT value FROM meta WHERE key='version'").fetchone()
    version = int(row["value"]) if row else 1
    if version < 2:
        # Steam art saved from old fixed addresses can be a grey placeholder for
        # newer games: mark it for re-collection (Refresh assets › fill in missing).
        c.execute(
            "UPDATE games SET art_at=NULL, cover_url=NULL, hero_url=NULL, logo_url=NULL "
            "WHERE cover_url LIKE '%steamstatic.com/steam/apps/%' "
            "OR hero_url LIKE '%steamstatic.com/steam/apps/%' OR logo_url LIKE '%steamstatic.com/steam/apps/%'"
        )
        c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('version', '2')")
    if version < 3:
        cols = {r[1] for r in c.execute("PRAGMA table_info(games)")}
        if "endless" not in cols:
            # NULL = decide automatically, 1 = no ending (no progress bar), 0 = has an ending
            c.execute("ALTER TABLE games ADD COLUMN endless INTEGER")
        if "steam_checked_at" not in cols:
            # when we last searched Steam for a version of a game we only know from another store
            c.execute("ALTER TABLE games ADD COLUMN steam_checked_at INTEGER")
        c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('version', '3')")
    if version < 4:
        cols = {r[1] for r in c.execute("PRAGMA table_info(games)")}
        for col, kind in (
            ("art_locks", "TEXT"),          # comma list of art slots you picked by hand: cover,hero,logo
            ("oc_id", "INTEGER"),           # OpenCritic
            ("oc_score", "REAL"),           # Top Critic Average (0-100)
            ("oc_recommend", "REAL"),       # % of critics who recommend it
            ("oc_tier", "TEXT"),            # Mighty / Strong / Fair / Weak
            ("oc_count", "INTEGER"),        # number of reviews
            ("oc_url", "TEXT"),
            ("oc_at", "INTEGER"),           # when scores were fetched
            ("oc_checked_at", "INTEGER"),   # when we last looked for this game on OpenCritic
        ):
            if col not in cols:
                c.execute(f"ALTER TABLE games ADD COLUMN {col} {kind}")
        # Lifetime playtime recorded per day, so "hours played this year" can be worked out.
        c.execute("""CREATE TABLE IF NOT EXISTS playtime_log (
            entry_id INTEGER NOT NULL, day TEXT NOT NULL, playtime_min INTEGER NOT NULL,
            PRIMARY KEY (entry_id, day))""")
        c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('version', '4')")
    if version < 5:
        cols = {r[1] for r in c.execute("PRAGMA table_info(games)")}
        if "hidden" not in cols:
            # NULL = decide automatically (playtests, betas, demos...), 1 = hidden, 0 = always shown
            c.execute("ALTER TABLE games ADD COLUMN hidden INTEGER")
        # Steam's covers now come first: re-collect art for games that got SteamGridDB covers
        # despite having a Steam version (art you picked by hand is kept).
        c.execute(
            "UPDATE games SET art_at=NULL WHERE steam_appid IS NOT NULL AND cover_url LIKE '%steamgriddb%' "
            "AND COALESCE(art_locks, '') NOT LIKE '%cover%'"
        )
        c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('version', '5')")
    if version < 6:
        cols = {r[1] for r in c.execute("PRAGMA table_info(entries)")}
        if "adjust_min" not in cols:
            # minutes of this copy's store playtime that aren't yours (someone else on your account)
            c.execute("ALTER TABLE entries ADD COLUMN adjust_min INTEGER NOT NULL DEFAULT 0")
        if "excluded" not in cols:
            # 1 = this copy doesn't count towards the game's playtime or last played
            c.execute("ALTER TABLE entries ADD COLUMN excluded INTEGER NOT NULL DEFAULT 0")
        c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('version', '6')")
    if version < 7:
        cols = {r[1] for r in c.execute("PRAGMA table_info(games)")}
        for col, kind in (
            ("ttb_custom", "REAL"),             # hours you set with the time-to-beat slider
            ("status_manual", "INTEGER"),       # 1 = you set the status yourself: detection leaves it alone
            ("status_auto", "TEXT"),            # why the app marked it completed (achievement/trophy)
            ("completion_checked_at", "INTEGER"),
        ):
            if col not in cols:
                c.execute(f"ALTER TABLE games ADD COLUMN {col} {kind}")
        # Statuses set before detection existed were set by hand.
        c.execute("UPDATE games SET status_manual=1 WHERE status <> ''")
        # Steam's "played before tracking began" placeholder (Jan 2, 1970) isn't a real date.
        c.execute("UPDATE entries SET last_played=NULL WHERE last_played IS NOT NULL AND last_played < 1072915200")
        c.execute("UPDATE entries SET first_played=NULL WHERE first_played IS NOT NULL AND first_played < 1072915200")
        # Re-collect art: wrong SteamGridDB matches and landscape Steam covers get replaced
        # (art you picked by hand is kept).
        c.execute("UPDATE games SET art_at=NULL WHERE COALESCE(art_locks, '') NOT LIKE '%cover%'")
        c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('version', '7')")
    if version < 8:
        # Day-by-day play from Nintendo's Play Activity (other stores only give totals).
        c.execute("""CREATE TABLE IF NOT EXISTS play_days (
            entry_id INTEGER NOT NULL, day TEXT NOT NULL, minutes INTEGER NOT NULL,
            PRIMARY KEY (entry_id, day))""")
        cols = {r[1] for r in c.execute("PRAGMA table_info(entries)")}
        if "days_played" not in cols:
            c.execute("ALTER TABLE entries ADD COLUMN days_played INTEGER")
        c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('version', '8')")
    if version < 9:
        # Time-to-beat lookups got smarter (spellings, store names, title without subtitle):
        # give every game that found nothing another go.
        c.execute("UPDATE games SET ttb_at=NULL WHERE ttb_main IS NULL AND ttb_extra IS NULL AND ttb_complete IS NULL")
        c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('version', '9')")
    if version < 10:
        # What's on PC Game Pass and PS Plus (see meta/catalogs.py).
        c.execute("""CREATE TABLE IF NOT EXISTS sub_catalog (
            service TEXT NOT NULL, item_id TEXT NOT NULL, title TEXT, norm_title TEXT, image TEXT,
            xbox_title_id TEXT, concept_id TEXT, url TEXT, leaving INTEGER NOT NULL DEFAULT 0,
            first_seen INTEGER, last_seen INTEGER, game_id INTEGER,
            PRIMARY KEY (service, item_id))""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_sub_catalog_game ON sub_catalog(game_id)")
        c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('version', '10')")
    if version < 11:
        # The PS+ list used to be read from the Game Trials list by mistake: start it over
        # (so the real catalog doesn't all show up as "recently added").
        c.execute("DELETE FROM sub_catalog WHERE service='psplus'")
        c.execute("DELETE FROM meta WHERE key='catalog_baseline_psplus'")
        c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('version', '11')")
    if version < 12:
        # Matching keys now ignore "PC" / "Windows" tags: recompute them, and tidy names
        # that still carry one ("Golf With Your Friends (Windows Version)").
        for r in c.execute("SELECT id, title FROM games").fetchall():
            c.execute("UPDATE games SET norm_title=?, title=? WHERE id=?",
                      (normalize_title(r["title"]), clean_display_title(r["title"]), r["id"]))
        c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('version', '12')")
    if version < 13:
        cols = {r[1] for r in c.execute("PRAGMA table_info(games)")}
        if "interest" not in cols:
            # For Curator: 'more', 'less', 'skip' (not right now) or NULL.
            c.execute("ALTER TABLE games ADD COLUMN interest TEXT")
        c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('version', '13')")
    if version < 14:
        # Steam's stand-in covers (portrait.png: the banner over a smeared fill) get replaced
        # by SteamGridDB, IGDB or PlayStation art at the next artwork pass. Picked art stays.
        c.execute("UPDATE games SET art_at=NULL WHERE cover_url LIKE '%/portrait.png%' "
                  "AND COALESCE(art_locks, '') NOT LIKE '%cover%'")
        c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('version', '14')")
    if version < 15:
        cols = {r[1] for r in c.execute("PRAGMA table_info(games)")}
        if "hltb_id" not in cols:
            # HowLongToBeat's own id, for a link to the game's page there.
            c.execute("ALTER TABLE games ADD COLUMN hltb_id INTEGER")
        c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('version', '15')")
    if version < 16:
        # "Watch Dogs2" and "Watch Dogs 2" now share a matching key.
        for r in c.execute("SELECT id, title FROM games").fetchall():
            c.execute("UPDATE games SET norm_title=? WHERE id=?", (normalize_title(r["title"]), r["id"]))
        c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('version', '16')")
    if version < 17:
        # Your Steam and PlayStation wishlists (see meta/wishlist.py).
        from .meta import wishlist
        wishlist._table(c)
        c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('version', '17')")
    if version < 18:
        # Early access (meta/early_access.py): still being made, and the announced full release.
        cols = {r[1] for r in c.execute("PRAGMA table_info(games)")}
        for col in ("early_access", "ea_full_date", "ea_checked_at"):
            if col not in cols:
                c.execute(f"ALTER TABLE games ADD COLUMN {col} INTEGER")
        from .meta import wishlist
        wishlist._table(c)
        wcols = {r[1] for r in c.execute("PRAGMA table_info(wishlist)")}
        for col in ("early_access", "ea_full_ts"):
            if col not in wcols:
                c.execute(f"ALTER TABLE wishlist ADD COLUMN {col} INTEGER")
        c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('version', '18')")
    if version < 19:
        # Steam's "VR Only" store category (meta/early_access.py reads it with the early access flag).
        cols = {r[1] for r in c.execute("PRAGMA table_info(games)")}
        if "vr_only" not in cols:
            c.execute("ALTER TABLE games ADD COLUMN vr_only INTEGER")
        c.execute("UPDATE games SET ea_checked_at = NULL")  # so the next check fills it in
        c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('version', '19')")
    if version < 20:
        # Installed games (installed.py), your own shelves, and IGDB's similar games.
        c.execute("""CREATE TABLE IF NOT EXISTS installs (
            game_id INTEGER NOT NULL, platform TEXT NOT NULL, ref TEXT NOT NULL, name TEXT,
            launch TEXT, dir TEXT, seen_at INTEGER, PRIMARY KEY (platform, ref))""")
        c.execute("CREATE INDEX IF NOT EXISTS installs_game ON installs(game_id)")
        c.execute("""CREATE TABLE IF NOT EXISTS shelves (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, position INTEGER NOT NULL DEFAULT 0, created_at INTEGER)""")
        c.execute("""CREATE TABLE IF NOT EXISTS shelf_games (
            shelf_id INTEGER NOT NULL, game_id INTEGER NOT NULL, added_at INTEGER,
            PRIMARY KEY (shelf_id, game_id))""")
        c.execute("CREATE INDEX IF NOT EXISTS shelf_games_game ON shelf_games(game_id)")
        cols = {r[1] for r in c.execute("PRAGMA table_info(games)")}
        if "similar" not in cols:
            c.execute("ALTER TABLE games ADD COLUMN similar TEXT")      # IGDB ids of similar games (JSON)
        if "similar_at" not in cols:
            c.execute("ALTER TABLE games ADD COLUMN similar_at INTEGER")
        c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('version', '20')")
    if version < 21:
        # An XGP / PS+ list entry you matched (or combined) with a game you own: remembered, so the
        # next sync doesn't bring it back as a game of its own when its name doesn't match yours
        # ("Tell Me Why: Chapters 1-3" on XGP is your Tell Me Why on Steam).
        c.execute("""CREATE TABLE IF NOT EXISTS catalog_links (
            service TEXT NOT NULL, item_id TEXT NOT NULL, game_id INTEGER NOT NULL, PRIMARY KEY (service, item_id))""")
        c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('version', '21')")
    if version < 22:
        # Wishlist › Sort by › My order: your own ranking (Sunbeam can't change the stores' lists).
        c.execute("CREATE TABLE IF NOT EXISTS wish_order (game_id INTEGER PRIMARY KEY, pos INTEGER NOT NULL)")
        c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('version', '22')")


@contextmanager
def tx():
    """Serialized transaction: `with db.tx() as c: c.execute(...)`."""
    with _lock:
        c = _connect()
        c.execute("BEGIN")
        try:
            yield c
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise


def query(sql: str, args=()) -> list[sqlite3.Row]:
    with _lock:
        return _connect().execute(sql, args).fetchall()


def now() -> int:
    return int(time.time())


# ---------------------------------------------------------------- titles

_EDITION_WORDS = (
    r"(digital\s+)?(deluxe|ultimate|complete|definitive|standard|gold|premium|special|"
    r"collector'?s|anniversary|enhanced|remastered|game of the year|goty|director'?s cut|"
    r"legendary|royal|champions?|launch|cross-gen|ps4\s*(&|and)\s*ps5)\s*(edition|bundle|version)?"
)


# PC store names carry platform tags that aren't part of the game's name:
# "Dead Island 2 (Windows)", "Diablo IV PC", "Cities: Skylines - Windows 10 Edition",
# "Minecraft Dungeons for Windows + Launcher", "Golf With Your Friends (Windows Version)".
_PC_PAREN = re.compile(
    r"\s*\((?:for\s+)?(?:pc|windows(?:\s*1[01])?|win\s*1[01]|microsoft\s+store|xbox\s+pc|pc\s+game\s+pass)"
    r"(?:\s+(?:version|edition))?\)", re.I)
_PC_TAIL = re.compile(
    r"\s*(?:[-–—:]\s*)?(?:for\s+)?(?:pc|windows(?:\s*1[01])?|win\s*1[01])(?:\s+(?:version|edition))?"
    r"(?:\s*\+\s*launcher)?\s*$", re.I)


def strip_pc_tags(title: str) -> str:
    """Drop "PC" / "Windows" tags from a store title (repeatedly: "Game (PC) - Windows")."""
    t = _PC_PAREN.sub("", title or "")
    for _ in range(3):
        t2 = _PC_TAIL.sub("", t)
        if t2 == t:
            break
        t = t2
    return t.strip() or (title or "").strip()


def normalize_title(title: str) -> str:
    """Loose key used to recognise the same game across stores."""
    t = unicodedata.normalize("NFKD", title or "").encode("ascii", "ignore").decode()
    t = t.lower()
    t = re.sub(r"[™®©]", "", t)
    t = strip_pc_tags(t)
    t = re.sub(r"\s*\(game preview\)", " ", t)
    t = re.sub(r"\((ps4|ps5|playstation\s*\d|for nintendo switch.*?|nintendo switch.*?)\)", " ", t)
    t = re.sub(r"\b(ps4|ps5)\s*(&|and)\s*(ps4|ps5)\b", " ", t)
    t = re.sub(r"\s+(for\s+)?(ps4|ps5|playstation\s*[45])\s*$", " ", t)
    t = re.sub(r"[\-:–—]\s*" + _EDITION_WORDS + r"\s*$", " ", t)
    t = re.sub(r"\s" + _EDITION_WORDS + r"\s*$", " ", t)
    t = t.replace("&", " and ")
    t = re.sub(r"([a-z])(\d)", r"\1 \2", t)  # "Watch Dogs2" = "Watch Dogs 2"
    t = re.sub(r"[^a-z0-9]+", " ", t)
    t = re.sub(r"\bthe\b", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def clean_display_title(title: str) -> str:
    t = strip_pc_tags(re.sub(r"[™®©]", "", title or "").strip())
    t = re.sub(r"\s*\((PS4|PS5)\)\s*$", "", t, flags=re.I)
    t = re.sub(r"\s+(PS4|PS5)\s*(&|and)\s*(PS4|PS5)\s*$", "", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip()


# ---------------------------------------------------------------- entries / games

# Copies that aren't yours: "catalog" (playable through XGP / PS+) and "wishlist" (on a
# store wishlist). OWNED picks your own copies; NOT_WISHLIST anything in your library.
OWNED = "COALESCE(ownership, '') NOT IN ('catalog', 'wishlist')"
NOT_WISHLIST = "COALESCE(ownership, '') <> 'wishlist'"

def upsert_entry(
    c: sqlite3.Connection,
    *,
    platform: str,
    external_id: str,
    name: str,
    playtime_min: int = 0,
    last_played: int | None = None,
    first_played: int | None = None,
    ownership: str | None = None,
    manual: bool = False,
    steam_appid: int | None = None,
    psn_image: str | None = None,
) -> int:
    """Insert or update a platform copy and attach it to a (possibly merged) game.
    Returns the game id."""
    ts = now()
    row = c.execute(
        "SELECT id, game_id FROM entries WHERE platform=? AND external_id=?", (platform, external_id)
    ).fetchone()
    if row:
        c.execute(
            """UPDATE entries SET name=?, playtime_min=?, last_played=?, first_played=COALESCE(?, first_played),
               ownership=?, updated_at=? WHERE id=?""",
            (name, playtime_min, last_played, first_played, ownership, ts, row["id"]),
        )
        game_id = row["game_id"]
        if psn_image:
            c.execute("UPDATE games SET psn_image=COALESCE(psn_image, ?) WHERE id=?", (psn_image, game_id))
        return game_id

    game_id = None
    if steam_appid:
        g = c.execute("SELECT id FROM games WHERE steam_appid=?", (steam_appid,)).fetchone()
        game_id = g["id"] if g else None
    norm = normalize_title(name)
    if game_id is None and norm:
        g = c.execute("SELECT id FROM games WHERE norm_title=? ORDER BY id LIMIT 1", (norm,)).fetchone()
        game_id = g["id"] if g else None
    if game_id is None:
        cur = c.execute(
            "INSERT INTO games(title, norm_title, steam_appid, psn_image, added_at) VALUES (?,?,?,?,?)",
            (clean_display_title(name), norm, steam_appid, psn_image, ts),
        )
        game_id = cur.lastrowid
    else:
        if steam_appid:
            c.execute("UPDATE games SET steam_appid=COALESCE(steam_appid, ?) WHERE id=?", (steam_appid, game_id))
        if psn_image:
            c.execute("UPDATE games SET psn_image=COALESCE(psn_image, ?) WHERE id=?", (psn_image, game_id))
    c.execute(
        """INSERT INTO entries(game_id, platform, external_id, name, playtime_min, last_played, first_played,
           ownership, manual, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (game_id, platform, external_id, name, playtime_min, last_played, first_played, ownership, int(manual), ts),
    )
    return game_id


def merge_games(c: sqlite3.Connection, keep_id: int, drop_id: int) -> None:
    """Fold `drop_id` into `keep_id` (same game found on two stores)."""
    if keep_id == drop_id:
        return
    keep = c.execute("SELECT * FROM games WHERE id=?", (keep_id,)).fetchone()
    drop = c.execute("SELECT * FROM games WHERE id=?", (drop_id,)).fetchone()
    if not keep or not drop:
        return
    # XGP / PS+ list entries folded into this game stay with it at the next sync (catalogs.link).
    for (ext,) in c.execute("SELECT external_id FROM entries WHERE game_id IN (?, ?) AND ownership='catalog'", (keep_id, drop_id)).fetchall():
        for svc, pre in (("gamepass", "xgp:"), ("psplus", "psplus:")):
            if str(ext).startswith(pre):
                c.execute("INSERT OR REPLACE INTO catalog_links(service, item_id, game_id) VALUES (?, ?, ?)", (svc, str(ext)[len(pre):], keep_id))
    c.execute("UPDATE catalog_links SET game_id=? WHERE game_id=?", (keep_id, drop_id))
    if not c.execute("SELECT 1 FROM wish_order WHERE game_id=?", (keep_id,)).fetchone():
        c.execute("UPDATE wish_order SET game_id=? WHERE game_id=?", (keep_id, drop_id))  # its place in your wishlist order
    c.execute("DELETE FROM wish_order WHERE game_id=?", (drop_id,))
    c.execute("UPDATE entries SET game_id=? WHERE game_id=?", (keep_id, drop_id))
    c.execute("UPDATE sub_catalog SET game_id=? WHERE game_id=?", (keep_id, drop_id))
    c.execute("UPDATE wishlist SET game_id=? WHERE game_id=?", (keep_id, drop_id))
    c.execute("UPDATE installs SET game_id=? WHERE game_id=?", (keep_id, drop_id))
    c.execute("INSERT OR IGNORE INTO shelf_games(shelf_id, game_id, added_at) SELECT shelf_id, ?, added_at FROM shelf_games WHERE game_id=?",
              (keep_id, drop_id))
    c.execute("DELETE FROM shelf_games WHERE game_id=?", (drop_id,))
    # Keep whichever personal data exists: the kept game's first, else the other one's.
    src = keep if keep["status"] or not drop["status"] else drop  # the status travels with its flags
    notes = "\n\n".join(n for n in (keep["notes"], drop["notes"]) if n)
    # Art picked by hand: each slot (cover, hero, logo) keeps whichever game had it locked, with its picture.
    locks = {x for x in (keep["art_locks"] or "").split(",") if x}
    art = {slot: keep[f"{slot}_url"] for slot in ("cover", "hero", "logo")}
    for slot in (x for x in (drop["art_locks"] or "").split(",") if x in art and x not in locks):
        locks.add(slot)
        art[slot] = drop[f"{slot}_url"]
    c.execute(
        """UPDATE games SET status=?, status_at=?, status_manual=?, status_auto=?, notes=?,
           steam_appid=COALESCE(steam_appid, ?), psn_image=COALESCE(psn_image, ?), added_at=MIN(added_at, ?),
           interest=COALESCE(interest, ?), hidden=COALESCE(hidden, ?), endless=COALESCE(endless, ?),
           ttb_custom=COALESCE(ttb_custom, ?), art_locks=?, cover_url=?, hero_url=?, logo_url=? WHERE id=?""",
        (src["status"], src["status_at"], src["status_manual"], src["status_auto"], notes,
         drop["steam_appid"], drop["psn_image"], drop["added_at"],
         drop["interest"], drop["hidden"], drop["endless"], drop["ttb_custom"],
         ",".join(sorted(locks)) or None, art["cover"], art["hero"], art["logo"], keep_id),
    )
    c.execute("DELETE FROM games WHERE id=?", (drop_id,))


def delete_orphans(c: sqlite3.Connection) -> None:
    c.execute("DELETE FROM games WHERE id NOT IN (SELECT DISTINCT game_id FROM entries)")
    # (game ids can be reused, so nothing may point at a game that's gone)
    c.execute("DELETE FROM shelf_games WHERE game_id NOT IN (SELECT id FROM games)")
    c.execute("DELETE FROM installs WHERE game_id NOT IN (SELECT id FROM games)")
    c.execute("DELETE FROM wish_order WHERE game_id NOT IN (SELECT id FROM games)")


# ---------------------------------------------------------------- reads

LIBRARY_SQL = """
SELECT g.*,
       COALESCE(SUM(CASE WHEN e.excluded THEN 0
                    ELSE MAX(e.playtime_min - e.adjust_min, 0) END), 0) AS playtime_min,
       MAX(CASE WHEN e.excluded THEN NULL ELSE e.last_played END)    AS last_played,
       MIN(CASE WHEN e.excluded THEN NULL ELSE e.first_played END)   AS first_played,
       GROUP_CONCAT(DISTINCT CASE WHEN e.ownership = 'wishlist' THEN NULL ELSE e.platform END) AS platforms,
       MAX(e.manual)                           AS has_manual,
       GROUP_CONCAT(DISTINCT e.ownership)      AS ownerships,
       MIN(CASE WHEN e.ownership IN ('catalog', 'wishlist') THEN 1 ELSE 0 END) AS catalog_only,
       MIN(CASE WHEN e.ownership = 'wishlist' THEN 1 ELSE 0 END) AS wishlist_only
FROM games g JOIN entries e ON e.game_id = g.id
{where}
GROUP BY g.id
"""


def game_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for f in JSON_FIELDS:
        if f in d:
            try:
                d[f] = json.loads(d[f]) if d[f] else []
            except (TypeError, json.JSONDecodeError):
                d[f] = []
    if "platforms" in d:
        d["platforms"] = sorted(set((d["platforms"] or "").split(","))) if d["platforms"] else []
    if "ownerships" in d:
        d["ownerships"] = [o for o in (d["ownerships"] or "").split(",") if o]
    if "catalog_only" in d:
        d["catalog_only"] = bool(d["catalog_only"]) and not d.get("wishlist_only")
    if "wishlist_only" in d:
        d["wishlist_only"] = bool(d["wishlist_only"])
    d.pop("similar", None)  # only the game page needs it, worked out in game_detail
    if "playtime_min" in d and "modes" in d:
        from .rules import open_ended
        d["endless_auto"] = open_ended(d)
    return d


def library(with_wishlist: bool = False) -> list[dict]:
    """Your library (owned games and XGP / PS+ games); with_wishlist adds games that are
    only on a wishlist (wishlist_only)."""
    rows = query(LIBRARY_SQL.format(where=""))
    games = [game_to_dict(r) for r in rows]
    if not with_wishlist:
        games = [g for g in games if not g["wishlist_only"]]
    _attach_subs(games)
    _attach_wish(games)
    _attach_mine(games)
    return games


def _attach_mine(games: list[dict]) -> None:
    """Installed copies (g["installed"]: [{platform, can_play}]) and your shelves (g["shelves"]: [ids])."""
    from . import installed
    inst = installed.by_game()
    shelves: dict[int, list[int]] = {}
    for r in query("SELECT shelf_id, game_id FROM shelf_games"):
        shelves.setdefault(r["game_id"], []).append(r["shelf_id"])
    for g in games:
        g["installed"] = inst.get(g["id"], [])
        g["shelves"] = shelves.get(g["id"], [])


def _attach_wish(games: list[dict]) -> None:
    """Wishlist info (release, prices, stores) for games on a wishlist: g["wish"], else None."""
    from .meta import wishlist
    wish = wishlist.by_game()
    for g in games:
        g["wish"] = wish.get(g["id"])


def _attach_subs(games: list[dict]) -> None:
    """Game Pass / PS Plus tags: [{"s": "gamepass", "new": bool, "leaving": bool}]."""
    from .meta import catalogs
    try:
        tags = catalogs.tags_by_game()
    except sqlite3.OperationalError:
        tags = {}
    for g in games:
        g["subs"] = tags.get(g["id"], [])


def get_meta(key: str, default=None):
    row = query("SELECT value FROM meta WHERE key=?", (key,))
    return row[0]["value"] if row else default


def set_meta(key: str, value: str) -> None:
    with tx() as c:
        c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))


def game_detail(game_id: int) -> dict | None:
    rows = query(LIBRARY_SQL.format(where="WHERE g.id = ?"), (game_id,))
    if not rows:
        return None
    d = game_to_dict(rows[0])
    d["entries"] = [dict(r) for r in query("SELECT * FROM entries WHERE game_id=? ORDER BY platform", (game_id,))]
    _attach_subs([d])
    _attach_wish([d])
    _attach_mine([d])
    d["similar_games"] = similar_games({**d, "similar": rows[0]["similar"]})
    from .meta import catalogs
    d["store_links"] = catalogs.store_links(game_id)
    d["play_days"] = [dict(r) for r in query(
        """SELECT p.day, SUM(p.minutes) AS minutes FROM play_days p JOIN entries e ON e.id = p.entry_id
           WHERE e.game_id=? AND NOT COALESCE(e.excluded, 0) GROUP BY p.day ORDER BY p.day DESC LIMIT 60""", (game_id,))]
    return d


def similar_games(g: dict, limit: int = 12) -> list[int]:
    """Library games (yours, XGP or PS+) that IGDB lists as similar to this one, or that list
    this one as similar to them: IGDB's own order first."""
    if not g.get("igdb_id"):
        return []
    rows = query("""SELECT g.id, g.igdb_id, g.similar FROM games g WHERE g.igdb_id IS NOT NULL AND g.id <> ?
                     AND EXISTS (SELECT 1 FROM entries e WHERE e.game_id = g.id AND COALESCE(e.ownership, '') <> 'wishlist')""",
                 (g["id"],))
    by_igdb = {r["igdb_id"]: r["id"] for r in rows}
    try:
        forward = [by_igdb[i] for i in json.loads(g.get("similar") or "[]") if i in by_igdb]
    except (TypeError, ValueError):
        forward = []
    me = g["igdb_id"]
    back = []
    for r in rows:
        if r["similar"] and str(me) in r["similar"]:
            try:
                if me in json.loads(r["similar"]):
                    back.append(r["id"])
            except ValueError:
                pass
    out = []
    for gid in forward + back:
        if gid not in out:
            out.append(gid)
    return out[:limit]


def set_json(value) -> str | None:
    return json.dumps(value) if value else None


# ---------------------------------------------------------------- yearly playtime

def _today() -> str:
    import datetime
    return datetime.date.today().isoformat()


def log_playtime(c: sqlite3.Connection | None = None) -> None:
    """Record today's lifetime playtime for every copy (one row per copy per day)."""
    sql = "INSERT OR REPLACE INTO playtime_log(entry_id, day, playtime_min) SELECT id, ?, playtime_min FROM entries WHERE " + OWNED
    if c is not None:
        c.execute(sql, (_today(),))
    else:
        with tx() as c2:
            c2.execute(sql, (_today(),))


def ensure_baseline() -> None:
    """First run: remember where every copy's playtime started."""
    with tx() as c:
        c.execute(
            "INSERT OR IGNORE INTO playtime_log(entry_id, day, playtime_min) "
            "SELECT id, ?, playtime_min FROM entries WHERE id NOT IN (SELECT entry_id FROM playtime_log)",
            (_today(),),
        )


def year_stats() -> dict:
    """Hours played since January 1 (or since tracking began, if that was later this year)."""
    import datetime
    year = datetime.date.today().year
    jan1 = f"{year}-01-01"
    jan1_ts = int(datetime.datetime(year, 1, 1).timestamp())
    total_min = 0
    for e in query("SELECT id, playtime_min, first_played FROM entries WHERE " + OWNED):
        before = query("SELECT playtime_min FROM playtime_log WHERE entry_id=? AND day<=? ORDER BY day DESC LIMIT 1",
                       (e["id"], jan1))
        if before:
            base = before[0]["playtime_min"]
        elif e["first_played"] and e["first_played"] >= jan1_ts:
            base = 0  # first played this year: all of it counts
        else:
            first = query("SELECT playtime_min FROM playtime_log WHERE entry_id=? ORDER BY day LIMIT 1", (e["id"],))
            base = first[0]["playtime_min"] if first else e["playtime_min"]
        total_min += max(e["playtime_min"] - base, 0)
    start = query("SELECT MIN(day) d FROM playtime_log")[0]["d"]
    return {"hours": round(total_min / 60, 1), "since": start if start and start > jan1 else None, "year": year}


# ---------------------------------------------------------------- your year in games

def year_in_games(year: int) -> dict:
    """What your stores say about one calendar year: games played (last or first played that year,
    or days on the Switch's play history), games finished, and hours where Sunbeam's daily record
    covers it. A game counts as finished on the date its achievement or trophy was earned when it
    was marked automatically; otherwise on the day you last played it (marking a game Completed
    later, during setup say, doesn't move it into that year). Unknown dates don't count."""
    import datetime
    start = int(datetime.datetime(year, 1, 1).timestamp())
    end = int(datetime.datetime(year + 1, 1, 1).timestamp())
    ds, de = f"{year}-01-01", f"{year + 1}-01-01"
    rows = query("""SELECT e.id, e.game_id, e.platform, e.last_played, e.first_played, e.ownership
                    FROM entries e WHERE NOT COALESCE(e.excluded, 0) AND COALESCE(e.ownership, '') NOT IN ('catalog', 'wishlist')""")
    played, started = set(), set()
    for r in rows:
        if r["last_played"] and start <= r["last_played"] < end:
            played.add(r["game_id"])
        if r["first_played"] and start <= r["first_played"] < end:
            played.add(r["game_id"])
            started.add(r["game_id"])
    minutes: dict[int, int] = {}
    for r in query("""SELECT e.game_id, SUM(p.minutes) m FROM play_days p JOIN entries e ON e.id = p.entry_id
                      WHERE p.day >= ? AND p.day < ? AND NOT COALESCE(e.excluded, 0) GROUP BY e.game_id""", (ds, de)):
        played.add(r["game_id"])
        minutes[r["game_id"]] = minutes.get(r["game_id"], 0) + (r["m"] or 0)
    # Sunbeam's own daily record of lifetime playtime: the growth within the year.
    for r in query("""SELECT e.game_id, MAX(l.playtime_min) - MIN(l.playtime_min) m
                      FROM playtime_log l JOIN entries e ON e.id = l.entry_id
                      WHERE l.day >= ? AND l.day < ? AND NOT COALESCE(e.excluded, 0) AND e.platform NOT IN ('switch', 'switch2')
                      GROUP BY l.entry_id HAVING m > 0""", (ds, de)):
        minutes[r["game_id"]] = minutes.get(r["game_id"], 0) + r["m"]
        played.add(r["game_id"])
    last_by_game: dict[int, int] = {}
    for r in rows:
        if r["last_played"]:
            last_by_game[r["game_id"]] = max(last_by_game.get(r["game_id"], 0), r["last_played"])
    finished = []
    for g in query("SELECT id, status_at, status_auto FROM games WHERE status='completed'"):
        if g["status_auto"]:
            at = g["status_at"]
        else:
            last = last_by_game.get(g["id"])
            at = min(last, g["status_at"]) if last and g["status_at"] else last
        if at and start <= at < end:
            finished.append({"id": g["id"], "at": at})
            played.add(g["id"])
    finished.sort(key=lambda x: -x["at"])
    first = query("""SELECT MIN(x) y FROM (SELECT MIN(last_played) x FROM entries WHERE last_played >= 1072915200
                     UNION SELECT MIN(first_played) FROM entries WHERE first_played >= 1072915200)""")[0]["y"]
    this_year = datetime.date.today().year
    first_year = datetime.date.fromtimestamp(first).year if first else this_year
    log_start = query("SELECT MIN(day) d FROM playtime_log")[0]["d"]
    return {"year": year, "years": list(range(this_year, first_year - 1, -1)),
            "played": sorted(played), "started": sorted(started), "finished": finished,
            "minutes": {str(k): v for k, v in minutes.items()},
            "log_since": log_start if log_start and log_start > ds else None}
