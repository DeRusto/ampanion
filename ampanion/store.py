"""SQLite store in /config: all of ampanion's state lives in one file."""

import secrets
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: Path):
        self.path = path

    @classmethod
    def open(cls, config_dir: Path) -> "Store":
        """Open (creating on first run) the database in config_dir."""
        config_dir.mkdir(parents=True, exist_ok=True)
        store = cls(config_dir / "ampanion.db")
        with store.connect() as db:
            db.executescript(SCHEMA)
            # INSERT OR IGNORE: generated once on first run, kept on every restart.
            db.execute("INSERT OR IGNORE INTO app_settings VALUES ('client_identifier', ?)", (str(uuid.uuid4()),))
            db.execute("INSERT OR IGNORE INTO app_settings VALUES ('session_secret', ?)", (secrets.token_urlsafe(32),))
        return store

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """One transaction on a fresh connection; a connection per use is safe across FastAPI's worker threads."""
        db = sqlite3.connect(self.path)
        try:
            with db:
                yield db
        finally:
            db.close()

    def ping(self) -> None:
        with self.connect() as db:
            db.execute("SELECT 1")

    def _setting(self, key: str) -> str | None:
        with self.connect() as db:
            row = db.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    @property
    def client_identifier(self) -> str:
        return self._setting("client_identifier")

    @property
    def session_secret(self) -> str:
        return self._setting("session_secret")
