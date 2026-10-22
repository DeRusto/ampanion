"""Expired Plex sign-in: a dead Owner token pauses every Refresh until the Owner signs in again."""

from tests.test_on_repeat import PAL, background, play, on_repeat, pal, refresh  # noqa: F401
from tests.test_schedule import refresh_all, until

EXPIRED = "Plex sign-in expired"


def play_five(gateway, clock):
    for n in range(1, 6):
        play(gateway, clock, 8, f"t{n}", times=2, days_ago=n)


def expire_before_the_nightly_run(client, gateway, clock):
    background(client)
    gateway.expire_token("owner-token")
    until(clock, 9, day=6)
    background(client)


def expired_records(client):
    return client.get("/").text.count(EXPIRED + "</span>")


def test_a_dead_token_at_startup_pauses_refreshes_with_a_banner(start_app, client, gateway, clock, pal):
    background(client)
    gateway.expire_token("owner-token")

    with start_app() as restarted:
        restarted.cookies = client.cookies
        html = restarted.get("/").text  # the Owner's session is still good
        assert "Refreshes are paused" in html and 'href="/signin/plex"' in html and "Sign in again" in html
        assert "Refreshes are paused" in restarted.get("/listeners/8").text
        assert "Refreshes are paused" in restarted.get("/settings").text
        assert expired_records(restarted) == 1
        assert 'class="todo">Choose your Plex server' in html  # the checklist's Plex item unchecks


def test_a_dead_token_before_the_nightly_run_touches_no_playlist(client, gateway, clock, pal):
    play_five(gateway, clock)
    refresh(client)
    before = list(on_repeat(gateway).items)
    play(gateway, clock, 8, "t9", times=9)
    gateway.expire_token("owner-token")

    until(clock, 9, day=6)
    background(client)
    until(clock, 9, day=7)
    background(client)

    assert on_repeat(gateway).items == before
    assert expired_records(client) == 1
    assert refresh_all(client).status_code == 409  # Refresh now is paused too
    background(client)
    assert expired_records(client) == 1


def test_a_401_during_a_refresh_writes_one_record_not_one_per_listener(client, gateway, clock, pal):
    from tests.test_listeners import add_listener

    add_listener(client, 42)
    background(client)
    before = len(client.get("/").text.split("<tr class=")) - 1
    gateway.expire_token("owner-token")  # dies after the nightly check: the first Owner-token call fails

    refresh_all(client)
    background(client)

    html = client.get("/").text
    assert expired_records(client) == 1
    assert len(html.split("<tr class=")) - 1 == before + 1  # only the expired record
    assert "Refreshes are paused" in html


def test_a_401_on_a_listeners_own_token_is_not_an_expired_sign_in(client, gateway, clock, pal):
    play_five(gateway, clock)
    background(client)
    gateway.rejected_tokens.add(PAL)  # Pal's server token stops working; the Owner's is fine

    refresh(client)

    assert "Refreshes are paused" not in client.get("/").text


def test_signing_in_again_as_the_owner_resumes_with_one_catch_up_run(client, gateway, clock, pal, sign_in):
    background(client)
    play_five(gateway, clock)
    gateway.expire_token("owner-token")
    until(clock, 9, day=6)
    background(client)
    assert on_repeat(gateway) is None

    gateway.add_account("dee", token="new-owner-token", account_id=42)
    response = sign_in(client, "new-owner-token")

    assert response.status_code == 200 and "Refreshes are paused" not in response.text
    background(client)
    assert on_repeat(gateway).items == ["t1", "t2", "t3", "t4", "t5"]
    background(client)
    assert client.get("/listeners/8?tab=history").text.count("<tr class=") == 2  # first Refresh, then one catch-up


def test_signing_in_again_as_another_account_is_refused(client, gateway, clock, pal, sign_in):
    expire_before_the_nightly_run(client, gateway, clock)
    gateway.add_account("someone", token="other-token", account_id=99)
    gateway.add_server(owner_token="other-token")

    response = sign_in(client, "other-token")

    assert response.status_code == 403
    assert "Refreshes are paused" in client.get("/").text  # still paused, and the Owner's session still good


def test_signing_in_again_when_no_longer_owning_the_server_is_refused(client, gateway, clock, pal, sign_in):
    expire_before_the_nightly_run(client, gateway, clock)
    gateway.add_account("dee", token="new-owner-token", account_id=42)
    gateway.disown_server("new-owner-token", client.app.state.store.server_machine_id)

    response = sign_in(client, "new-owner-token")

    assert response.status_code == 403
    assert "Refreshes are paused" in client.get("/").text
