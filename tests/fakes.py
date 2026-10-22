"""In-memory stand-ins for everything outside ampanion. Tests set up the world here, then drive the app."""

import itertools
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from ampanion.gateway import Pin, PlexAccount, PlexConnection, PlexResource, TokenExpired, Unreachable


@dataclass
class FakePin:
    pin: Pin
    token: str | None = None


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

    def approve_pin(self, code: str, token: str) -> None:
        """The person signs in on Plex's auth page, which attaches their token to the PIN."""
        self.pin_by_code(code).token = token

    def pin_by_code(self, code: str) -> FakePin:
        return next(p for p in self._pins.values() if p.pin.code == code)

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


class FakeClock:
    def __init__(self, now: datetime):
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, **kwargs) -> None:
        self._now += timedelta(**kwargs)
