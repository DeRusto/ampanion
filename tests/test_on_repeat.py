"""The first Refresh: Refresh now syncs a Listener's Listening history, reads their Ratings and publishes On Repeat."""

from datetime import timedelta

import pytest

from tests.test_listeners import add_listener, sidebar
from tests.test_sign_in import csrf_from

PAL = "Pal-server-token"  # Pal's server token, which owns Pal's Playlists


@pytest.fixture
def pal(client, gateway, sign_in):
    """Signed in as the Owner "dee" (42), with "Pal" (8) a Listener; the library has tracks t1..t40 by four artists."""
    gateway.add_account("dee", token="owner-token", account_id=42)
    server = gateway.add_server(owner_token="owner-token", name="Basement")
    gateway.add_user("owner-token", "Pal", account_id=8, share=server)
    for n in range(1, 41):
        gateway.add_track(f"t{n}", artist=f"Artist {n % 4}", album=f"Album {n % 8}")
    sign_in(client, "owner-token")
    add_listener(client, 8)
    return 8


def listen(gateway, clock, account_id, key, times, days_ago=1):
    for i in range(times):
        gateway.play(account_id, key, at=clock.now() - timedelta(days=days_ago, minutes=i))


def refresh(client, account_id=8):
    csrf = csrf_from(client.get(f"/listeners/{account_id}").text)
    return client.post(f"/listeners/{account_id}/refresh", data={"csrf_token": csrf})


def on_repeat(gateway, token=PAL):
    return next((p for p in gateway.playlists_of(token) if p.title == "On Repeat"), None)


def save_mix(client, account_id=8, **fields):
    csrf = csrf_from(client.get(f"/listeners/{account_id}").text)
    data = {"csrf_token": csrf, "enabled": "on", "window_days": "30", "length": "30"} | fields
    return client.post(f"/listeners/{account_id}/mixes/on_repeat", data=data)


def test_refresh_now_publishes_on_repeat_ranked_by_plays_in_pals_own_plex_account(client, gateway, clock, pal):
    for n in range(1, 7):
        listen(gateway, clock, 8, f"t{n}", times=8 - n)  # t1 played 7 times ... t6 twice
    listen(gateway, clock, 8, "t7", times=1)  # once is not enough

    refresh(client)

    playlist = on_repeat(gateway)
    assert playlist.items == ["t1", "t2", "t3", "t4", "t5", "t6"]
    assert playlist.summary == "Made by ampanion: On Repeat. Refreshed daily; edits are replaced."
    assert on_repeat(gateway, "owner-token") is None  # not in the Owner's account


def test_ties_go_to_the_latest_play_and_plays_outside_the_window_dont_count(client, gateway, clock, pal):
    for n in range(1, 6):
        listen(gateway, clock, 8, f"t{n}", times=2, days_ago=n)  # t1 most recent
    listen(gateway, clock, 8, "t9", times=5, days_ago=31)  # outside the 30-day window

    refresh(client)

    assert on_repeat(gateway).items == ["t1", "t2", "t3", "t4", "t5"]


def test_the_length_caps_the_playlist_and_there_is_no_per_artist_cap(client, gateway, clock, pal):
    for n in range(1, 41):
        listen(gateway, clock, 8, f"t{n}", times=2)

    refresh(client)

    assert len(on_repeat(gateway).items) == 30
    assert "30 tracks" in client.get("/listeners/8").text


def test_window_and_length_are_editable_on_the_mixes_tab(client, gateway, clock, pal):
    for n in range(1, 9):
        listen(gateway, clock, 8, f"t{n}", times=2, days_ago=50)

    html = client.get("/listeners/8").text
    assert "On Repeat" in html and 'name="window_days" value="30"' in html and 'name="length" value="30"' in html

    save_mix(client, window_days="60", length="6")
    refresh(client)

    assert len(on_repeat(gateway).items) == 6
    html = client.get("/listeners/8").text
    assert 'name="window_days" value="60"' in html and 'name="length" value="6"' in html


def test_mix_settings_out_of_range_are_refused(client, pal):
    assert save_mix(client, length="0").status_code == 400
    assert save_mix(client, window_days="abc").status_code == 400
    assert 'name="length" value="30"' in client.get("/listeners/8").text


def test_tracks_rated_one_star_or_lower_are_excluded_by_track_then_album_then_artist(client, gateway, clock, pal):
    for n in range(1, 11):
        listen(gateway, clock, 8, f"t{n}", times=2, days_ago=n)
    gateway.rate(PAL, "t1", stars=1)  # track rating excludes
    gateway.rate(PAL, "album-Album 2", stars=0.5)  # album rating excludes t2 (and t10)...
    gateway.rate(PAL, "t10", stars=4)  # ...unless the track's own Rating says otherwise
    gateway.rate(PAL, "artist-Artist 3", stars=1)  # artist rating excludes t3 and t7
    gateway.rate(PAL, "album-Album 7", stars=3)  # but t7's album is rated well
    gateway.rate("owner-token", "t4", stars=1)  # someone else's Rating doesn't matter

    refresh(client)

    assert on_repeat(gateway).items == ["t4", "t5", "t6", "t7", "t8", "t9", "t10"]


def test_ratings_are_read_fresh_each_refresh(client, gateway, clock, pal):
    for n in range(1, 7):
        listen(gateway, clock, 8, f"t{n}", times=2, days_ago=n)
    gateway.rate(PAL, "t1", stars=1)
    refresh(client)
    gateway.rate(PAL, "t1", stars=5)

    refresh(client)

    assert on_repeat(gateway).items[0] == "t1"


def test_later_refreshes_replace_the_tracks_in_place_keeping_the_playlist_id(client, gateway, clock, pal):
    for n in range(1, 6):
        listen(gateway, clock, 8, f"t{n}", times=2, days_ago=n)
    refresh(client)
    first = on_repeat(gateway)

    listen(gateway, clock, 8, "t20", times=9)
    refresh(client)

    assert [p.id for p in gateway.playlists_of(PAL) if p.title == "On Repeat"] == [first.id]
    assert on_repeat(gateway).items == ["t20", "t1", "t2", "t3", "t4", "t5"]


def test_long_playlists_are_added_in_chunks(client, gateway, clock, pal):
    for n in range(41, 641):
        gateway.add_track(f"t{n}", artist=f"Artist {n % 4}", album=f"Album {n % 8}")
        listen(gateway, clock, 8, f"t{n}", times=2)
    save_mix(client, length="600")

    refresh(client)
    refresh(client)

    assert len(on_repeat(gateway).items) == 600
    assert max(gateway.item_batches[PAL]) < 600


def test_the_owner_as_a_listener_uses_history_account_1_and_the_owner_token(client, gateway, clock, pal):
    add_listener(client, 42)
    for n in range(1, 6):
        listen(gateway, clock, 1, f"t{n}", times=2, days_ago=n)  # the Owner's local history account is always 1

    refresh(client, 42)

    assert on_repeat(gateway, "owner-token").items == ["t1", "t2", "t3", "t4", "t5"]
    assert on_repeat(gateway) is None


def test_short_results_are_published_as_they_are(client, gateway, clock, pal):
    for n in range(1, 13):
        listen(gateway, clock, 8, f"t{n}", times=2)

    refresh(client)

    assert len(on_repeat(gateway).items) == 12
    assert "12 of 30 tracks (not enough history)" in client.get("/listeners/8").text


def test_under_five_tracks_creates_nothing_and_still_counts_as_ok(client, gateway, clock, pal):
    for n in range(1, 5):
        listen(gateway, clock, 8, f"t{n}", times=2)

    refresh(client)

    assert on_repeat(gateway) is None
    assert "Not enough history yet" in client.get("/listeners/8").text
    history = client.get("/listeners/8?tab=history").text
    assert "ok" in history and "On Repeat: Not enough history yet" in history


def test_under_five_tracks_leaves_an_existing_playlist_untouched(client, gateway, clock, pal):
    for n in range(1, 7):
        listen(gateway, clock, 8, f"t{n}", times=2, days_ago=20)
    refresh(client)
    before = list(on_repeat(gateway).items)

    clock.advance(days=15)  # those Plays fall out of the window
    refresh(client)

    assert sorted(before) == ["t1", "t2", "t3", "t4", "t5", "t6"]
    assert on_repeat(gateway).items == before
    assert "Not enough history yet" in client.get("/listeners/8").text


def test_the_first_refresh_reads_all_history_and_later_ones_only_the_last_90_days(client, gateway, clock, pal):
    save_mix(client, window_days="400")
    for n in range(1, 6):
        listen(gateway, clock, 8, f"t{n}", times=2, days_ago=300 + n)  # years-old history is read on the first fill
    refresh(client)
    assert len(on_repeat(gateway).items) == 5

    listen(gateway, clock, 8, "t6", times=5, days_ago=100)  # arrives late, older than 90 days: never read
    listen(gateway, clock, 8, "t7", times=3, days_ago=80)  # arrives late within 90 days: read
    listen(gateway, clock, 8, "t8", times=1, days_ago=10)
    refresh(client)
    refresh(client)  # re-reading the window doesn't count Plays twice, so t8 still has only one

    assert on_repeat(gateway).items == ["t7", "t1", "t2", "t3", "t4", "t5"]


def test_plays_deleted_in_plex_stay_in_ampanion(client, gateway, clock, pal):
    for n in range(1, 6):
        listen(gateway, clock, 8, f"t{n}", times=2, days_ago=n)
    refresh(client)

    gateway.clear_history(8)
    refresh(client)

    assert on_repeat(gateway).items == ["t1", "t2", "t3", "t4", "t5"]


def test_a_track_whose_rating_key_changed_is_relinked_by_guid(client, gateway, clock, pal):
    for n in range(1, 6):
        listen(gateway, clock, 8, f"t{n}", times=2, days_ago=n)
    refresh(client)

    gateway.rekey_track("t1", "t1-rescanned")  # same guid, new rating key
    refresh(client)

    assert on_repeat(gateway).items == ["t1-rescanned", "t2", "t3", "t4", "t5"]


def test_a_track_gone_from_the_library_yields_to_the_next_rank(client, gateway, clock, pal):
    for n in range(1, 7):
        listen(gateway, clock, 8, f"t{n}", times=2, days_ago=n)
    gateway.remove_track("t1")

    refresh(client)

    assert on_repeat(gateway).items == ["t2", "t3", "t4", "t5", "t6"]


def test_a_failed_history_sync_leaves_playlists_untouched_and_is_recorded(client, gateway, clock, pal):
    for n in range(1, 6):
        listen(gateway, clock, 8, f"t{n}", times=2, days_ago=n)
    refresh(client)
    listen(gateway, clock, 8, "t20", times=9)

    gateway.history_down = True
    refresh(client)

    assert on_repeat(gateway).items == ["t1", "t2", "t3", "t4", "t5"]
    history = client.get("/listeners/8?tab=history").text
    assert "failed" in history and "Couldn&#39;t read Listening history from Plex" in history
    assert 'class="status failed"' in sidebar(client.get("/").text)


def test_turning_on_repeat_off_skips_it_on_refresh(client, gateway, clock, pal):
    for n in range(1, 6):
        listen(gateway, clock, 8, f"t{n}", times=2)
    csrf = csrf_from(client.get("/listeners/8").text)
    client.post("/listeners/8/mixes/on_repeat", data={"csrf_token": csrf, "window_days": "30", "length": "30"})

    refresh(client)

    assert on_repeat(gateway) is None
    assert 'name="enabled"' in client.get("/listeners/8").text
    assert "checked" not in client.get("/listeners/8").text.split('name="enabled"')[1].split(">")[0]


def test_refreshes_are_recorded_on_home_and_the_history_tab_and_in_the_sidebar(client, gateway, clock, pal):
    for n in range(1, 31):
        listen(gateway, clock, 8, f"t{n}", times=2)
    assert 'class="status' not in sidebar(client.get("/").text)

    refresh(client)

    home = client.get("/").text
    assert "Recent Refreshes" in home and "Pal" in home and "On Repeat: 30 tracks" in home
    assert "2026-01-05 12:00" in home
    history = client.get("/listeners/8?tab=history").text
    assert "On Repeat: 30 tracks" in history and "ok" in history
    assert 'class="status ok"' in sidebar(home)


def test_turning_a_listener_off_forgets_their_history(client, gateway, clock, pal):
    for n in range(1, 6):
        listen(gateway, clock, 8, f"t{n}", times=2)
    refresh(client)
    client.post("/listeners/8/turn-off", data={"csrf_token": csrf_from(client.get("/").text)})
    gateway.clear_history(8)

    add_listener(client, 8)
    refresh(client)

    assert "Not enough history yet" in client.get("/listeners/8").text
    assert "On Repeat: 5 tracks" not in client.get("/").text
