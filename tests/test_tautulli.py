"""Tautulli: connected in Settings, it adds Skips, and often-skipped music drops out of every Mix but On Repeat."""

from datetime import timedelta

import pytest

from tests.test_listeners import add_listener
from tests.test_on_repeat import PAL, background, play, on_repeat, refresh
from tests.test_reset import reset
from tests.test_sign_in import csrf_from

TAUTULLI, KEY = "http://tautulli:8181", "s3cret-admin-key-9876"


@pytest.fixture
def pal(client, gateway, sign_in):
    """Signed in as the Owner (42), with "Pal" (8) a Listener and an empty library; Tautulli runs but isn't connected."""
    gateway.add_account("dee", token="owner-token", account_id=42)
    server = gateway.add_server(owner_token="owner-token", name="Basement")
    gateway.add_user("owner-token", "Pal", account_id=8, share=server)
    gateway.add_tautulli(TAUTULLI, KEY, server.client_identifier)
    sign_in(client, "owner-token")
    add_listener(client, 8)
    background(client)
    return 8


def connect(client, url=TAUTULLI, api_key=KEY):
    csrf = csrf_from(client.get("/settings").text)
    return client.post("/settings/tautulli", data={"csrf_token": csrf, "url": url, "api_key": api_key})


def remove(client):
    csrf = csrf_from(client.get("/settings").text)
    return client.post("/settings/tautulli/remove", data={"csrf_token": csrf})


def skip(gateway, clock, key, times=1, user_id=8, percent=20, hours_ago=1):
    for i in range(times):
        gateway.tautulli_listen(user_id, key, clock.now() - timedelta(hours=hours_ago, minutes=i), percent)


def tracks(gateway, artist, count, plays=0):
    keys = [f"{artist}-{n}" for n in range(count)]
    for k in keys:
        gateway.add_track(k, artist=artist, album=f"{artist} LP", track_genres=("Rock",))
    return keys


def playlist(gateway, title, token=PAL):
    return next((p for p in gateway.playlists_of(token) if p.title == title), None)


def deep_cuts_world(client, gateway, clock, account_id=8, history_account_id=8):
    """One Top Artist with seven unplayed tracks; Deep Cuts takes them all."""
    keys = tracks(gateway, "Band", 8)
    play(gateway, clock, history_account_id, keys[0], times=10)
    csrf = csrf_from(client.get(f"/listeners/{account_id}").text)
    data = {"csrf_token": csrf, "enabled": "on", "length": "30", "tracks_per_artist": "10"}
    client.post(f"/listeners/{account_id}/mixes/deep_cuts", data=data)
    return keys


@pytest.fixture
def running(client, gateway, signed_in):
    """Signed in, with the Plex server chosen and Tautulli running for it, not yet connected."""
    client.get("/")  # the only server is chosen here
    gateway.add_tautulli(TAUTULLI, KEY, client.app.state.store.server_machine_id)


# --- Settings ---


def test_tautulli_for_the_chosen_plex_server_is_saved_and_the_key_never_shown_in_full(client, running):
    assert '<li class="todo">Connect Tautulli' in client.get("/").text

    html = connect(client).text

    assert TAUTULLI in html and KEY not in html and "9876" in html
    assert '<li class="done">Connect Tautulli' in client.get("/").text


def test_tautulli_for_another_plex_server_is_refused_and_not_saved(client, gateway, signed_in):
    client.get("/")
    gateway.add_tautulli(TAUTULLI, KEY, "some-other-server")

    response = connect(client)

    assert response.status_code == 400 and "for this Plex server" in response.text
    assert '<li class="todo">Connect Tautulli' in client.get("/").text


def test_a_wrong_key_or_unreachable_tautulli_is_refused_and_not_saved(client, running):

    assert connect(client, api_key="wrong").status_code == 400
    assert connect(client, url="http://nothing:8181").status_code == 400
    assert connect(client, url="ftp://tautulli").status_code == 400
    assert '<li class="todo">Connect Tautulli' in client.get("/").text


def test_saving_again_with_the_key_left_blank_keeps_the_saved_key(client, running):
    connect(client)

    assert connect(client, api_key="").status_code == 200
    assert '<li class="done">Connect Tautulli' in client.get("/").text


def test_removing_tautulli_unticks_the_checklist_and_stops_skip_sync(client, gateway, clock, pal):
    connect(client)
    refresh(client)
    queries = len(gateway.tautulli_queries)

    html = remove(client).text
    refresh(client)

    assert TAUTULLI not in html
    assert len(gateway.tautulli_queries) == queries


def test_reset_forgets_tautulli(client, gateway, sign_in, pal):
    connect(client)

    reset(client)
    sign_in(client, "owner-token")

    assert TAUTULLI not in client.get("/settings").text
    assert '<li class="todo">Connect Tautulli' in client.get("/").text


# --- Skips ---


def test_tracks_skipped_twice_drop_out_of_deep_cuts_once_tautulli_is_connected(client, gateway, clock, pal):
    keys = deep_cuts_world(client, gateway, clock)
    skip(gateway, clock, keys[1], times=2)  # often skipped
    skip(gateway, clock, keys[2], times=1)  # once isn't often
    skip(gateway, clock, keys[3], times=2, percent=50)  # half or more isn't a Skip

    refresh(client)
    assert sorted(playlist(gateway, "Deep Cuts").items) == keys[1:]  # not connected: as before

    connect(client)
    refresh(client)
    assert sorted(playlist(gateway, "Deep Cuts").items) == keys[2:]


def test_skips_are_kept_when_tautulli_is_reset_but_unused_while_it_is_removed(client, gateway, clock, pal):
    keys = deep_cuts_world(client, gateway, clock)
    skip(gateway, clock, keys[1], times=2)
    connect(client)
    refresh(client)
    gateway.reset_tautulli()

    refresh(client)
    assert keys[1] not in playlist(gateway, "Deep Cuts").items

    remove(client)
    refresh(client)
    assert keys[1] in playlist(gateway, "Deep Cuts").items


def test_skips_sync_incrementally_and_a_row_read_twice_counts_once(client, gateway, clock, pal):
    keys = deep_cuts_world(client, gateway, clock)
    skip(gateway, clock, keys[1], times=1)
    connect(client)
    refresh(client)
    clock.advance(hours=2)
    refresh(client)  # reads yesterday onwards again, so the same row comes back

    assert gateway.tautulli_queries == [(8, None), (8, (clock.now() - timedelta(hours=2, days=1)).date())]
    assert keys[1] in playlist(gateway, "Deep Cuts").items


def test_a_reinstalled_tautulli_reusing_row_ids_still_adds_its_skips(client, gateway, clock, pal):
    keys = deep_cuts_world(client, gateway, clock)
    skip(gateway, clock, keys[1], times=1, hours_ago=3)
    connect(client)
    refresh(client)
    gateway.reset_tautulli()
    skip(gateway, clock, keys[1], times=1)  # row id 1 again, but another play

    refresh(client)

    assert keys[1] not in playlist(gateway, "Deep Cuts").items


def test_skips_of_a_track_whose_rating_key_changed_are_relinked_by_guid(client, gateway, clock, pal):
    keys = deep_cuts_world(client, gateway, clock)
    skip(gateway, clock, keys[1], times=2)
    connect(client)
    refresh(client)

    gateway.rekey_track(keys[1], "rescanned")  # same guid, new rating key
    refresh(client)

    assert "rescanned" not in playlist(gateway, "Deep Cuts").items


def test_the_owner_maps_by_their_plex_tv_id(client, gateway, clock, pal):
    add_listener(client, 42)
    keys = deep_cuts_world(client, gateway, clock, account_id=42, history_account_id=1)  # the Owner is 1 in Plex
    skip(gateway, clock, keys[1], times=2, user_id=42)
    skip(gateway, clock, keys[2], times=2, user_id=1)
    connect(client)

    refresh(client, 42)

    items = playlist(gateway, "Deep Cuts", token="owner-token").items

    assert keys[1] not in items and keys[2] in items


def test_an_unreachable_tautulli_fails_the_refresh_but_mixes_use_the_skips_already_kept(client, gateway, clock, pal):
    keys = deep_cuts_world(client, gateway, clock)
    skip(gateway, clock, keys[1], times=2)
    connect(client)
    refresh(client)
    gateway.stop_tautulli(TAUTULLI)
    play(gateway, clock, 8, keys[7], times=2)  # now played twice: no longer a deep cut

    refresh(client)

    assert sorted(playlist(gateway, "Deep Cuts").items) == keys[2:7]
    latest = client.app.state.store.refreshes(8)[0]
    assert latest.outcome == "failed" and "read Skips from Tautulli" in client.get("/listeners/8?tab=history").text


def test_turning_a_listener_off_forgets_their_skips(client, gateway, clock, pal):
    keys = deep_cuts_world(client, gateway, clock)
    skip(gateway, clock, keys[1], times=2)
    connect(client)
    refresh(client)
    csrf = csrf_from(client.get("/listeners/8/turn-off").text)
    client.post("/listeners/8/turn-off", data={"csrf_token": csrf})
    gateway.reset_tautulli()

    add_listener(client, 8)
    deep_cuts_world(client, gateway, clock)  # its settings went too
    refresh(client)

    assert keys[1] in playlist(gateway, "Deep Cuts").items


def test_often_skipped_tracks_drop_out_of_genre_mix_and_daily_mixtape_but_not_on_repeat(client, gateway, clock, pal):
    keys = tracks(gateway, "Band", 25)
    for k in keys[:10]:
        play(gateway, clock, 8, k, times=2)  # familiar for Daily Mixtape, 20 Rock Plays, all On Repeat
    skip(gateway, clock, keys[0], times=2)  # familiar
    skip(gateway, clock, keys[20], times=2)  # fresh
    csrf = csrf_from(client.get("/listeners/8").text)
    client.post("/listeners/8/mixes/daily_mixtape", data={"csrf_token": csrf, "enabled": "on", "length": "50", "tracks_per_artist": "50", "fresh_share": "40"})
    client.post("/listeners/8/mixes/genre_mix", data={"csrf_token": csrf, "enabled": "on", "count": "3", "length": "50", "tracks_per_artist": "50"})
    connect(client)

    refresh(client)

    assert sorted(playlist(gateway, "Rock Mix").items) == sorted(set(keys) - {keys[0], keys[20]})
    assert sorted(playlist(gateway, "Daily Mixtape").items) == sorted(set(keys) - {keys[0], keys[20]})
    assert keys[0] in on_repeat(gateway).items


def test_new_artists_drops_artists_whose_tracks_have_two_skips_in_total(client, gateway, clock, pal):
    once = tracks(gateway, "Once", 5)
    twice = tracks(gateway, "Twice", 5)
    skip(gateway, clock, once[0])
    skip(gateway, clock, twice[0])
    skip(gateway, clock, twice[1])  # two tracks, one Skip each
    csrf = csrf_from(client.get("/listeners/8").text)
    client.post("/listeners/8/mixes/new_artists", data={"csrf_token": csrf, "enabled": "on", "length": "30", "tracks_per_artist": "5"})
    connect(client)

    refresh(client)

    assert sorted(playlist(gateway, "New Artists").items) == once
