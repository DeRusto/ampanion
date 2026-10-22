"""New Artists: tracks by album artists the Listener has never played, favouring their Top Genres."""

import pytest

from tests.test_listeners import add_listener
from tests.test_on_repeat import PAL, listen, refresh
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


def artist_with(gateway, clock, artist, tracks=4, plays=0, genre=None, popularity=None):
    """`tracks` library tracks by `artist` (tagged `genre` at artist level); the first played `plays` times."""
    keys = [f"{artist}-{n}" for n in range(tracks)]
    for n, k in enumerate(keys):
        fields = {"artist_genres": (genre,)} if genre else {}
        if popularity:
            fields["popularity"] = popularity[n]
        gateway.add_track(k, artist=artist, album=f"{artist} LP", **fields)
    listen(gateway, clock, 8, keys[0], times=plays)
    return keys


def new_artists(gateway):
    return next((p for p in gateway.playlists_of(PAL) if p.title == "New Artists"), None)


def artist_of(key):
    return key.rsplit("-", 1)[0]


def save_new_artists(client, **fields):
    csrf = csrf_from(client.get("/listeners/8").text)
    data = {"csrf_token": csrf, "enabled": "on", "length": "30", "tracks_per_artist": "3"} | fields
    return client.post("/listeners/8/mixes/new_artists", data=data)


def test_new_artists_are_unplayed_album_artists_capped_per_artist_and_spread(client, gateway, clock, pal):
    for i in range(5):
        artist_with(gateway, clock, f"Played{i}", plays=1)
    for i in range(20):
        artist_with(gateway, clock, f"New{i}")
    artist_with(gateway, clock, "Various Artists", tracks=10)  # never a new artist

    refresh(client)

    items = new_artists(gateway).items
    artists = [artist_of(k) for k in items]
    assert len(items) == 30 and len(set(items)) == 30
    assert all(a.startswith("New") for a in artists)
    assert max(artists.count(a) for a in artists) <= 3
    assert all(a != b for a, b in zip(artists, artists[1:]))
    assert new_artists(gateway).summary == "Made by ampanion: New Artists. Refreshed daily; edits are replaced."


def test_artists_sharing_a_top_genre_are_favoured(client, gateway, clock, pal):
    artist_with(gateway, clock, "Loved", plays=25, genre="Rock")  # makes Rock a Top Genre
    for i in range(40):
        artist_with(gateway, clock, f"Rock{i}", genre="Rock")
        artist_with(gateway, clock, f"Polka{i}", genre="Polka")
    save_new_artists(client, tracks_per_artist="1")

    refresh(client)

    artists = [artist_of(k) for k in new_artists(gateway).items]
    assert len(artists) == 30 and sum(a.startswith("Rock") for a in artists) >= 20


def test_with_no_top_genres_artists_are_picked_unweighted(client, gateway, clock, pal):
    artist_with(gateway, clock, "Loved", plays=19, genre="Rock")  # under 20 Plays: no Top Genre
    for i in range(40):
        artist_with(gateway, clock, f"Rock{i}", genre="Rock")
        artist_with(gateway, clock, f"Polka{i}", genre="Polka")
    save_new_artists(client, tracks_per_artist="1")

    refresh(client)

    artists = [artist_of(k) for k in new_artists(gateway).items]
    assert len(artists) == 30 and 8 <= sum(a.startswith("Rock") for a in artists) <= 22


def test_per_artist_the_most_popular_tracks_come_first(client, gateway, clock, pal):
    artist_with(gateway, clock, "Hits", tracks=6, popularity=[5, 900, 0, 40, 7000, 30])
    artist_with(gateway, clock, "Obscure", tracks=6)  # no popularity: any 3 of its tracks

    refresh(client)

    items = new_artists(gateway).items
    assert sorted(k for k in items if k.startswith("Hits")) == ["Hits-1", "Hits-3", "Hits-4"]
    assert len([k for k in items if k.startswith("Obscure")]) == 3


def test_last_times_artists_are_skipped_on_the_next_refresh_only(client, gateway, clock, pal):
    for i in range(12):
        artist_with(gateway, clock, f"New{i}", tracks=3)

    refresh(client)
    first = {artist_of(k) for k in new_artists(gateway).items}
    refresh(client)
    second = {artist_of(k) for k in new_artists(gateway).items}
    refresh(client)
    third = {artist_of(k) for k in new_artists(gateway).items}

    assert len(first) == 10 and len(second) == 2 and not first & second
    assert len(third) == 10 and not second & third and third <= first


def test_low_rated_artists_are_dropped_and_low_rated_tracks_left_out(client, gateway, clock, pal):
    artist_with(gateway, clock, "Panned", tracks=4)
    good = artist_with(gateway, clock, "Good", tracks=7)
    gateway.rate(PAL, "artist-Panned", stars=1)
    gateway.rate(PAL, good[1], stars=1)
    gateway.rate(PAL, good[2], stars=0.5)
    save_new_artists(client, tracks_per_artist="10")

    refresh(client)

    assert sorted(new_artists(gateway).items) == ["Good-0", "Good-3", "Good-4", "Good-5", "Good-6"]


def test_length_and_tracks_per_artist_are_editable_on_the_mixes_tab(client, gateway, clock, pal):
    for i in range(10):
        artist_with(gateway, clock, f"New{i}", tracks=6)
    html = client.get("/listeners/8").text
    assert "New Artists" in html and 'name="tracks_per_artist" value="3"' in html

    save_new_artists(client, length="12", tracks_per_artist="2")
    refresh(client)

    artists = [artist_of(k) for k in new_artists(gateway).items]
    assert len(artists) == 12 and max(artists.count(a) for a in artists) == 2
    assert "New Artists: 12 tracks" in client.get("/listeners/8?tab=history").text
