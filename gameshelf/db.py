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

from .paths import DB_PATH

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
        _conn.executescript(SCHEMA)
        _migrate(_conn)
    return _conn


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
            ("status_auto", "TEXT"),            # why GameShelf marked it completed (achievement/trophy)
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
    c.execute("UPDATE entries SET game_id=? WHERE game_id=?", (keep_id, drop_id))
    c.execute("UPDATE sub_catalog SET game_id=? WHERE game_id=?", (keep_id, drop_id))
    c.execute("UPDATE wishlist SET game_id=? WHERE game_id=?", (keep_id, drop_id))
    # Keep whichever personal data exists.
    status, status_at = keep["status"], keep["status_at"]
    if not status and drop["status"]:
        status, status_at = drop["status"], drop["status_at"]
    notes = "\n\n".join(n for n in (keep["notes"], drop["notes"]) if n)
    c.execute(
        """UPDATE games SET status=?, status_at=?, notes=?, steam_appid=COALESCE(steam_appid, ?),
           psn_image=COALESCE(psn_image, ?), added_at=MIN(added_at, ?) WHERE id=?""",
        (status, status_at, notes, drop["steam_appid"], drop["psn_image"], drop["added_at"], keep_id),
    )
    c.execute("DELETE FROM games WHERE id=?", (drop_id,))


def delete_orphans(c: sqlite3.Connection) -> None:
    c.execute("DELETE FROM games WHERE id NOT IN (SELECT DISTINCT game_id FROM entries)")


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
    return games


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
    from .meta import catalogs
    d["store_links"] = catalogs.store_links(game_id)
    d["play_days"] = [dict(r) for r in query(
        """SELECT p.day, SUM(p.minutes) AS minutes FROM play_days p JOIN entries e ON e.id = p.entry_id
           WHERE e.game_id=? AND NOT COALESCE(e.excluded, 0) GROUP BY p.day ORDER BY p.day DESC LIMIT 60""", (game_id,))]
    return d


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
