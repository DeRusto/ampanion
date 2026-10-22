"""Deep Cuts: tracks a Listener has played at most once, by their Top Artists, capped per artist and spread out."""

import random

import pytest

from ampanion.mixes.artists import spread_shuffle
from tests.test_listeners import add_listener
from tests.test_on_repeat import PAL, play, refresh
from tests.test_sign_in import csrf_from


@pytest.fixture
def pal(client, gateway, sign_in):
    """Signed in as the Owner (42), with "Pal" (8) a Listener and an empty library."""
    gateway.add_account("dee", token="owner-token", account_id=42)
    server = gateway.add_server(owner_token="owner-token", name="Basement")
    gateway.add_user("owner-token", "Pal", account_id=8, share=server)
    sign_in(client, "owner-token")
    add_listener(client, 8)
    return 8


def artist_with(gateway, clock, artist, tracks, plays):
    """`tracks` library tracks by `artist`; the first one played `plays` times. Returns the track keys."""
    keys = [f"{artist}-{n}" for n in range(tracks)]
    for k in keys:
        gateway.add_track(k, artist=artist, album=f"{artist} LP")
    play(gateway, clock, 8, keys[0], times=plays)
    return keys


def deep_cuts(gateway):
    return next((p for p in gateway.playlists_of(PAL) if p.title == "Deep Cuts"), None)


def artist_of(gateway, key):
    return key.rsplit("-", 1)[0]


def save_deep_cuts(client, **fields):
    csrf = csrf_from(client.get("/listeners/8").text)
    data = {"csrf_token": csrf, "enabled": "on", "length": "30", "tracks_per_artist": "3"} | fields
    return client.post("/listeners/8/mixes/deep_cuts", data=data)


def test_deep_cuts_are_rarely_played_tracks_by_top_artists_capped_per_artist_and_spread(client, gateway, clock, pal):
    for i in range(12):
        artist_with(gateway, clock, f"Band{i}", tracks=6, plays=20 - i)
    play(gateway, clock, 8, "Band0-1", times=1)  # once still counts as a deep cut
    play(gateway, clock, 8, "Band0-2", times=2)  # twice doesn't

    for _ in range(5):
        refresh(client)
        items = deep_cuts(gateway).items
        artists = [artist_of(gateway, k) for k in items]
        assert len(items) == 30 and len(set(items)) == 30
        assert max(artists.count(a) for a in artists) <= 3
        assert not {k for k in items if k.endswith("-0")} and "Band0-2" not in items
        assert all(a != b for a, b in zip(artists, artists[1:]))
    assert deep_cuts(gateway).summary == "Made by ampanion: Deep Cuts. Refreshed weekly; edits are replaced."


def test_top_artists_are_the_top_15_by_all_time_plays_with_at_least_10(client, gateway, clock, pal):
    for i in range(17):
        artist_with(gateway, clock, f"Band{i}", tracks=2, plays=40 - i)  # Band15 and Band16 rank 16th and 17th
    save_deep_cuts(client, length="100")

    refresh(client)

    assert {artist_of(gateway, k) for k in deep_cuts(gateway).items} == {f"Band{i}" for i in range(15)}


def test_with_fewer_than_15_at_10_plays_any_played_artist_counts(client, gateway, clock, pal):
    artist_with(gateway, clock, "Loved", tracks=4, plays=30)
    artist_with(gateway, clock, "Once", tracks=4, plays=1)
    artist_with(gateway, clock, "Never", tracks=4, plays=0)

    refresh(client)

    assert {artist_of(gateway, k) for k in deep_cuts(gateway).items} == {"Loved", "Once"}


def test_various_artists_low_rated_artists_and_low_rated_tracks_are_left_out(client, gateway, clock, pal):
    artist_with(gateway, clock, "Various Artists", tracks=4, plays=30)
    artist_with(gateway, clock, "Panned", tracks=4, plays=30)
    good = artist_with(gateway, clock, "Good", tracks=6, plays=30)
    for k in ("Good-b1", "Good-b2"):
        gateway.add_track(k, artist="Good", album="Good B")
    gateway.rate(PAL, "artist-Panned", stars=1)
    gateway.rate(PAL, good[1], stars=1)
    gateway.rate(PAL, "album-Good B", stars=0.5)
    gateway.rate(PAL, "Good-b2", stars=4)  # its own Rating outranks the album's
    save_deep_cuts(client, tracks_per_artist="10")

    refresh(client)

    assert sorted(deep_cuts(gateway).items) == ["Good-2", "Good-3", "Good-4", "Good-5", "Good-b2"]


def test_length_and_tracks_per_artist_are_editable_on_the_mixes_tab(client, gateway, clock, pal):
    for i in range(4):
        artist_with(gateway, clock, f"Band{i}", tracks=6, plays=10)
    html = client.get("/listeners/8").text
    assert "Deep Cuts" in html and 'name="tracks_per_artist" value="3"' in html

    save_deep_cuts(client, length="6", tracks_per_artist="1")
    refresh(client)

    assert deep_cuts(gateway) is None  # 4 artists x 1 track: under five tracks
    assert "Not enough history yet" in client.get("/listeners/8").text
    save_deep_cuts(client, length="6", tracks_per_artist="2")
    refresh(client)

    assert len(deep_cuts(gateway).items) == 6
    assert 'name="tracks_per_artist" value="2"' in client.get("/listeners/8").text
    assert "Deep Cuts: 6 tracks" in client.get("/listeners/8?tab=history").text


def test_a_short_result_is_published_as_it_is(client, gateway, clock, pal):
    artist_with(gateway, clock, "Band", tracks=8, plays=12)
    artist_with(gateway, clock, "Other", tracks=8, plays=12)

    refresh(client)

    assert len(deep_cuts(gateway).items) == 6  # 3 per artist
    assert "6 of 30 tracks (not enough history)" in client.get("/listeners/8").text


def test_the_shuffle_spreads_artists_where_the_mix_allows_it():
    for seed in range(200):
        rng = random.Random(seed)
        tracks = [f"{a}{n}" for a, count in zip("abcde", rng.sample(range(1, 7), 5)) for n in range(count)]
        ordered = spread_shuffle(tracks, lambda t: t[0], rng)
        assert sorted(ordered) == sorted(tracks)
        assert all(x[0] != y[0] for x, y in zip(ordered, ordered[1:]))

    ordered = spread_shuffle(["a1", "a2", "a3", "b1", "b2"], lambda t: t[0], random.Random(7))
    assert [t[0] for t in ordered] == ["a", "b", "a", "b", "a"]
