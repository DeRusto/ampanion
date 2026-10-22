"""In-memory stand-ins for everything outside ampanion. Tests set up the world here, then drive the app."""

import itertools
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from ampanion.gateway import (
    HistoryEntry,
    LibraryTrack,
    NotFound,
    Pin,
    PlexAccount,
    PlexConnection,
    PlexPlaylist,
    PlexResource,
    TokenExpired,
    Unreachable,
)


@dataclass
class FakePin:
    pin: Pin
    token: str | None = None


@dataclass
class FakePlaylist:
    id: str
    title: str
    summary: str = ""
    items: list[str] = field(default_factory=list)


class FakeGateway:
    """Models plex.tv, the Plex server and Tautulli in memory. Later tickets grow it alongside the Gateway."""

    def __init__(self):
        self._accounts_by_token: dict[str, PlexAccount] = {}
        self._resources_by_account: dict[int, list[PlexResource]] = {}
        self._pins: dict[int, FakePin] = {}
        self._servers_by_uri: dict[str, str] = {}  # uri -> machineIdentifier of the server answering there
        self._users_by_account: dict[int, list[PlexAccount]] = {}
        self._shared_tokens: dict[str, dict[int, str]] = {}  # machineIdentifier -> {account id: server token}
        self._ids = itertools.count(1)
        # The one Plex server's music library, history and per-account state (single-server world).
        self._tracks: dict[str, LibraryTrack] = {}
        self._history: list[tuple[int, HistoryEntry]] = []  # (local account id, entry)
        self._ratings: dict[str, dict[str, float]] = defaultdict(dict)  # token -> rating key -> 0-10
        self._playlists: dict[str, list[FakePlaylist]] = defaultdict(list)  # token -> its account's Playlists
        self.item_batches: dict[str, list[int]] = defaultdict(list)  # token -> size of each create/add call
        self.history_down = False

    # --- set up the world ---

    def add_account(self, username: str, token: str, account_id: int | None = None) -> PlexAccount:
        account = PlexAccount(id=account_id or len(self._accounts_by_token) + 1, username=username)
        self._accounts_by_token[token] = account
        return account

    def expire_token(self, token: str) -> None:
        del self._accounts_by_token[token]

    def add_server(
        self,
        owner_token: str,
        name: str = "Plex Server",
        machine_id: str | None = None,
        connections: list[PlexConnection] | None = None,
        answering: list[str] | None = None,
    ) -> PlexResource:
        """A Plex Media Server owned by the account holding owner_token.

        By default it has one local connection that answers; `answering` names the connection URIs that do.
        """
        machine_id = machine_id or f"machine-{next(self._ids)}"
        if connections is None:
            connections = [PlexConnection(uri=f"http://{machine_id}.lan:32400", local=True, relay=False)]
        server = PlexResource(
            name=name, client_identifier=machine_id, provides="server", owned=True, connections=tuple(connections)
        )
        for uri in [c.uri for c in connections] if answering is None else answering:
            self.serve(uri, machine_id)
        self._resources_by_account.setdefault(self._accounts_by_token[owner_token].id, []).append(server)
        return server

    def serve(self, uri: str, machine_id: str) -> None:
        """The Plex server machine_id answers at uri."""
        self._servers_by_uri[uri] = machine_id

    def share_server(self, server: PlexResource, with_token: str) -> None:
        """The account holding with_token sees server, without owning it (a friend or Home member)."""
        self._resources_by_account.setdefault(self.check_token(with_token).id, []).append(replace(server, owned=False))

    def disown_server(self, token: str, machine_id: str) -> None:
        """The account holding token still sees the server, but no longer owns it."""
        resources = self._resources_by_account[self.check_token(token).id]
        resources[:] = [replace(r, owned=False) if r.client_identifier == machine_id else r for r in resources]

    def add_user(
        self, owner_token: str, username: str, account_id: int | None = None, share: PlexResource | None = None
    ) -> PlexAccount:
        """A friend or Home member of the account holding owner_token, with a library share on `share` if given."""
        user = self.add_account(username, token=f"{username}-token", account_id=account_id)
        self._users_by_account.setdefault(self.check_token(owner_token).id, []).append(user)
        if share is not None:
            self._shared_tokens.setdefault(share.client_identifier, {})[user.id] = f"{username}-server-token"
        return user

    def unshare(self, account_id: int) -> None:
        """The Owner stops sharing the server with the account."""
        for tokens in self._shared_tokens.values():
            tokens.pop(account_id, None)

    def approve_pin(self, code: str, token: str) -> None:
        """The person signs in on Plex's auth page, which attaches their token to the PIN."""
        self.pin_by_code(code).token = token

    def pin_by_code(self, code: str) -> FakePin:
        return next(p for p in self._pins.values() if p.pin.code == code)

    def add_track(self, rating_key: str, artist: str = "Artist", album: str = "Album", **fields) -> LibraryTrack:
        """A library track; its album and artist keys are "album-<album>" and "artist-<artist>"."""
        track = LibraryTrack(
            **{
                "rating_key": rating_key,
                "guid": f"plex://track/{rating_key}",
                "title": f"Song {rating_key}",
                "artist": artist,
                "album_artist": artist,
                "album": album,
                "album_key": f"album-{album}",
                "artist_key": f"artist-{artist}",
            }
            | fields
        )
        self._tracks[rating_key] = track
        return track

    def rekey_track(self, old: str, new: str) -> None:
        """Plex gives the track a new rating key (e.g. after a rescan), keeping its guid."""
        self._tracks[new] = replace(self._tracks.pop(old), rating_key=new)

    def remove_track(self, rating_key: str) -> None:
        del self._tracks[rating_key]

    def play(self, account_id: int, rating_key: str, at: datetime) -> str:
        """The local account (the Owner is 1) plays the track to the end, so Plex logs it in history."""
        track = self._tracks[rating_key]
        key = f"/status/sessions/history/{next(self._ids)}"
        entry = HistoryEntry(key, rating_key, at, track.title, track.artist, track.album_artist, track.album)
        self._history.append((account_id, entry))
        return key

    def clear_history(self, account_id: int) -> None:
        """An admin deletes the account's history in Plex."""
        self._history = [(a, e) for a, e in self._history if a != account_id]

    def rate(self, token: str, rating_key: str, stars: float) -> None:
        self._ratings[token][rating_key] = stars * 2

    def playlists_of(self, token: str) -> list[FakePlaylist]:
        return list(self._playlists[token])

    def rename_playlist(self, token: str, playlist_id: str, title: str) -> None:
        """The Playlist's owner renames it in Plex."""
        self._playlist(token, playlist_id).title = title

    def _server_token(self, token: str) -> None:
        """Plex server calls take an account token or a shared server token; anything else is refused."""
        shared = {t for tokens in self._shared_tokens.values() for t in tokens.values()}
        if token not in self._accounts_by_token and token not in shared:
            raise TokenExpired("The Plex server rejected the token")

    def _playlist(self, token: str, playlist_id: str) -> FakePlaylist:
        found = next((p for p in self._playlists[token] if p.id == playlist_id), None)
        if found is None:
            raise NotFound(f"Plex server has no /playlists/{playlist_id}")
        return found

    # --- Gateway operations ---

    def check_token(self, token: str) -> PlexAccount:
        try:
            return self._accounts_by_token[token]
        except KeyError:
            raise TokenExpired("plex.tv rejected the token") from None

    def create_pin(self) -> Pin:
        pin = Pin(id=next(self._ids), code=f"CODE{len(self._pins) + 1}")
        self._pins[pin.id] = FakePin(pin=pin)
        return pin

    def pin_token(self, pin_id: int) -> str | None:
        if pin_id not in self._pins:
            raise Unreachable("plex.tv answered 404")
        return self._pins[pin_id].token

    def resources(self, token: str) -> list[PlexResource]:
        return list(self._resources_by_account.get(self.check_token(token).id, []))

    def server_identity(self, url: str, token: str) -> str:
        if url not in self._servers_by_uri:
            raise Unreachable(f"Plex server at {url}: connection refused")
        return self._servers_by_uri[url]

    def users(self, token: str) -> list[PlexAccount]:
        return list(self._users_by_account.get(self.check_token(token).id, []))

    def shared_tokens(self, token: str, machine_id: str) -> dict[int, str]:
        self.check_token(token)
        return dict(self._shared_tokens.get(machine_id, {}))


    def history(self, url: str, token: str, account_id: int, since: datetime | None) -> list[HistoryEntry]:
        self._server_token(token)
        if self.history_down:
            raise Unreachable("Plex server answered 500")
        entries = [e for a, e in self._history if a == account_id and (since is None or e.played_at >= since)]
        return sorted(entries, key=lambda e: e.played_at, reverse=True)

    def library_tracks(self, url: str, token: str) -> list[LibraryTrack]:
        self._server_token(token)
        return list(self._tracks.values())

    def ratings(self, url: str, token: str) -> dict[str, float]:
        self._server_token(token)
        return dict(self._ratings[token])

    def playlist(self, url: str, token: str, playlist_id: str) -> PlexPlaylist | None:
        self._server_token(token)
        try:
            p = self._playlist(token, playlist_id)
        except NotFound:
            return None
        return PlexPlaylist(p.id, p.title, p.summary)

    def playlists(self, url: str, token: str) -> list[PlexPlaylist]:
        self._server_token(token)
        return [PlexPlaylist(p.id, p.title, p.summary) for p in self._playlists[token]]

    def delete_playlist(self, url: str, token: str, playlist_id: str) -> None:
        self._server_token(token)
        self._playlists[token].remove(self._playlist(token, playlist_id))

    def create_playlist(self, url: str, token: str, machine_id: str, title: str, rating_keys: list[str]) -> str:
        self._server_token(token)
        assert rating_keys, "Plex refuses to create an empty Playlist"
        playlist = FakePlaylist(id=str(next(self._ids)), title=title, items=list(rating_keys))
        self._playlists[token].append(playlist)
        self.item_batches[token].append(len(rating_keys))
        return playlist.id

    def clear_playlist(self, url: str, token: str, playlist_id: str) -> None:
        self._server_token(token)
        self._playlist(token, playlist_id).items.clear()

    def add_to_playlist(self, url: str, token: str, machine_id: str, playlist_id: str, rating_keys: list[str]) -> None:
        self._server_token(token)
        self._playlist(token, playlist_id).items.extend(rating_keys)
        self.item_batches[token].append(len(rating_keys))

    def set_playlist_summary(self, url: str, token: str, playlist_id: str, summary: str) -> None:
        self._server_token(token)
        self._playlist(token, playlist_id).summary = summary


class FakeClock:
    def __init__(self, now: datetime):
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, **kwargs) -> None:
        self._now += timedelta(**kwargs)
