"""Picking Listeners: the sidebar of Plex users, making one a Listener, their page and turning them off."""

import pytest

from tests.test_sign_in import csrf_from

NO_SHARE = "No library share on this server"


@pytest.fixture
def home(client, gateway, sign_in):
    """Signed in as the Owner "dee" (account 42) of one server, shared with "Pal" (8) but not with "Stranger" (9)."""
    gateway.add_account("dee", token="owner-token", account_id=42)
    server = gateway.add_server(owner_token="owner-token", name="Basement")
    gateway.add_user("owner-token", "Pal", account_id=8, share=server)
    gateway.add_user("owner-token", "Stranger", account_id=9)
    return sign_in(client, "owner-token")


def sidebar(html: str) -> str:
    return html.split('<nav class="sidebar"')[1].split("</nav>")[0]


def add_listener(client, account_id):
    csrf = csrf_from(client.get("/").text)
    return client.post("/listeners", data={"csrf_token": csrf, "account_id": account_id})


def test_the_sidebar_lists_home_every_plex_user_including_the_owner_and_settings(client, home):
    nav = sidebar(client.get("/settings").text)

    assert nav.index(">Home<") < nav.index("dee") < nav.index("Pal") < nav.index("Stranger") < nav.index(">Settings<")


def test_users_with_a_share_show_plus_and_users_without_one_show_as_unavailable(client, home):
    nav = sidebar(client.get("/").text)

    assert 'aria-label="Make dee a Listener"' in nav
    assert 'aria-label="Make Pal a Listener"' in nav
    assert 'aria-label="Make Stranger a Listener"' not in nav
    stranger = nav.split("Stranger")[1].split("</span>")[0]
    assert NO_SHARE in stranger


def test_clicking_plus_makes_the_user_a_listener_and_shows_their_page(client, home):
    response = add_listener(client, 8)

    assert response.url.path == "/listeners/8"
    html = response.text
    assert "<h1>Pal</h1>" in html
    assert "Refresh now" in html and "disabled" in html.split("Refresh now")[0].rsplit("<button", 1)[1]
    assert "Turn off" in html
    for tab in ("Mixes", "Playlists", "History"):
        assert f">{tab}<" in html
    assert 'href="/listeners/8"' in sidebar(client.get("/").text)
    assert 'aria-label="Make Pal a Listener"' not in sidebar(client.get("/").text)


def test_the_owner_can_be_a_listener_without_a_share_of_their_own_server(client, home):
    # The Owner has no shared_servers entry: they use the Owner token directly.
    assert add_listener(client, 42).url.path == "/listeners/42"
    assert "<h1>dee</h1>" in client.get("/listeners/42").text


def test_a_user_without_a_share_cant_be_made_a_listener(client, home):
    assert add_listener(client, 9).status_code == 400
    assert add_listener(client, 1234).status_code == 400
    assert client.get("/listeners/9").status_code == 404


def test_turn_off_asks_first_then_removes_the_listener(client, home):
    add_listener(client, 8)

    confirm = client.get("/listeners/8/turn-off").text
    assert "Their Playlists will be deleted from Plex" in confirm
    assert client.get("/listeners/8").status_code == 200  # asking changed nothing

    response = client.post("/listeners/8/turn-off", data={"csrf_token": csrf_from(confirm)})

    assert response.url.path == "/"
    assert client.get("/listeners/8").status_code == 404
    assert 'aria-label="Make Pal a Listener"' in sidebar(response.text)


def test_turning_off_needs_a_csrf_token(client, home):
    add_listener(client, 8)

    assert client.post("/listeners/8/turn-off").status_code == 403
    assert client.get("/listeners/8").status_code == 200


def test_the_listener_checklist_item_ticks_and_setup_completes_once_there_is_a_listener(client, home):
    html = client.get("/").text
    assert '<li class="todo">Pick at least one Listener' in html

    add_listener(client, 42)
    html = client.get("/").text

    # Tautulli is optional: a server and a Listener complete setup, so the checklist goes away.
    assert "Getting started" not in html

    client.post("/listeners/42/turn-off", data={"csrf_token": csrf_from(html)})
    assert '<li class="todo">Pick at least one Listener' in client.get("/").text


def test_listeners_stay_listeners_after_a_restart(start_app, gateway, sign_in, home, client):
    add_listener(client, 8)

    with start_app() as again:
        sign_in(again, "owner-token")
        assert "<h1>Pal</h1>" in again.get("/listeners/8").text


def test_the_sidebar_still_shows_listeners_when_plex_cant_be_reached(client, gateway, home):
    add_listener(client, 8)
    gateway.expire_token("owner-token")  # plex.tv now refuses every Owner call

    nav = sidebar(client.get("/settings").text)

    assert 'href="/listeners/8"' in nav
    assert "Couldn&#39;t reach Plex to list its users" in nav
