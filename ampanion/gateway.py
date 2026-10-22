"""The Gateway: ampanion's only route to plex.tv, the Plex server and Tautulli.

Everything outside the process goes through a Gateway, so tests swap in an
in-memory fake (tests/fakes.py) and drive the rest of the app for real.
Each operation raises a GatewayError subclass the caller can tell apart.
"""

import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Protocol
from urllib.parse import quote
from xml.etree import ElementTree

import httpx
import requests

os.environ["PLEXAPI_PLEXAPI_AUTORELOAD"] = "false"  # read per object: a partial item must never fetch itself again

import plexapi  # noqa: E402
from plexapi.audio import Track  # noqa: E402
from plexapi.exceptions import NotFound as PlexNotFound, PlexApiException, Unauthorized  # noqa: E402
from plexapi.myplex import MyPlexAccount  # noqa: E402
from plexapi.playlist import Playlist  # noqa: E402
from plexapi.server import PlexServer  # noqa: E402
from plexapi.utils import setDatetimeTimezone  # noqa: E402

setDatetimeTimezone("UTC")  # plexapi's datetimes come back aware, in UTC, like the rest of ampanion's

PRODUCT = "ampanion"
PAGE = 1000  # container_size for library searches: one request per thousand items, not per hundred


class GatewayError(Exception):
    """Any failure talking to plex.tv, the Plex server or Tautulli."""


class TokenExpired(GatewayError):
    """plex.tv rejected the Owner's token (401): Refreshes must pause until the Owner signs in again."""


class Unreachable(GatewayError):
    """The service could not be reached or answered with an unexpected error."""


class NotFound(GatewayError):
    """The Plex server has no such item (404)."""


@dataclass(frozen=True)
class PlexAccount:
    id: int
    username: str


@dataclass(frozen=True)
class Pin:
    """A plex.tv sign-in PIN: the id stays server-side, the code goes to Plex's auth page."""

    id: int
    code: str


@dataclass(frozen=True)
class PlexConnection:
    """One way plex.tv says a server can be reached."""

    uri: str
    local: bool
    relay: bool


@dataclass(frozen=True)
class PlexResource:
    """A device on the account, as plex.tv lists it. `provides` is comma-separated, e.g. "server"."""

    name: str
    client_identifier: str
    provides: str
    owned: bool
    connections: tuple[PlexConnection, ...] = ()

    @property
    def is_server(self) -> bool:
        return "server" in self.provides.split(",")


@dataclass(frozen=True)
class HistoryEntry:
    """One Play as Plex's history lists it, with the track as it was named then."""

    history_key: str
    rating_key: str
    played_at: datetime
    title: str
    artist: str
    album_artist: str
    album: str


@dataclass(frozen=True)
class LibraryTrack:
    """A music track on the server, with its album and artist and their genre tags."""

    rating_key: str
    guid: str
    title: str
    artist: str
    album_artist: str
    album: str
    album_key: str
    artist_key: str
    track_genres: tuple[str, ...] = ()
    album_genres: tuple[str, ...] = ()
    artist_genres: tuple[str, ...] = ()
    popularity: int | None = None  # Plex's ratingCount (Last.fm scrobbles), when known
    section: str = ""  # the key of the music library it is in

    @property
    def genres(self) -> tuple[str, ...]:
        """The tags at the most specific level that has any: track, then album, then artist."""
        return self.track_genres or self.album_genres or self.artist_genres


@dataclass(frozen=True)
class TautulliRow:
    """One track row of Tautulli's history, with the track as it was named then."""

    row_id: int
    rating_key: str
    started_at: datetime
    title: str
    artist: str
    album_artist: str
    album: str
    percent_complete: int


@dataclass(frozen=True)
class PlexPlaylist:
    id: str
    title: str
    summary: str


class Gateway(Protocol):
    def check_token(self, token: str) -> PlexAccount:
        """Return the plex.tv account the token belongs to. Raises TokenExpired on 401."""
        ...

    def create_pin(self) -> Pin:
        """Create a strong sign-in PIN on plex.tv."""
        ...

    def pin_token(self, pin_id: int) -> str | None:
        """The token Plex attached to the PIN once the person signed in, else None."""
        ...

    def resources(self, token: str) -> list[PlexResource]:
        """The devices (servers, players) the token's account can see, with whether it owns them."""
        ...

    def server_identity(self, url: str, token: str) -> str:
        """The machineIdentifier of the Plex server answering at url. Raises Unreachable if none answers."""
        ...

    def users(self, token: str) -> list[PlexAccount]:
        """The token's friends and Home members (not the account itself), named by their display title."""
        ...

    def shared_tokens(self, token: str, machine_id: str) -> dict[int, str]:
        """Server tokens by plex.tv account id for the users with a share on the server (python-plexapi's switchUser)."""
        ...


    def history(self, url: str, token: str, account_id: int, since: datetime | None) -> list[HistoryEntry]:
        """Track Plays in the server's history for a local account id (the Owner is 1), all of it or since a time."""
        ...

    def library_tracks(self, url: str, token: str) -> list[LibraryTrack]:
        """Every track in the server's music libraries."""
        ...

    def music_sections(self, url: str, token: str) -> list[str]:
        """The keys of the music libraries the token can see: all of them for the Owner, the shared ones for a Listener."""
        ...

    def ratings(self, url: str, token: str) -> dict[str, float]:
        """The token's account's Ratings (0-10, 2 per star) by rating key, on tracks, albums and artists."""
        ...

    def playlist(self, url: str, token: str, playlist_id: str) -> PlexPlaylist | None:
        """The token's account's Playlist with this id, or None if it doesn't exist (or isn't theirs)."""
        ...

    def playlists(self, url: str, token: str) -> list[PlexPlaylist]:
        """The token's account's audio Playlists."""
        ...

    def delete_playlist(self, url: str, token: str, playlist_id: str) -> None:
        """Raises NotFound if the token's account has no such Playlist."""
        ...

    def create_playlist(self, url: str, token: str, machine_id: str, title: str, rating_keys: list[str]) -> str:
        """Create a regular audio Playlist holding rating_keys (at least one) in the token's account; returns its id."""
        ...

    def clear_playlist(self, url: str, token: str, playlist_id: str) -> None: ...

    def add_to_playlist(self, url: str, token: str, machine_id: str, playlist_id: str, rating_keys: list[str]) -> None:
        """Append rating_keys, in order."""
        ...

    def set_playlist_summary(self, url: str, token: str, playlist_id: str, summary: str) -> None: ...

    def tautulli_identity(self, url: str, api_key: str) -> str:
        """The machineIdentifier of the Plex server the Tautulli at url watches. Any failure raises Unreachable."""
        ...

    def tautulli_history(self, url: str, api_key: str, user_id: int, after: date | None) -> list[TautulliRow]:
        """Tautulli's ungrouped track rows for a plex.tv user id, all or started on or after a day, newest first."""
        ...


def _tags(item) -> tuple[str, ...]:
    return tuple(g.tag for g in item.genres)


def _playlist(item: Playlist) -> PlexPlaylist:
    return PlexPlaylist(id=str(item.ratingKey), title=item.title or "", summary=item.summary or "")


@contextmanager
def _errors(where: str, not_found: type[GatewayError] = NotFound):
    """Translate python-plexapi's and requests' failures into the Gateway's typed errors."""
    try:
        yield
    except Unauthorized as e:  # before BadRequest, its base class
        raise TokenExpired(f"{where} rejected the token") from e
    except PlexNotFound as e:
        raise not_found(f"{where}: {e}") from e
    # The last three: plexapi reading an answer that isn't shaped as it expects.
    except (PlexApiException, requests.RequestException, ElementTree.ParseError, AttributeError, TypeError, ValueError) as e:
        raise Unreachable(f"{where}: {e}") from e


class PlexGateway:
    """The real Gateway: python-plexapi for plex.tv and the Plex server, httpx for what it doesn't cover."""

    def __init__(self, client_identifier: str, http: httpx.Client | None = None, session: requests.Session | None = None):
        self._client_identifier = client_identifier
        self._http = http or httpx.Client(timeout=30)
        self._session = session or requests.Session()
        # plexapi sends these process-wide headers on every request; there is one Gateway per process.
        plexapi.BASE_HEADERS.update({"X-Plex-Client-Identifier": client_identifier, "X-Plex-Product": PRODUCT})

    # --- plex.tv ---

    def _plex_tv(self, method: str, path: str, **kwargs) -> dict:
        """The PIN endpoints, over httpx: plexapi's MyPlexPinLogin keeps the PIN id private to one object, and ampanion reads it back in a later request."""
        headers = {"Accept": "application/json", "X-Plex-Client-Identifier": self._client_identifier, "X-Plex-Product": PRODUCT}
        try:
            response = self._http.request(method, f"https://plex.tv/api/v2{path}", headers=headers, **kwargs)
        except httpx.HTTPError as e:
            raise Unreachable(f"plex.tv: {e}") from e
        if response.status_code == 401:
            raise TokenExpired("plex.tv rejected the token")
        if response.is_error:
            raise Unreachable(f"plex.tv answered {response.status_code}")
        return response.json()

    def _account(self, token: str) -> MyPlexAccount:
        return MyPlexAccount(token=token, session=self._session)  # reads /api/v2/user

    def check_token(self, token: str) -> PlexAccount:
        with _errors("plex.tv", Unreachable):
            account = self._account(token)
        return PlexAccount(id=account.id, username=account.username)

    def create_pin(self) -> Pin:
        body = self._plex_tv("POST", "/pins", params={"strong": "true"})
        return Pin(id=body["id"], code=body["code"])

    def pin_token(self, pin_id: int) -> str | None:
        return self._plex_tv("GET", f"/pins/{pin_id}").get("authToken")

    def resources(self, token: str) -> list[PlexResource]:
        with _errors("plex.tv", Unreachable):
            resources = self._account(token).resources()
        return [
            PlexResource(
                name=r.name,
                client_identifier=r.clientIdentifier,
                provides=r.provides or "",
                owned=bool(r.owned),
                connections=tuple(PlexConnection(uri=c.uri, local=bool(c.local), relay=bool(c.relay)) for c in r.connections),
            )
            for r in resources
        ]

    def server_identity(self, url: str, token: str) -> str:
        try:
            # Short timeout: connections are probed one after another and a dead LAN address must not stall the page.
            with _errors(f"Plex server at {url}", Unreachable):
                return PlexServer(url, token, session=self._session, timeout=5).machineIdentifier
        except TokenExpired as e:  # a server refusing the token is not the Owner's server answering
            raise Unreachable(str(e)) from e

    def users(self, token: str) -> list[PlexAccount]:
        with _errors("plex.tv", Unreachable):
            users = self._account(token).users()
        return [PlexAccount(id=u.id, username=u.title or u.username) for u in users]

    def shared_tokens(self, token: str, machine_id: str) -> dict[int, str]:
        # The shared_servers read behind switchUser, once for all users: MyPlexUser.get_token makes one request per
        # user and swallows every error, a 401 included, and switchUser also needs the server's URL.
        with _errors("plex.tv", Unreachable):
            shares = self._account(token).query(MyPlexAccount.FRIENDINVITE.format(machineId=machine_id))
        shares = shares.iter("SharedServer") if shares is not None else ()
        return {int(s.get("userID")): s.get("accessToken") for s in shares if s.get("accessToken")}

    # --- Plex server ---

    def _server(self, url: str, token: str) -> PlexServer:
        """A fresh connection (one GET /) per call, so new music sections and Playlists are never served from plexapi's caches."""
        with _errors("Plex server"):
            return PlexServer(url, token, session=self._session)

    def _music_libraries(self, server: PlexServer):
        return [s for s in server.library.sections() if s.type == "artist"]

    def music_sections(self, url: str, token: str) -> list[str]:
        server = self._server(url, token)
        with _errors("Plex server"):
            return [str(s.key) for s in self._music_libraries(server)]

    def history(self, url: str, token: str, account_id: int, since: datetime | None) -> list[HistoryEntry]:
        server = self._server(url, token)
        with _errors("Plex server"):
            # shortcut: assumes Plex decodes %3E in "viewedAt>" keys, unverified until tests/test_live_gateway.py runs against a live server
            entries = server.history(mindate=since, accountID=account_id)
        return [
            HistoryEntry(
                history_key=e.historyKey,
                rating_key=str(e.ratingKey),
                played_at=e.viewedAt,
                title=e.title or "",
                artist=e.originalTitle or e.grandparentTitle or "",
                album_artist=e.grandparentTitle or "",
                album=e.parentTitle or "",
            )
            for e in entries
            if isinstance(e, Track) and e.ratingKey is not None
        ]

    def library_tracks(self, url: str, token: str) -> list[LibraryTrack]:
        # shortcut: assumes bulk listings carry Genre tags and ratingCount, unverified until tests/test_live_gateway.py runs against a live server
        server, tracks = self._server(url, token), []
        with _errors("Plex server"):
            for section in self._music_libraries(server):
                artists = {str(a.ratingKey): _tags(a) for a in section.search(libtype="artist", container_size=PAGE)}
                albums = {str(a.ratingKey): _tags(a) for a in section.search(libtype="album", container_size=PAGE)}
                for t in section.search(libtype="track", container_size=PAGE):
                    album_key, artist_key = str(t.parentRatingKey or ""), str(t.grandparentRatingKey or "")
                    tracks.append(
                        LibraryTrack(
                            rating_key=str(t.ratingKey),
                            guid=t.guid or "",
                            title=t.title or "",
                            artist=t.originalTitle or t.grandparentTitle or "",
                            album_artist=t.grandparentTitle or "",
                            album=t.parentTitle or "",
                            album_key=album_key,
                            artist_key=artist_key,
                            track_genres=_tags(t),
                            album_genres=albums.get(album_key, ()),
                            artist_genres=artists.get(artist_key, ()),
                            popularity=t.ratingCount,
                            section=str(section.key),
                        )
                    )
        return tracks

    def ratings(self, url: str, token: str) -> dict[str, float]:
        server, ratings = self._server(url, token), {}
        with _errors("Plex server"):
            for section in self._music_libraries(server):
                for libtype in ("artist", "album", "track"):
                    for item in section.search(libtype=libtype, filters={"userRating>>": 0}, container_size=PAGE):
                        ratings[str(item.ratingKey)] = float(item.userRating)
        return ratings

    def _fetch_playlist(self, server: PlexServer, playlist_id: str) -> Playlist:
        return server.fetchItem(f"/playlists/{quote(playlist_id)}", cls=Playlist)

    def _tracks(self, server: PlexServer, rating_keys: list[str]) -> list[Track]:
        """Stand-ins plexapi builds Playlist uris from, without fetching each track."""
        return [Track(server, ElementTree.Element("Track", ratingKey=k)) for k in rating_keys]

    def playlist(self, url: str, token: str, playlist_id: str) -> PlexPlaylist | None:
        server = self._server(url, token)
        try:
            with _errors("Plex server"):
                return _playlist(self._fetch_playlist(server, playlist_id))
        except NotFound:
            return None

    def playlists(self, url: str, token: str) -> list[PlexPlaylist]:
        server = self._server(url, token)
        with _errors("Plex server"):
            return [_playlist(p) for p in server.playlists(playlistType="audio")]

    def delete_playlist(self, url: str, token: str, playlist_id: str) -> None:
        server = self._server(url, token)
        with _errors("Plex server"):
            self._fetch_playlist(server, playlist_id).delete()

    def create_playlist(self, url: str, token: str, machine_id: str, title: str, rating_keys: list[str]) -> str:
        # plexapi builds the uri from the server's own machineIdentifier, which is machine_id.
        server = self._server(url, token)
        with _errors("Plex server"):
            return str(server.createPlaylist(title, items=self._tracks(server, rating_keys)).ratingKey)

    def clear_playlist(self, url: str, token: str, playlist_id: str) -> None:
        server = self._server(url, token)
        with _errors("Plex server"):
            # plexapi has no "remove all items": a raw DELETE through its own query, for its headers and errors.
            server.query(f"/playlists/{quote(playlist_id)}/items", method=self._session.delete)

    def add_to_playlist(self, url: str, token: str, machine_id: str, playlist_id: str, rating_keys: list[str]) -> None:
        server = self._server(url, token)
        with _errors("Plex server"):
            self._fetch_playlist(server, playlist_id).addItems(self._tracks(server, rating_keys))

    def set_playlist_summary(self, url: str, token: str, playlist_id: str, summary: str) -> None:
        server = self._server(url, token)
        with _errors("Plex server"):
            self._fetch_playlist(server, playlist_id).editSummary(summary)

    # --- Tautulli, over httpx (plexapi doesn't cover it): read commands only, as its API key is an admin key that can delete history ---

    def _tautulli(self, url: str, api_key: str, cmd: str, **params):
        try:
            # The key goes in the header, never the query string, which would land in logs.
            response = self._http.get(f"{url.rstrip('/')}/api/v2", params={"cmd": cmd, **params}, headers={"X-Api-Key": api_key})
            envelope = response.json()["response"]
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
            raise Unreachable(f"Tautulli: {e}") from e
        if response.is_error or envelope.get("result") != "success":
            raise Unreachable(f"Tautulli answered {response.status_code}: {envelope.get('message') or 'error'}")
        return envelope.get("data")

    def tautulli_identity(self, url: str, api_key: str) -> str:
        try:
            return self._tautulli(url, api_key, "get_server_identity")["machine_identifier"]
        except (KeyError, TypeError) as e:
            raise Unreachable(f"Tautulli answered without a server identity: {e}") from e

    def tautulli_history(self, url: str, api_key: str, user_id: int, after: date | None) -> list[TautulliRow]:
        # grouping=0 always: the server default may merge sessions into one row.
        params = {"media_type": "track", "user_id": user_id, "grouping": 0, "include_activity": 0, "order_column": "date", "order_dir": "desc"}
        if after is not None:
            params["after"] = after.isoformat()  # inclusive, by day
        rows, size = [], 1000
        while True:
            body = self._tautulli(url, api_key, "get_history", **params, start=len(rows), length=size) or {}
            batch = body.get("data", [])
            rows += batch
            if len(batch) < size or len(rows) >= body.get("recordsFiltered", len(rows) + 1):
                break
        return [
            TautulliRow(
                row_id=int(r["row_id"]),
                rating_key=str(r["rating_key"]),
                started_at=datetime.fromtimestamp(r["started"], timezone.utc),
                title=r.get("title", ""),
                artist=r.get("original_title") or r.get("grandparent_title", ""),
                album_artist=r.get("grandparent_title", ""),
                album=r.get("parent_title", ""),
                percent_complete=int(r.get("percent_complete") or 0),
            )
            for r in rows
            if r.get("row_id") is not None and r.get("rating_key") not in (None, "")
        ]
