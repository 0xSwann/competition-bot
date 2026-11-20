"""SQLite store for comps seen/entered, emails checked and small bits of state."""
import sqlite3
from datetime import datetime, timezone

from .config import DATA_DIR

SCHEMA = """
CREATE TABLE IF NOT EXISTS comps (
    node_id     TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    node_url    TEXT NOT NULL,
    go_url      TEXT,
    categories  TEXT,
    closing     TEXT,
    first_seen  TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'new',  -- new | entered | skipped | filtered
    entered_at  TEXT,
    entry_domain TEXT,
    daily       INTEGER NOT NULL DEFAULT 0  -- 1 = can be entered once a day
);
CREATE TABLE IF NOT EXISTS emails (
    msg_key     TEXT PRIMARY KEY,
    checked_at  TEXT NOT NULL,
    flagged     INTEGER NOT NULL DEFAULT 0,
    subject     TEXT,
    sender      TEXT
);
CREATE TABLE IF NOT EXISTS entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id     TEXT,
    title       TEXT NOT NULL,
    action      TEXT NOT NULL,  -- entered | skipped | later | quit | outside (a win not entered through the bot)
    at          TEXT NOT NULL,
    seconds     INTEGER,        -- time the comp was on screen in Enter comps
    won         INTEGER NOT NULL DEFAULT 0,
    prize_value REAL,
    won_at      TEXT
);
CREATE TABLE IF NOT EXISTS alerts (
    id          TEXT PRIMARY KEY,  -- the Gmail message id, so the same email is never posted twice
    data        TEXT NOT NULL,     -- JSON of the possible win, kept until it reaches Discord
    received_at TEXT NOT NULL,
    posted_at   TEXT
);
CREATE TABLE IF NOT EXISTS state (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path=None) -> sqlite3.Connection:
    if path is None:
        DATA_DIR.mkdir(exist_ok=True)
        path = DATA_DIR / "compbot.sqlite3"
    # The background task and Enter comps share this file: wait for each other instead of failing.
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode=WAL")
    had_entries = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'entries'").fetchone()
    conn.executescript(SCHEMA)
    if not had_entries:
        # Comps entered before stats tracking existed still count as entries, just without a time.
        conn.execute(
            "INSERT INTO entries (node_id, title, action, at) "
            "SELECT node_id, title, 'entered', entered_at FROM comps WHERE status = 'entered' AND entered_at IS NOT NULL"
        )
    if "daily" not in {r[1] for r in conn.execute("PRAGMA table_info(comps)")}:
        conn.execute("ALTER TABLE comps ADD COLUMN daily INTEGER NOT NULL DEFAULT 0")
        conn.execute("UPDATE comps SET daily = 1 WHERE lower(categories || ' ' || title) LIKE '%daily%'")
        conn.commit()
    if get_state(conn, "daily_v2") is None:
        # Older versions called a comp "daily" if "daily" appeared anywhere in its description ("daily prizes",
        # "drawn daily"), so one-off comps came back every day. Keep only those whose tags or title say so.
        from .feed import is_daily
        for r in conn.execute("SELECT node_id, categories, title FROM comps WHERE daily = 1").fetchall():
            if not is_daily({"categories": r["categories"] or "", "title": r["title"]}):
                conn.execute("UPDATE comps SET daily = 0 WHERE node_id = ?", (r["node_id"],))
        set_state(conn, "daily_v2", now())
    # Older versions linked to the OzBargain page instead of the comp's "Go to" link.
    conn.execute(
        "UPDATE comps SET go_url = 'https://www.ozbargain.com.au/goto/' || node_id "
        "WHERE go_url IS NULL OR go_url NOT LIKE '%/goto/%'"
    )
    conn.commit()
    return conn


def get_state(conn, key, default=None):
    row = conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_state(conn, key, value):
    conn.execute(
        "INSERT INTO state (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
    conn.commit()
