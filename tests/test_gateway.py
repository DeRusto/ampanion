"""The Gateway's contract: the fake and the real one must agree on results and typed errors."""

import httpx
import pytest

from ampanion.gateway import GatewayError, PlexAccount, PlexGateway, PlexResource, TokenExpired, Unreachable
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


def recording_plex_tv(body) -> tuple[PlexGateway, list[httpx.Request]]:
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=body)

    return PlexGateway(client_identifier="test-client", http=httpx.Client(transport=httpx.MockTransport(handle))), requests


def test_a_sign_in_pin_is_strong_and_its_token_is_read_back_once_plex_attaches_it():
    real, requests = recording_plex_tv({"id": 7, "code": "abcd"})
    assert real.create_pin().code == "abcd"
    assert requests[0].method == "POST" and requests[0].url.params["strong"] == "true"
    assert requests[0].headers["X-Plex-Client-Identifier"] == "test-client"

    assert plex_tv(200, {"id": 7, "authToken": None}).pin_token(7) is None
    assert plex_tv(200, {"id": 7, "authToken": "tok"}).pin_token(7) == "tok"

    fake = fake_with_owner()
    pin = fake.create_pin()
    assert fake.pin_token(pin.id) is None
    fake.approve_pin(pin.code, "owner-token")
    assert fake.pin_token(pin.id) == "owner-token"


def test_resources_say_which_servers_the_account_owns():
    real, requests = recording_plex_tv(
        [
            {"name": "Basement", "clientIdentifier": "m1", "provides": "server", "owned": True},
            {"name": "Phone", "clientIdentifier": "p1", "provides": "client,player", "owned": True},
        ]
    )
    resources = real.resources("owner-token")
    assert requests[0].headers["X-Plex-Token"] == "owner-token"
    assert resources[0] == PlexResource(name="Basement", client_identifier="m1", provides="server", owned=True)

    fake = fake_with_owner()
    fake.add_server(owner_token="owner-token", name="Basement", machine_id="m1")
    assert fake.resources("owner-token") == [resources[0]]
