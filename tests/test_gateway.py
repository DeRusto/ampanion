"""The Gateway's contract: the fake and the real one must agree on results and typed errors."""

import httpx
import pytest

from ampanion.gateway import GatewayError, PlexAccount, PlexConnection, PlexGateway, PlexResource, TokenExpired, Unreachable
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
            {
                "name": "Basement",
                "clientIdentifier": "m1",
                "provides": "server",
                "owned": True,
                "connections": [
                    {"uri": "http://192.168.1.20:32400", "local": True, "relay": False},
                    {"uri": "https://relay.plex.direct:8443", "local": False, "relay": True},
                ],
            },
            {"name": "Phone", "clientIdentifier": "p1", "provides": "client,player", "owned": True},
        ]
    )
    resources = real.resources("owner-token")
    assert requests[0].headers["X-Plex-Token"] == "owner-token"
    assert requests[0].url.params["includeRelay"] == "1"
    connections = (
        PlexConnection(uri="http://192.168.1.20:32400", local=True, relay=False),
        PlexConnection(uri="https://relay.plex.direct:8443", local=False, relay=True),
    )
    assert resources[0] == PlexResource(
        name="Basement", client_identifier="m1", provides="server", owned=True, connections=connections
    )

    fake = fake_with_owner()
    fake.add_server(owner_token="owner-token", name="Basement", machine_id="m1", connections=list(connections))
    assert fake.resources("owner-token") == [resources[0]]


def test_a_plex_server_names_its_machine_id_or_is_unreachable():
    real, requests = recording_plex_tv({"MediaContainer": {"machineIdentifier": "m1"}})
    assert real.server_identity("http://192.168.1.20:32400", "owner-token") == "m1"
    assert str(requests[0].url) == "http://192.168.1.20:32400/identity"
    assert requests[0].headers["X-Plex-Token"] == "owner-token"
    with pytest.raises(Unreachable):
        plex_tv(503).server_identity("http://192.168.1.20:32400", "owner-token")

    fake = fake_with_owner()
    fake.add_server(owner_token="owner-token", machine_id="m1")
    assert fake.server_identity("http://m1.lan:32400", "owner-token") == "m1"
    with pytest.raises(Unreachable):
        fake.server_identity("http://elsewhere:32400", "owner-token")


def xml_plex_tv(body: str) -> tuple[PlexGateway, list[httpx.Request]]:
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, text=body, headers={"Content-Type": "application/xml"})

    return PlexGateway(client_identifier="test-client", http=httpx.Client(transport=httpx.MockTransport(handle))), requests


def test_users_are_the_owners_friends_and_home_members_by_display_name():
    real, requests = xml_plex_tv(
        '<MediaContainer><User id="7" title="Kid" username="" />'
        '<User id="8" title="Pal" username="pal1"><Server machineIdentifier="m1" /></User></MediaContainer>'
    )
    expected = [PlexAccount(id=7, username="Kid"), PlexAccount(id=8, username="Pal")]
    assert real.users("owner-token") == expected
    assert str(requests[0].url) == "https://plex.tv/api/users"
    assert requests[0].headers["X-Plex-Token"] == "owner-token"

    fake = fake_with_owner()
    fake.add_user("owner-token", "Kid", account_id=7)
    fake.add_user("owner-token", "Pal", account_id=8)
    assert fake.users("owner-token") == expected


def test_shared_tokens_are_the_server_tokens_of_users_with_a_share_on_that_server():
    real, requests = xml_plex_tv(
        '<MediaContainer><SharedServer userID="8" username="pal1" accessToken="pal-server-token" /></MediaContainer>'
    )
    assert real.shared_tokens("owner-token", "m1") == {8: "pal-server-token"}
    assert str(requests[0].url) == "https://plex.tv/api/servers/m1/shared_servers"
    with pytest.raises(Unreachable):
        xml_plex_tv("<not xml")[0].shared_tokens("owner-token", "m1")
    with pytest.raises(TokenExpired):
        plex_tv(401).shared_tokens("owner-token", "m1")

    fake = fake_with_owner()
    server = fake.add_server(owner_token="owner-token", machine_id="m1")
    fake.add_user("owner-token", "Kid", account_id=7)
    fake.add_user("owner-token", "Pal", account_id=8, share=server)
    assert fake.shared_tokens("owner-token", "m1") == {8: "Pal-server-token"}
