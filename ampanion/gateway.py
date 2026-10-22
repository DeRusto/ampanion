"""The Gateway: ampanion's only route to plex.tv, the Plex server and Tautulli.

Everything outside the process goes through a Gateway, so tests swap in an
in-memory fake (tests/fakes.py) and drive the rest of the app for real.
Each operation raises a GatewayError subclass the caller can tell apart.
"""

from dataclasses import dataclass
from typing import Protocol
from xml.etree import ElementTree

import httpx


class GatewayError(Exception):
    """Any failure talking to plex.tv, the Plex server or Tautulli."""


class TokenExpired(GatewayError):
    """plex.tv rejected the Owner's token (401): Refreshes must pause until the Owner signs in again."""


class Unreachable(GatewayError):
    """The service could not be reached or answered with an unexpected error."""


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
