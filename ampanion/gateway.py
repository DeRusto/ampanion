"""The Gateway: ampanion's only route to plex.tv, the Plex server and Tautulli.

Everything outside the process goes through a Gateway, so tests swap in an
in-memory fake (tests/fakes.py) and drive the rest of the app for real.
Each operation raises a GatewayError subclass the caller can tell apart.
"""

from dataclasses import dataclass
from typing import Protocol

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
class PlexResource:
    """A device on the account, as plex.tv lists it. `provides` is comma-separated, e.g. "server"."""

    name: str
    client_identifier: str
    provides: str
    owned: bool


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


class PlexGateway:
    """The real Gateway, over HTTP."""

    def __init__(self, client_identifier: str, http: httpx.Client | None = None):
        self._client_identifier = client_identifier
        self._http = http or httpx.Client(timeout=30)

    def _plex_tv(self, method: str, path: str, token: str | None = None, **kwargs):
        headers = {
            "Accept": "application/json",
            "X-Plex-Client-Identifier": self._client_identifier,
            "X-Plex-Product": "ampanion",
        }
        if token:
            headers["X-Plex-Token"] = token
        try:
            response = self._http.request(method, f"https://plex.tv/api/v2{path}", headers=headers, **kwargs)
        except httpx.HTTPError as e:
            raise Unreachable(f"plex.tv: {e}") from e
        if response.status_code == 401:
            raise TokenExpired("plex.tv rejected the token")
        if response.is_error:
            raise Unreachable(f"plex.tv answered {response.status_code}")
        return response.json()

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
                name=r["name"], client_identifier=r["clientIdentifier"], provides=r["provides"], owned=bool(r["owned"])
            )
            for r in self._plex_tv("GET", "/resources", token)
        ]
