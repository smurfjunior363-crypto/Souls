import os
import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS sl_games (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
);
CREATE TABLE IF NOT EXISTS sl_challenges (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT NOT NULL UNIQUE COLLATE NOCASE,
    rules TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS sl_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    game_id       INTEGER NOT NULL,
    challenge_id  INTEGER NOT NULL,
    build         TEXT NOT NULL DEFAULT '',
    seconds       INTEGER NOT NULL,
    proof_msg_id  INTEGER,
    proof_url     TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'pending',  -- pending|approved|rejected|cancelled|invalidated
    reviewer_id   INTEGER,
    reject_reason TEXT,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_sl_cat ON sl_runs(game_id, challenge_id, status);
CREATE TABLE IF NOT EXISTS logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id   INTEGER NOT NULL,
    action     TEXT NOT NULL,
    details    TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class Database:
    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def connect(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.executescript(SCHEMA)
        await self.conn.commit()

    async def _one(self, sql, args=()):
        async with self.conn.execute(sql, args) as cur:
            return await cur.fetchone()

    async def _all(self, sql, args=()):
        async with self.conn.execute(sql, args) as cur:
            return await cur.fetchall()

    async def log(self, actor_id: int, action: str, details: str = ""):
        await self.conn.execute(
            "INSERT INTO logs(actor_id, action, details) VALUES (?,?,?)", (actor_id, action, details)
        )
        await self.conn.commit()
