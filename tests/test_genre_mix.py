"""Genre Mix: one Playlist per Top Genre, a random pick from the library's tracks in that genre, capped per artist."""

import pytest

from tests.test_listeners import add_listener
from tests.test_on_repeat import PAL, play, refresh
from tests.test_sign_in import csrf_from

URL = "http://unused"


@pytest.fixture
def pal(client, gateway, sign_in):
    """Signed in as the Owner (42), with "Pal" (8) a Listener and an empty library."""
    gateway.add_account("dee", token="owner-token", account_id=42)
    server = gateway.add_server(owner_token="owner-token", name="Basement")
    gateway.add_user("owner-token", "Pal", account_id=8, share=server)
    sign_in(client, "owner-token")
    add_listener(client, 8)
    return 8


def genre(gateway, clock, name, artists=10, tracks=6, plays=0, days_ago=1, **level):
    """`artists` x `tracks` library tracks tagged `name` at track level (or as `level` says); the first played `plays` times."""
    tags = level or {"track_genres": (name,)}
    keys = []
    for a in range(artists):
        for n in range(tracks):
            keys.append(f"{name}-{a}-{n}")
            gateway.add_track(keys[-1], artist=f"{name} band {a}", album=f"{name} LP {a}", **tags)
    play(gateway, clock, 8, keys[0], times=plays, days_ago=days_ago)
    return keys


def mixes(gateway):
    return {p.title: p for p in gateway.playlists_of(PAL) if p.title.endswith(" Mix")}


def save_genre_mix(client, **fields):
    csrf = csrf_from(client.get("/listeners/8").text)
    data = {"csrf_token": csrf, "enabled": "on", "count": "3", "length": "50", "tracks_per_artist": "3"} | fields
    return client.post("/listeners/8/mixes/genre_mix", data=data)


def test_one_playlist_per_top_genre_picked_from_the_genre_and_capped_per_artist(client, gateway, clock, pal):
    rock = genre(gateway, clock, "Rock", artists=30, plays=40)
    genre(gateway, clock, "Jazz", plays=30)
    genre(gateway, clock, "Folk", plays=25)
    genre(gateway, clock, "Polka", plays=21)  # fourth: past the count
    genre(gateway, clock, "Ska", plays=19)  # under 20 Plays

    refresh(client)

    assert set(mixes(gateway)) == {"Rock Mix", "Jazz Mix", "Folk Mix"}
    items = mixes(gateway)["Rock Mix"].items
    artists = [k.rsplit("-", 1)[0] for k in items]
    assert len(items) == 50 and set(items) <= set(rock)
    assert max(artists.count(a) for a in artists) <= 3
    assert len(mixes(gateway)["Jazz Mix"].items) == 30  # 10 artists x 3
    assert mixes(gateway)["Rock Mix"].summary == "Made by ampanion: Rock Mix. Refreshed weekly; edits are replaced."
    assert "Rock Mix: 50 tracks" in client.get("/listeners/8?tab=history").text


def test_genre_mix_playlists_never_put_two_tracks_by_one_artist_side_by_side(client, gateway, clock, pal):
    genre(gateway, clock, "Rock", artists=4, tracks=6, plays=40)

    for _ in range(5):
        refresh(client)
        artists = [k.rsplit("-", 1)[0] for k in mixes(gateway)["Rock Mix"].items]
        assert len(artists) == 12 and all(a != b for a, b in zip(artists, artists[1:]))


def test_plays_of_a_track_gone_from_the_library_still_count_toward_top_genres(client, gateway, clock, pal):
    rock = genre(gateway, clock, "Rock", plays=25)
    refresh(client)

    gateway.remove_track(rock[0])  # every Rock Play was of this track
    refresh(client)

    assert set(mixes(gateway)) == {"Rock Mix"} and rock[0] not in mixes(gateway)["Rock Mix"].items


def test_no_playlist_when_no_genre_has_20_plays(client, gateway, clock, pal):
    genre(gateway, clock, "Rock", plays=19)

    refresh(client)

    assert mixes(gateway) == {}


def test_genres_come_from_the_most_specific_level_that_has_any(client, gateway, clock, pal):
    # Tagged Rock at track level, the album says Pop and the artist Jazz: only Rock counts.
    genre(gateway, clock, "Rock", plays=30, track_genres=("Rock",), album_genres=("Pop",), artist_genres=("Jazz",))
    # No track tags: the album's Soul counts, not the artist's Jazz.
    genre(gateway, clock, "Soul", plays=30, album_genres=("Soul",), artist_genres=("Jazz",))
    # Only the artist is tagged.
    genre(gateway, clock, "Blues", plays=30, artist_genres=("Blues",))

    refresh(client)

    assert set(mixes(gateway)) == {"Rock Mix", "Soul Mix", "Blues Mix"}
    assert all(k.startswith("Soul") for k in mixes(gateway)["Soul Mix"].items)


def test_a_track_with_two_top_genres_counts_toward_each_and_may_appear_in_both(client, gateway, clock, pal):
    both = genre(gateway, clock, "Both", artists=2, tracks=3, plays=20, track_genres=("Rock", "Jazz"))

    refresh(client)

    assert set(mixes(gateway)) == {"Rock Mix", "Jazz Mix"}
    assert sorted(mixes(gateway)["Rock Mix"].items) == sorted(mixes(gateway)["Jazz Mix"].items) == sorted(both)


def test_ties_go_to_the_latest_play(client, gateway, clock, pal):
    for name, days_ago in (("Old", 5), ("New", 1), ("Mid", 3), ("Older", 7)):
        genre(gateway, clock, name, plays=20, days_ago=days_ago)

    refresh(client)

    assert set(mixes(gateway)) == {"New Mix", "Mid Mix", "Old Mix"}


def test_low_rated_tracks_are_left_out_and_the_five_track_minimum_is_per_playlist(client, gateway, clock, pal):
    genre(gateway, clock, "Rock", plays=30)
    small = genre(gateway, clock, "Jazz", artists=1, tracks=6, plays=30)
    gateway.rate(PAL, small[1], stars=1)
    gateway.rate(PAL, small[2], stars=0.5)  # four left, under five

    refresh(client)

    assert set(mixes(gateway)) == {"Rock Mix"}
    assert "Jazz: Not enough history yet" in client.get("/listeners/8").text


def test_a_genre_that_drops_out_loses_its_playlist_and_a_new_top_genre_gets_one(client, gateway, clock, pal):
    genre(gateway, clock, "Rock", plays=40)
    genre(gateway, clock, "Jazz", plays=30)
    genre(gateway, clock, "Folk", plays=25)
    theirs = gateway.create_playlist(URL, PAL, "m", "Polka Mix", ["Rock-0-0"])  # the Listener's own
    refresh(client)
    folk = mixes(gateway)["Folk Mix"].id

    genre(gateway, clock, "Punk", plays=26)
    refresh(client)

    assert set(mixes(gateway)) == {"Rock Mix", "Jazz Mix", "Punk Mix", "Polka Mix"}
    assert folk not in {p.id for p in gateway.playlists_of(PAL)}
    assert theirs in {p.id for p in gateway.playlists_of(PAL)}

    save_genre_mix(client, count="1")
    refresh(client)

    assert set(mixes(gateway)) == {"Rock Mix", "Polka Mix"}
    assert "Jazz" not in client.get("/listeners/8").text


def test_lowering_the_count_deletes_the_surplus_playlists_at_once(client, gateway, clock, pal):
    genre(gateway, clock, "Rock", plays=40)
    genre(gateway, clock, "Jazz", plays=30)
    genre(gateway, clock, "Folk", plays=25)
    refresh(client)

    save_genre_mix(client, count="2")  # no Refresh after it

    assert set(mixes(gateway)) == {"Rock Mix", "Jazz Mix"}
    assert "Folk" not in client.get("/listeners/8").text


def test_count_length_and_tracks_per_artist_are_editable_on_the_mixes_tab(client, gateway, clock, pal):
    genre(gateway, clock, "Rock", plays=40)
    genre(gateway, clock, "Jazz", plays=30)
    html = client.get("/listeners/8").text
    assert "Genre Mix" in html and 'name="count" value="3"' in html

    save_genre_mix(client, count="1", length="8", tracks_per_artist="1")
    refresh(client)

    assert set(mixes(gateway)) == {"Rock Mix"}
    items = mixes(gateway)["Rock Mix"].items
    assert len(items) == 8 and len({k.rsplit("-", 1)[0] for k in items}) == 8


def test_takeover_by_marker_matches_on_mix_and_genre(client, gateway, clock, pal):
    marker = "Made by ampanion: {}. Refreshed daily; edits are replaced."
    jazz = gateway.create_playlist(URL, PAL, "m", "Renamed", ["x"])
    gateway.set_playlist_summary(URL, PAL, jazz, marker.format("Jazz Mix"))
    rock = gateway.create_playlist(URL, PAL, "m", "Mine", ["x"])
    gateway.set_playlist_summary(URL, PAL, rock, marker.format("Rock Mix"))
    genre(gateway, clock, "Rock", plays=40)

    refresh(client)

    playlists = {p.id: p for p in gateway.playlists_of(PAL)}
    assert playlists[jazz].items == ["x"]  # another genre's marker: left alone
    assert len(playlists[rock].items) == 30 and "Rock Mix" not in mixes(gateway)
