"""Reset ampanion: Settings wipes everything ampanion stored and returns it to first run, leaving Plex alone."""

from tests.test_listeners import add_listener
from tests.test_on_repeat import PAL, background, play, on_repeat, pal, refresh  # noqa: F401 (pal is a fixture)
from tests.test_sign_in import csrf_from


def published(client, gateway, clock):
    for n in range(1, 6):
        play(gateway, clock, 8, f"t{n}", times=2, days_ago=n)
    refresh(client)
    return {p.id: p.title for p in gateway.playlists_of(PAL)}  # On Repeat, and Deep Cuts


def reset(client):
    background(client)  # Reset waits for any Refresh to finish
    page = client.get("/settings/reset")
    return client.post("/settings/reset", data={"csrf_token": csrf_from(page.text)}, follow_redirects=False)


def test_reset_asks_for_confirmation_first(client, gateway, clock, pal):
    assert 'href="/settings/reset"' in client.get("/settings").text

    page = client.get("/settings/reset")

    assert "Reset ampanion?" in page.text
    assert client.app.state.store.listeners()  # nothing wiped yet
    assert client.get("/listeners/8").status_code == 200


def test_after_reset_ampanion_is_at_first_run_and_the_next_qualifying_sign_in_becomes_owner(client, gateway, sign_in, pal):
    response = reset(client)

    assert response.headers["location"] == "/signin"
    assert client.get("/", follow_redirects=False).headers["location"] == "/signin"
    gateway.add_account("newcomer", token="new-token", account_id=77)
    gateway.add_server(owner_token="new-token", name="Attic")
    sign_in(client, "new-token")
    store = client.app.state.store
    assert (store.owner_account_id, store.owner_username) == (77, "newcomer")
    assert store.listeners() == [] and store.refreshes() == []
    assert store.server_name == "Attic"  # the server was forgotten, and the new Owner's one was chosen


def test_reset_leaves_playlists_on_plex(client, gateway, clock, pal):
    playlists = published(client, gateway, clock)

    reset(client)

    assert {p.id: p.title for p in gateway.playlists_of(PAL)} == playlists


def test_after_reset_on_the_same_server_a_listener_takes_over_their_playlists(client, gateway, clock, sign_in, pal):
    playlists = published(client, gateway, clock)
    reset(client)

    sign_in(client, "owner-token")  # the same server is chosen again on Home
    add_listener(client, 8)
    refresh(client)

    assert {p.id: p.title for p in gateway.playlists_of(PAL)} == playlists  # none duplicated
    assert on_repeat(gateway).items == ["t1", "t2", "t3", "t4", "t5"]
