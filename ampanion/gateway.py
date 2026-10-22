"""The Gateway: ampanion's only route to plex.tv, the Plex server and Tautulli.

Everything outside the process goes through a Gateway, so tests swap in an
in-memory fake (tests/fakes.py) and drive the rest of the app for real.
Each operation raises a GatewayError subclass the caller can tell apart.
"""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Protocol
from urllib.parse import quote
from xml.etree import ElementTree

import httpx


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


def _metadata_uri(machine_id: str, rating_keys: list[str]) -> str:
    return f"server://{machine_id}/com.plexapp.plugins.library/library/metadata/{','.join(rating_keys)}"


def _playlist(item: dict) -> PlexPlaylist:
    return PlexPlaylist(id=str(item["ratingKey"]), title=item.get("title", ""), summary=item.get("summary", ""))


def _tags(item: dict) -> tuple[str, ...]:
    return tuple(g["tag"] for g in item.get("Genre", []))


class PlexGateway:
    """The real Gateway, over HTTP."""

    def __init__(self, client_identifier: str, http: httpx.Client | None = None):
        self._client_identifier = client_identifier
        self._http = http or httpx.Client(timeout=30)

    def _plex_tv_response(self, method: str, url: str, token: str | None, accept="application/json", **kwargs):
        headers = {
            "Accept": accept,
            "X-Plex-Client-Identifier": self._client_identifier,
            "X-Plex-Product": "ampanion",
        }
        if token:
            headers["X-Plex-Token"] = token
        try:
            response = self._http.request(method, url, headers=headers, **kwargs)
        except httpx.HTTPError as e:
            raise Unreachable(f"plex.tv: {e}") from e
        if response.status_code == 401:
            raise TokenExpired("plex.tv rejected the token")
        if response.is_error:
            raise Unreachable(f"plex.tv answered {response.status_code}")
        return response

    def _plex_tv(self, method: str, path: str, token: str | None = None, **kwargs):
        return self._plex_tv_response(method, f"https://plex.tv/api/v2{path}", token, **kwargs).json()

    def _plex_tv_xml(self, path: str, token: str) -> ElementTree.Element:
        """plex.tv's older /api endpoints, which answer only in XML."""
        response = self._plex_tv_response("GET", f"https://plex.tv/api{path}", token, accept="application/xml")
        try:
            return ElementTree.fromstring(response.content)
        except ElementTree.ParseError as e:
            raise Unreachable(f"plex.tv answered unreadable XML: {e}") from e

    def check_token(self, token: str) -> PlexAccount:
        body = self._plex_tv("GET", "/user", token)
        return PlexAccount(id=body["id"], username=body["username"])

    def create_pin(self) -> Pin:
        body = self._plex_tv("POST", "/pins", params={"strong": "true"})
        return Pin(id=body["id"], code=body["code"])

    def pin_token(self, pin_id: int) -> str | None:
        return self._plex_tv("GET", f"/pins/{pin_id}").get("authToken")

    def resources(self, token: str) -> list[PlexResource]:
        return [
            PlexResource(
                name=r["name"],
                client_identifier=r["clientIdentifier"],
                provides=r["provides"],
                owned=bool(r["owned"]),
                connections=tuple(
                    PlexConnection(uri=c["uri"], local=bool(c["local"]), relay=bool(c["relay"]))
                    for c in r.get("connections", [])
                ),
            )
            for r in self._plex_tv("GET", "/resources", token, params={"includeHttps": "1", "includeRelay": "1"})
        ]

    def server_identity(self, url: str, token: str) -> str:
        headers = {"Accept": "application/json", "X-Plex-Token": token}
        try:
            # Short timeout: connections are probed one after another and a dead LAN address must not stall the page.
            response = self._http.get(f"{url.rstrip('/')}/identity", headers=headers, timeout=5)
            response.raise_for_status()
            return response.json()["MediaContainer"]["machineIdentifier"]
        except (httpx.HTTPError, ValueError, KeyError) as e:
            raise Unreachable(f"Plex server at {url}: {e}") from e

    def users(self, token: str) -> list[PlexAccount]:
        return [
            PlexAccount(id=int(u.get("id")), username=u.get("title") or u.get("username"))
            for u in self._plex_tv_xml("/users", token).iter("User")
        ]

    def shared_tokens(self, token: str, machine_id: str) -> dict[int, str]:
        shares = self._plex_tv_xml(f"/servers/{machine_id}/shared_servers", token).iter("SharedServer")
        return {int(s.get("userID")): s.get("accessToken") for s in shares if s.get("accessToken")}

    # --- Plex server ---

    def _pms(self, method: str, url: str, path: str, token: str, params: dict | None = None) -> dict:
        headers = {"Accept": "application/json", "X-Plex-Token": token, "X-Plex-Client-Identifier": self._client_identifier}
        try:
            response = self._http.request(method, f"{url.rstrip('/')}{path}", headers=headers, params=params)
        except httpx.HTTPError as e:
            raise Unreachable(f"Plex server: {e}") from e
        if response.status_code == 401:
            raise TokenExpired("The Plex server rejected the token")
        if response.status_code == 404:
            raise NotFound(f"Plex server has no {path}")
        if response.is_error:
            raise Unreachable(f"Plex server answered {response.status_code} to {path}")
        try:
            return response.json().get("MediaContainer", {}) if response.content else {}
        except ValueError as e:
            raise Unreachable(f"Plex server answered unreadable JSON: {e}") from e

    def _pages(self, url: str, path: str, token: str, params: dict) -> list[dict]:
        """Every Metadata item of a listing, read a page at a time."""
        items, size = [], 1000
        while True:
            page = {"X-Plex-Container-Start": len(items), "X-Plex-Container-Size": size}
            body = self._pms("GET", url, path, token, params | page)
            batch = body.get("Metadata", [])
            items += batch
            if len(batch) < size or len(items) >= body.get("totalSize", len(items) + 1):
                return items

    def _music_sections(self, url: str, token: str) -> list[str]:
        sections = self._pms("GET", url, "/library/sections", token).get("Directory", [])
        return [d["key"] for d in sections if d.get("type") == "artist"]

    def history(self, url: str, token: str, account_id: int, since: datetime | None) -> list[HistoryEntry]:
        params = {"sort": "viewedAt:desc", "accountID": account_id}
        if since is not None:
            params["viewedAt>"] = int(since.timestamp())  # "viewedAt>=<epoch>": at or after. shortcut: assumes Plex decodes %3E in keys, check in the live contract suite
        return [
            HistoryEntry(
                history_key=e["historyKey"],
                rating_key=str(e["ratingKey"]),
                played_at=datetime.fromtimestamp(e["viewedAt"], timezone.utc),
                title=e.get("title", ""),
                artist=e.get("originalTitle") or e.get("grandparentTitle", ""),
                album_artist=e.get("grandparentTitle", ""),
                album=e.get("parentTitle", ""),
            )
            for e in self._pages(url, "/status/sessions/history/all", token, params)
            if e.get("type") == "track" and "ratingKey" in e
        ]

    def library_tracks(self, url: str, token: str) -> list[LibraryTrack]:
        # shortcut: whether bulk track listings carry Genre tags is unverified, check in the live contract suite
        tracks = []
        for section in self._music_sections(url, token):
            path = f"/library/sections/{section}/all"
            artists = {str(a["ratingKey"]): _tags(a) for a in self._pages(url, path, token, {"type": 8})}
            albums = {str(a["ratingKey"]): _tags(a) for a in self._pages(url, path, token, {"type": 9})}
            for t in self._pages(url, path, token, {"type": 10}):
                album_key, artist_key = str(t.get("parentRatingKey", "")), str(t.get("grandparentRatingKey", ""))
                tracks.append(
                    LibraryTrack(
                        rating_key=str(t["ratingKey"]),
                        guid=t.get("guid", ""),
                        title=t.get("title", ""),
                        artist=t.get("originalTitle") or t.get("grandparentTitle", ""),
                        album_artist=t.get("grandparentTitle", ""),
                        album=t.get("parentTitle", ""),
                        album_key=album_key,
                        artist_key=artist_key,
                        track_genres=_tags(t),
                        album_genres=albums.get(album_key, ()),
                        artist_genres=artists.get(artist_key, ()),
                        popularity=t.get("ratingCount"),
                    )
                )
        return tracks

    def ratings(self, url: str, token: str) -> dict[str, float]:
        ratings = {}
        for section in self._music_sections(url, token):
            for libtype in (8, 9, 10):
                params = {"type": libtype, "userRating>": 0}  # sent as "userRating>>=0": rated items only
                for item in self._pages(url, f"/library/sections/{section}/all", token, params):
                    ratings[str(item["ratingKey"])] = float(item["userRating"])
        return ratings

    def playlist(self, url: str, token: str, playlist_id: str) -> PlexPlaylist | None:
        try:
            items = self._pms("GET", url, f"/playlists/{quote(playlist_id)}", token).get("Metadata", [])
        except NotFound:
            return None
        if not items:
            return None
        return _playlist(items[0])

    def playlists(self, url: str, token: str) -> list[PlexPlaylist]:
        return [_playlist(p) for p in self._pms("GET", url, "/playlists", token, {"playlistType": "audio"}).get("Metadata", [])]

    def delete_playlist(self, url: str, token: str, playlist_id: str) -> None:
        self._pms("DELETE", url, f"/playlists/{quote(playlist_id)}", token)

    def create_playlist(self, url: str, token: str, machine_id: str, title: str, rating_keys: list[str]) -> str:
        params = {"uri": _metadata_uri(machine_id, rating_keys), "type": "audio", "title": title, "smart": 0}
        return str(self._pms("POST", url, "/playlists", token, params)["Metadata"][0]["ratingKey"])

    def clear_playlist(self, url: str, token: str, playlist_id: str) -> None:
        self._pms("DELETE", url, f"/playlists/{quote(playlist_id)}/items", token)

    def add_to_playlist(self, url: str, token: str, machine_id: str, playlist_id: str, rating_keys: list[str]) -> None:
        params = {"uri": _metadata_uri(machine_id, rating_keys)}
        self._pms("PUT", url, f"/playlists/{quote(playlist_id)}/items", token, params)

    def set_playlist_summary(self, url: str, token: str, playlist_id: str, summary: str) -> None:
        self._pms("PUT", url, f"/playlists/{quote(playlist_id)}", token, {"summary": summary})

    # --- Tautulli: read commands only, as its API key is an admin key that can delete history ---

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
