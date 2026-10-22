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


class Gateway(Protocol):
    def check_token(self, token: str) -> PlexAccount:
        """Return the plex.tv account the token belongs to. Raises TokenExpired on 401."""
        ...


class PlexGateway:
    """The real Gateway, over HTTP."""

    def __init__(self, client_identifier: str, http: httpx.Client | None = None):
        self._client_identifier = client_identifier
        self._http = http or httpx.Client(timeout=30)

    def check_token(self, token: str) -> PlexAccount:
        try:
            response = self._http.get(
                "https://plex.tv/api/v2/user",
                headers={
                    "Accept": "application/json",
                    "X-Plex-Token": token,
                    "X-Plex-Client-Identifier": self._client_identifier,
                    "X-Plex-Product": "ampanion",
                },
            )
        except httpx.HTTPError as e:
            raise Unreachable(f"plex.tv: {e}") from e
        if response.status_code == 401:
            raise TokenExpired("plex.tv rejected the token")
        if response.is_error:
            raise Unreachable(f"plex.tv answered {response.status_code}")
        body = response.json()
        return PlexAccount(id=body["id"], username=body["username"])
