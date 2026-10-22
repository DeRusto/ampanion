"""In-memory stand-ins for everything outside ampanion. Tests set up the world here, then drive the app."""

import itertools
from dataclasses import dataclass
from datetime import datetime, timedelta

from ampanion.gateway import Pin, PlexAccount, PlexResource, TokenExpired, Unreachable


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
        self._ids = itertools.count(1)

    # --- set up the world ---

    def add_account(self, username: str, token: str, account_id: int | None = None) -> PlexAccount:
        account = PlexAccount(id=account_id or len(self._accounts_by_token) + 1, username=username)
        self._accounts_by_token[token] = account
        return account

    def expire_token(self, token: str) -> None:
        del self._accounts_by_token[token]

    def add_server(self, owner_token: str, name: str = "Plex Server", machine_id: str | None = None) -> PlexResource:
        """A Plex Media Server owned by the account holding owner_token."""
        server = PlexResource(
            name=name, client_identifier=machine_id or f"machine-{next(self._ids)}", provides="server", owned=True
        )
        self._resources_by_account.setdefault(self._accounts_by_token[owner_token].id, []).append(server)
        return server

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


class FakeClock:
    def __init__(self, now: datetime):
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, **kwargs) -> None:
        self._now += timedelta(**kwargs)
