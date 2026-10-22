"""The Gateway's contract: the fake and the real one must agree on results and typed errors."""

import httpx
import pytest

from ampanion.gateway import GatewayError, PlexAccount, PlexGateway, TokenExpired, Unreachable
from tests.fakes import FakeGateway


def plex_tv(status: int, body: dict | None = None) -> PlexGateway:
    transport = httpx.MockTransport(lambda request: httpx.Response(status, json=body or {}))
    return PlexGateway(client_identifier="test-client", http=httpx.Client(transport=transport))


def fake_with_owner() -> FakeGateway:
    gateway = FakeGateway()
    gateway.add_account("owner", token="owner-token", account_id=42)
    return gateway


def test_a_valid_token_names_its_account():
    expected = PlexAccount(id=42, username="owner")

    assert fake_with_owner().check_token("owner-token") == expected
    assert plex_tv(200, {"id": 42, "username": "owner"}).check_token("owner-token") == expected


def test_an_expired_owner_token_is_told_apart_from_other_failures():
    fake = fake_with_owner()
    fake.expire_token("owner-token")

    for gateway in (fake, plex_tv(401)):
        with pytest.raises(TokenExpired):
            gateway.check_token("owner-token")


def test_other_failures_are_gateway_errors_but_not_token_expiry():
    with pytest.raises(Unreachable):
        plex_tv(503).check_token("owner-token")

    assert issubclass(TokenExpired, GatewayError)
    assert issubclass(Unreachable, GatewayError)
    assert not issubclass(Unreachable, TokenExpired)
