"""In-memory stand-ins for everything outside ampanion. Tests set up the world here, then drive the app."""

from datetime import datetime, timedelta

from ampanion.gateway import PlexAccount, TokenExpired


class FakeGateway:
    """Models plex.tv, the Plex server and Tautulli in memory. Later tickets grow it alongside the Gateway."""

    def __init__(self):
        self._accounts_by_token: dict[str, PlexAccount] = {}

    # --- set up the world ---

    def add_account(self, username: str, token: str, account_id: int | None = None) -> PlexAccount:
        account = PlexAccount(id=account_id or len(self._accounts_by_token) + 1, username=username)
        self._accounts_by_token[token] = account
        return account

    def expire_token(self, token: str) -> None:
        del self._accounts_by_token[token]

    # --- Gateway operations ---

    def check_token(self, token: str) -> PlexAccount:
        try:
            return self._accounts_by_token[token]
        except KeyError:
            raise TokenExpired("plex.tv rejected the token") from None


class FakeClock:
    def __init__(self, now: datetime):
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, **kwargs) -> None:
        self._now += timedelta(**kwargs)
