"""Choosing the Plex server: the Home checklist, reaching the server, Settings, and the owned=1 sign-in check."""

from ampanion.gateway import PlexConnection
from tests.test_sign_in import csrf_from


def test_home_shows_the_getting_started_checklist(client, signed_in):
    html = client.get("/").text

    assert "Getting started" in html
    assert "Choose your Plex server" in html
    assert "Connect Tautulli (optional)" in html
    assert "Pick at least one Listener" in html


def test_the_only_owned_server_is_picked_automatically_and_checked(client, signed_in):
    html = client.get("/").text

    assert '<li class="done">Choose your Plex server' in html
    assert "Plex Server" in client.get("/settings").text


def owner_with_two_servers(gateway):
    gateway.add_account("owner", token="owner-token")
    gateway.add_server(owner_token="owner-token", name="Basement", machine_id="m1")
    gateway.add_server(owner_token="owner-token", name="Old laptop", machine_id="m2")


def test_only_servers_the_owner_owns_are_offered(client, gateway, sign_in):
    owner_with_two_servers(gateway)
    gateway.add_account("friend", token="friend-token")
    gateway.share_server(gateway.add_server(owner_token="friend-token", name="Friend's", machine_id="f1"), "owner-token")
    sign_in(client, "owner-token")

    html = client.get("/").text

    assert '<li class="todo">Choose your Plex server' in html
    assert 'value="m1">Basement' in html and 'value="m2">Old laptop' in html
    assert "Friend" not in html


def test_choosing_one_of_several_servers_checks_the_item(client, gateway, sign_in):
    owner_with_two_servers(gateway)
    sign_in(client, "owner-token")
    csrf = csrf_from(client.get("/").text)

    response = client.post("/server", data={"csrf_token": csrf, "machine_id": "m2"})

    assert '<li class="done">Choose your Plex server' in response.text
    assert "Old laptop" in client.get("/settings").text


def test_a_server_the_owner_doesnt_own_cant_be_chosen(client, gateway, sign_in):
    owner_with_two_servers(gateway)
    gateway.add_account("friend", token="friend-token")
    gateway.share_server(gateway.add_server(owner_token="friend-token", machine_id="f1"), "owner-token")
    sign_in(client, "owner-token")
    csrf = csrf_from(client.get("/").text)

    assert client.post("/server", data={"csrf_token": csrf, "machine_id": "f1"}).status_code == 400


def test_there_is_no_way_to_switch_server_in_place(client, gateway, sign_in):
    owner_with_two_servers(gateway)
    sign_in(client, "owner-token")
    csrf = csrf_from(client.get("/").text)
    client.post("/server", data={"csrf_token": csrf, "machine_id": "m1"})

    assert client.post("/server", data={"csrf_token": csrf, "machine_id": "m2"}).status_code == 409
    assert "Basement" in client.get("/settings").text


LOCAL = PlexConnection(uri="http://192.168.1.20:32400", local=True, relay=False)
LOCAL_2 = PlexConnection(uri="http://10.0.0.5:32400", local=True, relay=False)
REMOTE = PlexConnection(uri="https://1-2-3-4.abc.plex.direct:32400", local=False, relay=False)
RELAY = PlexConnection(uri="https://relay.plex.direct:8443", local=False, relay=True)


def shown_server_url(client):
    """The server URL Settings says ampanion reaches Plex at, or None if it couldn't reach it."""
    html = client.get("/settings").text
    return None if "couldn't be reached" in html else html.split("</b>\n    at ")[1].split("</p>")[0]


def server_url_once_picked(client, gateway, sign_in, connections, answering):
    gateway.add_account("owner", token="owner-token")
    gateway.add_server(owner_token="owner-token", machine_id="m1", connections=connections, answering=answering)
    sign_in(client, "owner-token")  # lands on Home, which picks the only server
    return shown_server_url(client)


def test_local_connections_are_tried_before_remote(client, gateway, sign_in):
    answering = [REMOTE.uri, LOCAL.uri, LOCAL_2.uri]
    assert server_url_once_picked(client, gateway, sign_in, [REMOTE, LOCAL, LOCAL_2], answering) == LOCAL.uri


def test_the_first_connection_that_answers_is_kept(client, gateway, sign_in):
    answering = [LOCAL_2.uri, REMOTE.uri]
    assert server_url_once_picked(client, gateway, sign_in, [LOCAL, LOCAL_2, REMOTE], answering) == LOCAL_2.uri


def test_remote_is_used_when_no_local_connection_answers_but_relay_never_is(client, gateway, sign_in):
    answering = [RELAY.uri, REMOTE.uri]
    assert server_url_once_picked(client, gateway, sign_in, [RELAY, LOCAL, REMOTE], answering) == REMOTE.uri


def test_a_server_only_reachable_by_relay_is_chosen_but_left_unreached(client, gateway, sign_in):
    assert server_url_once_picked(client, gateway, sign_in, [RELAY], [RELAY.uri]) is None
    assert "couldn't be reached; set its URL below" in client.get("/settings").text


def test_a_connection_answering_as_another_server_is_skipped(client, gateway, sign_in):
    gateway.serve(LOCAL.uri, "someone-elses")
    assert server_url_once_picked(client, gateway, sign_in, [LOCAL, REMOTE], [REMOTE.uri]) == REMOTE.uri


def test_settings_show_the_chosen_server_and_the_signed_in_owner(client, gateway, sign_in):
    gateway.add_account("dee", token="owner-token")
    gateway.add_server(owner_token="owner-token", name="Basement", connections=[LOCAL])
    sign_in(client, "owner-token")

    html = client.get("/settings").text

    assert f"<b>Basement</b>\n    at {LOCAL.uri}" in html
    assert "Signed in as dee" in html


def test_a_manual_url_in_settings_overrides_the_discovered_connection(client, gateway, sign_in):
    gateway.add_account("owner", token="owner-token")
    server = gateway.add_server(owner_token="owner-token", connections=[LOCAL])
    gateway.serve("http://plex.example:32400", server.client_identifier)
    sign_in(client, "owner-token")
    csrf = csrf_from(client.get("/settings").text)

    html = client.post("/settings/server-url", data={"csrf_token": csrf, "url": "http://plex.example:32400"}).text
    assert "at http://plex.example:32400" in html
    assert shown_server_url(client) == "http://plex.example:32400"

    client.post("/settings/server-url", data={"csrf_token": csrf, "url": ""})  # cleared: back to the discovered one
    assert shown_server_url(client) == LOCAL.uri


def test_a_manual_url_must_answer_as_the_chosen_server(client, gateway, sign_in):
    owner_with_two_servers(gateway)
    gateway.serve("http://laptop.example:32400", "m2")
    sign_in(client, "owner-token")
    client.post("/server", data={"csrf_token": csrf_from(client.get("/").text), "machine_id": "m1"})
    csrf = csrf_from(client.get("/settings").text)

    other = client.post("/settings/server-url", data={"csrf_token": csrf, "url": "http://laptop.example:32400"})
    nobody = client.post("/settings/server-url", data={"csrf_token": csrf, "url": "http://nothing.example:32400"})

    assert other.status_code == 400 and nobody.status_code == 400
    assert shown_server_url(client) == "http://m1.lan:32400"


def test_a_manual_url_must_be_http(client, signed_in):
    csrf = csrf_from(client.get("/settings").text)

    response = client.post("/settings/server-url", data={"csrf_token": csrf, "url": "javascript:alert(1)"})

    assert response.status_code == 400


def test_once_a_server_is_chosen_the_owner_must_still_own_it_to_sign_in(client, gateway, sign_in):
    owner_with_two_servers(gateway)
    sign_in(client, "owner-token")
    client.post("/server", data={"csrf_token": csrf_from(client.get("/").text), "machine_id": "m1"})
    gateway.disown_server("owner-token", "m1")  # still owns m2, but not the chosen server
    client.cookies.clear()

    response = sign_in(client, "owner-token")

    assert response.status_code == 403
    assert "doesn&#39;t own ampanion&#39;s Plex server" in response.text
    assert client.get("/", follow_redirects=False).headers["location"] == "/signin"


def test_the_owner_still_owning_the_chosen_server_signs_in_again(client, gateway, sign_in, signed_in):
    client.get("/")
    client.cookies.clear()

    assert sign_in(client, "owner-token").url.path == "/"
