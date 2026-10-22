"""SQLite store in /config: all of ampanion's state lives in one file."""

import json
import secrets
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS listeners (
    account_id INTEGER PRIMARY KEY,  -- plex.tv account id
    name       TEXT NOT NULL,
    added_at   TEXT NOT NULL,
    first_fill_done INTEGER NOT NULL DEFAULT 0  -- their whole Plex history has been read once
);
CREATE TABLE IF NOT EXISTS plays (
    account_id   INTEGER NOT NULL,
    history_key  TEXT NOT NULL,  -- Plex's historyKey
    played_at    TEXT NOT NULL,
    rating_key   TEXT NOT NULL,  -- the track's current rating key, re-linked by guid when Plex changes it
    guid         TEXT,
    title        TEXT NOT NULL,
    artist       TEXT NOT NULL,
    album_artist TEXT NOT NULL,
    album        TEXT NOT NULL,
    genres       TEXT NOT NULL DEFAULT '[]',  -- JSON list: the track's genres when the Play was synced
    PRIMARY KEY (account_id, history_key)
);
CREATE TABLE IF NOT EXISTS skips (
    account_id       INTEGER NOT NULL,
    row_id           INTEGER NOT NULL,  -- Tautulli's row id
    started_at       TEXT NOT NULL,     -- in the key too: a reinstalled Tautulli numbers its rows from 1 again
    rating_key       TEXT NOT NULL,
    guid             TEXT,
    title            TEXT NOT NULL,
    artist           TEXT NOT NULL,
    album_artist     TEXT NOT NULL,
    album            TEXT NOT NULL,
    percent_complete INTEGER NOT NULL,
    PRIMARY KEY (account_id, row_id, started_at)
);
CREATE TABLE IF NOT EXISTS skip_syncs (
    account_id INTEGER PRIMARY KEY,
    synced_at  TEXT NOT NULL  -- the last Skip sync from Tautulli, where the next one picks up
);
CREATE TABLE IF NOT EXISTS mix_settings (
    account_id INTEGER NOT NULL,
    mix        TEXT NOT NULL,
    enabled    INTEGER NOT NULL,
    settings   TEXT NOT NULL,  -- JSON object of the Mix's numeric settings
    PRIMARY KEY (account_id, mix)
);
CREATE TABLE IF NOT EXISTS managed_playlists (
    account_id   INTEGER NOT NULL,
    mix          TEXT NOT NULL,
    genre        TEXT NOT NULL DEFAULT '',  -- Genre Mix only
    playlist_id  TEXT,                      -- none until a Playlist has been published
    tracks       TEXT NOT NULL DEFAULT '[]', -- JSON list of the rating keys last published
    status       TEXT NOT NULL,
    refreshed_at TEXT NOT NULL,
    PRIMARY KEY (account_id, mix, genre)
);
CREATE TABLE IF NOT EXISTS mix_builds (
    account_id INTEGER NOT NULL,
    mix        TEXT NOT NULL,
    built_at   TEXT NOT NULL,  -- when the Mix was last rebuilt, which decides when a weekly Mix is due
    PRIMARY KEY (account_id, mix)
);
CREATE TABLE IF NOT EXISTS refreshes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER,           -- none for the "Plex sign-in expired" record
    started_at TEXT NOT NULL,
    ended_at   TEXT,              -- none while the Refresh runs
    outcome    TEXT,              -- "ok" or "failed"
    mixes      TEXT NOT NULL DEFAULT '[]',  -- JSON list of {"mix", "outcome", "detail"}
    error      TEXT
);
"""


@dataclass(frozen=True)
class Listener:
    account_id: int
    name: str


@dataclass(frozen=True)
class Play:
    played_at: datetime
    rating_key: str
    guid: str | None
    title: str
    artist: str
    album_artist: str
    album: str
    genres: tuple[str, ...] = ()  # snapshot, so Top Genres still count the Play once its track is gone


@dataclass(frozen=True)
class Skip:
    started_at: datetime
    rating_key: str
    guid: str | None
    title: str
    artist: str
    album_artist: str
    album: str
    percent_complete: int


@dataclass(frozen=True)
class ManagedPlaylist:
    mix: str
    genre: str | None
    playlist_id: str | None
    tracks: list[str]
    status: str
    refreshed_at: datetime


@dataclass(frozen=True)
class RefreshRecord:
    account_id: int | None
    listener_name: str | None
    started_at: datetime
    ended_at: datetime | None
    outcome: str | None  # None while running
    mixes: list[dict]  # {"mix", "outcome", "detail"}
    error: str | None


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

    def reset(self) -> None:
        """Back to first run: empty every table, keeping only the identity ampanion generated for itself."""
        with self.connect() as db:
            tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite%'")]
            for table in tables:  # every table, so ones added later are wiped too
                if table == "app_settings":
                    db.execute("DELETE FROM app_settings WHERE key NOT IN ('client_identifier', 'session_secret')")
                else:
                    db.execute(f"DELETE FROM {table}")

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
        """Store the Owner and their token; a new token is never an expired one."""
        with self.connect() as db:
            db.executemany(
                "INSERT OR REPLACE INTO app_settings VALUES (?, ?)",
                [("owner_account_id", str(account_id)), ("owner_username", username), ("owner_token", token)],
            )
            db.execute("DELETE FROM app_settings WHERE key = 'owner_token_expired'")

    @property
    def owner_token_expired(self) -> bool:
        """plex.tv rejected the Owner token: Refreshes are paused until the Owner signs in again."""
        return self._setting("owner_token_expired") is not None

    def set_owner_token_expired(self) -> None:
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO app_settings VALUES ('owner_token_expired', '1')")

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

    @property
    def tautulli_url(self) -> str | None:
        return self._setting("tautulli_url")

    @property
    def tautulli_api_key(self) -> str | None:
        """Tautulli's admin API key: a secret, never logged or sent to the browser in full."""
        return self._setting("tautulli_api_key")

    def set_tautulli(self, url: str | None, api_key: str | None) -> None:
        """Save Tautulli, or forget it with None. Skips already synced are kept."""
        with self.connect() as db:
            db.execute("DELETE FROM app_settings WHERE key IN ('tautulli_url', 'tautulli_api_key')")
            if url:
                db.executemany("INSERT INTO app_settings VALUES (?, ?)", [("tautulli_url", url), ("tautulli_api_key", api_key)])

    # --- Schedule ---

    @property
    def run_time(self) -> time:
        """The nightly run's local time (in TZ)."""
        return time.fromisoformat(self._setting("run_time") or "04:00")

    def set_run_time(self, run_time: time) -> None:
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO app_settings VALUES ('run_time', ?)", (run_time.strftime("%H:%M"),))

    def cadence(self, mix: str) -> str | None:
        """The Mix's server-wide cadence, "daily" or "weekly", if the Owner has set one."""
        return self._setting(f"cadence.{mix}")

    def set_cadence(self, mix: str, cadence: str) -> None:
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO app_settings VALUES (?, ?)", (f"cadence.{mix}", cadence))

    @property
    def last_slot(self) -> datetime | None:
        """The latest nightly slot that has had its run (or needed none)."""
        value = self._setting("last_slot")
        return datetime.fromisoformat(value) if value else None

    def set_last_slot(self, slot: datetime) -> None:
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO app_settings VALUES ('last_slot', ?)", (slot.isoformat(),))

    def mix_built(self, account_id: int, mix: str) -> datetime | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT built_at FROM mix_builds WHERE account_id = ? AND mix = ?", (account_id, mix)
            ).fetchone()
        return datetime.fromisoformat(row[0]) if row else None

    def set_mix_built(self, account_id: int, mix: str, built_at: datetime) -> None:
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO mix_builds VALUES (?, ?, ?)", (account_id, mix, built_at.isoformat()))

    def listeners(self) -> list[Listener]:
        with self.connect() as db:
            rows = db.execute("SELECT account_id, name FROM listeners ORDER BY added_at").fetchall()
        return [Listener(*row) for row in rows]

    def listener(self, account_id: int) -> Listener | None:
        return next((l for l in self.listeners() if l.account_id == account_id), None)

    def add_listener(self, account_id: int, name: str, added_at: datetime) -> None:
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO listeners VALUES (?, ?, ?, 0)", (account_id, name, added_at.isoformat()))

    def remove_listener(self, account_id: int) -> None:
        """Forget everything stored for the Listener. Later tables (Plays, Skips, Mix settings...) are deleted here too."""
        with self.connect() as db:
            for table in ("listeners", "plays", "skips", "skip_syncs", "mix_settings", "managed_playlists", "mix_builds", "refreshes"):
                db.execute(f"DELETE FROM {table} WHERE account_id = ?", (account_id,))

    # --- Listening history ---

    def first_fill_done(self, account_id: int) -> bool:
        with self.connect() as db:
            row = db.execute("SELECT first_fill_done FROM listeners WHERE account_id = ?", (account_id,)).fetchone()
        return bool(row and row[0])

    def add_plays(self, account_id: int, plays: list[tuple[str, Play]]) -> None:
        """Insert (historyKey, Play) pairs, leaving ones already stored; the Listener's first fill is then done."""
        with self.connect() as db:
            db.execute("UPDATE listeners SET first_fill_done = 1 WHERE account_id = ?", (account_id,))
            db.executemany(
                "INSERT OR IGNORE INTO plays VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (account_id, key, p.played_at.isoformat(), p.rating_key, p.guid, p.title, p.artist, p.album_artist, p.album, json.dumps(p.genres))
                    for key, p in plays
                ],
            )

    def plays(self, account_id: int) -> list[Play]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT played_at, rating_key, guid, title, artist, album_artist, album, genres FROM plays WHERE account_id = ?",
                (account_id,),
            ).fetchall()
        return [Play(datetime.fromisoformat(r[0]), *r[1:7], tuple(json.loads(r[7]))) for r in rows]

    def relink(self, account_id: int, guid: str, rating_key: str) -> None:
        """Point the Listener's Plays and Skips of the track with this guid at its new rating key."""
        with self.connect() as db:
            for table in ("plays", "skips"):
                db.execute(f"UPDATE {table} SET rating_key = ? WHERE account_id = ? AND guid = ?", (rating_key, account_id, guid))

    def add_skips(self, account_id: int, skips: list[tuple[int, Skip]], synced_at: datetime) -> None:
        """Insert (Tautulli row id, Skip) pairs, leaving ones already stored, and note when this sync ran."""
        with self.connect() as db:
            db.executemany(
                "INSERT OR IGNORE INTO skips VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (account_id, row_id, s.started_at.isoformat(), s.rating_key, s.guid, s.title, s.artist, s.album_artist, s.album, s.percent_complete)
                    for row_id, s in skips
                ],
            )
            db.execute("INSERT OR REPLACE INTO skip_syncs VALUES (?, ?)", (account_id, synced_at.isoformat()))

    def skips_synced(self, account_id: int) -> datetime | None:
        with self.connect() as db:
            row = db.execute("SELECT synced_at FROM skip_syncs WHERE account_id = ?", (account_id,)).fetchone()
        return datetime.fromisoformat(row[0]) if row else None

    def skips(self, account_id: int) -> list[Skip]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT started_at, rating_key, guid, title, artist, album_artist, album, percent_complete FROM skips WHERE account_id = ?",
                (account_id,),
            ).fetchall()
        return [Skip(datetime.fromisoformat(r[0]), *r[1:]) for r in rows]

    # --- Mixes and their Playlists ---

    def mix_settings(self, account_id: int, mix: str) -> tuple[bool, dict] | None:
        """(enabled, settings) as last saved, or None if never saved."""
        with self.connect() as db:
            row = db.execute(
                "SELECT enabled, settings FROM mix_settings WHERE account_id = ? AND mix = ?", (account_id, mix)
            ).fetchone()
        return (bool(row[0]), json.loads(row[1])) if row else None

    def set_mix_settings(self, account_id: int, mix: str, enabled: bool, settings: dict) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO mix_settings VALUES (?, ?, ?, ?)", (account_id, mix, enabled, json.dumps(settings))
            )

    def managed_playlists(self, account_id: int, mix: str) -> list[ManagedPlaylist]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT mix, genre, playlist_id, tracks, status, refreshed_at FROM managed_playlists"
                " WHERE account_id = ? AND mix = ? ORDER BY genre",
                (account_id, mix),
            ).fetchall()
        return [ManagedPlaylist(r[0], r[1] or None, r[2], json.loads(r[3]), r[4], datetime.fromisoformat(r[5])) for r in rows]

    def save_managed_playlist(self, account_id: int, playlist: ManagedPlaylist) -> None:
        p = playlist
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO managed_playlists VALUES (?, ?, ?, ?, ?, ?, ?)",
                (account_id, p.mix, p.genre or "", p.playlist_id, json.dumps(p.tracks), p.status, p.refreshed_at.isoformat()),
            )

    def forget_managed_playlist(self, account_id: int, mix: str, genre: str | None) -> None:
        with self.connect() as db:
            db.execute(
                "DELETE FROM managed_playlists WHERE account_id = ? AND mix = ? AND genre = ?", (account_id, mix, genre or "")
            )

    # --- Refresh records ---

    def start_refresh(self, account_id: int | None, started_at: datetime) -> int:
        with self.connect() as db:
            cursor = db.execute(
                "INSERT INTO refreshes (account_id, started_at) VALUES (?, ?)", (account_id, started_at.isoformat())
            )
            return cursor.lastrowid

    def finish_refresh(self, refresh_id: int, ended_at: datetime, outcome: str, mixes: list[dict], error: str | None):
        with self.connect() as db:
            db.execute(
                "UPDATE refreshes SET ended_at = ?, outcome = ?, mixes = ?, error = ? WHERE id = ?",
                (ended_at.isoformat(), outcome, json.dumps(mixes), error, refresh_id),
            )

    def delete_refresh(self, refresh_id: int) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM refreshes WHERE id = ?", (refresh_id,))

    def prune_refreshes(self, before: datetime) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM refreshes WHERE started_at < ?", (before.isoformat(),))

    def refreshes(self, account_id: int | None = None, limit: int = 20) -> list[RefreshRecord]:
        """The newest Refresh records first, all Listeners' or one Listener's."""
        where, args = ("WHERE r.account_id = ?", (account_id,)) if account_id is not None else ("", ())
        with self.connect() as db:
            rows = db.execute(
                "SELECT r.account_id, l.name, r.started_at, r.ended_at, r.outcome, r.mixes, r.error"
                f" FROM refreshes r LEFT JOIN listeners l ON l.account_id = r.account_id {where}"
                " ORDER BY r.id DESC LIMIT ?",
                (*args, limit),
            ).fetchall()
        return [
            RefreshRecord(r[0], r[1], datetime.fromisoformat(r[2]), r[3] and datetime.fromisoformat(r[3]), r[4], json.loads(r[5]), r[6])
            for r in rows
        ]

    def refresh_states(self) -> dict[int, str]:
        """Each Listener's latest Refresh: "refreshing", "failed" or "ok"."""
        with self.connect() as db:
            rows = db.execute(
                "SELECT account_id, outcome FROM refreshes WHERE id IN"
                " (SELECT MAX(id) FROM refreshes WHERE account_id IS NOT NULL GROUP BY account_id)"
            ).fetchall()
        return {account_id: outcome or "refreshing" for account_id, outcome in rows}
