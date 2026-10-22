"""SQLite store in /config: all of ampanion's state lives in one file."""

import secrets
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS listeners (
    account_id INTEGER PRIMARY KEY,  -- plex.tv account id
    name       TEXT NOT NULL,
    added_at   TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Listener:
    account_id: int
    name: str



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

    @property
    def owner_account_id(self) -> int | None:
        value = self._setting("owner_account_id")
        return int(value) if value else None

    @property
    def owner_username(self) -> str | None:
        return self._setting("owner_username")

    @property
    def owner_token(self) -> str | None:
        """The Owner's Plex token: a full-account credential, never logged or sent to the browser."""
        return self._setting("owner_token")

    def set_owner(self, account_id: int, username: str, token: str) -> None:
        with self.connect() as db:
            db.executemany(
                "INSERT OR REPLACE INTO app_settings VALUES (?, ?)",
                [("owner_account_id", str(account_id)), ("owner_username", username), ("owner_token", token)],
            )

    @property
    def server_machine_id(self) -> str | None:
        return self._setting("server_machine_id")

    @property
    def server_name(self) -> str | None:
        return self._setting("server_name")

    @property
    def server_manual_url(self) -> str | None:
        return self._setting("server_manual_url") or None

    @property
    def server_url(self) -> str | None:
        """Where ampanion reaches the Plex server: the manual URL from Settings if set, else the discovered one."""
        return self.server_manual_url or self._setting("server_url") or None

    def set_server(self, machine_id: str, name: str, url: str | None) -> None:
        with self.connect() as db:
            db.executemany(
                "INSERT OR REPLACE INTO app_settings VALUES (?, ?)",
                [("server_machine_id", machine_id), ("server_name", name), ("server_url", url or "")],
            )

    def set_server_manual_url(self, url: str | None) -> None:
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO app_settings VALUES ('server_manual_url', ?)", (url or "",))

    def listeners(self) -> list[Listener]:
        with self.connect() as db:
            rows = db.execute("SELECT account_id, name FROM listeners ORDER BY added_at").fetchall()
        return [Listener(*row) for row in rows]

    def listener(self, account_id: int) -> Listener | None:
        return next((l for l in self.listeners() if l.account_id == account_id), None)

    def add_listener(self, account_id: int, name: str, added_at: datetime) -> None:
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO listeners VALUES (?, ?, ?)", (account_id, name, added_at.isoformat()))

    def remove_listener(self, account_id: int) -> None:
        """Forget everything stored for the Listener. Later tables (Plays, Skips, Mix settings...) are deleted here too."""
        with self.connect() as db:
            db.execute("DELETE FROM listeners WHERE account_id = ?", (account_id,))
