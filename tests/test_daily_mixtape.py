"""Daily Mixtape: familiar tracks (2+ Plays) and fresh ones (unplayed, by played artists), new every day."""

import pytest

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


def artist_with(gateway, clock, artist, familiar=0, fresh=0, once=0, days_ago=1):
    """Library tracks by `artist`: `familiar` played 3 times, `fresh` never, `once` played once."""
    keys = {}
    for kind, count, plays in (("familiar", familiar, 3), ("fresh", fresh, 0), ("once", once, 1)):
        keys[kind] = [f"{artist}-{kind}{n}" for n in range(count)]
        for k in keys[kind]:
            gateway.add_track(k, artist=artist, album=f"{artist} LP")
            play(gateway, clock, 8, k, times=plays, days_ago=days_ago)
    return keys


def mixtape(gateway):
    return next((p for p in gateway.playlists_of(PAL) if p.title == "Daily Mixtape"), None)


def artist_of(key):
    return key.rsplit("-", 1)[0]


def kind_of(key):
    return key.rsplit("-", 1)[1].rstrip("0123456789")


def save_mixtape(client, **fields):
    csrf = csrf_from(client.get("/listeners/8").text)
    data = {"csrf_token": csrf, "enabled": "on", "length": "50", "tracks_per_artist": "2", "fresh_share": "40"} | fields
    return client.post("/listeners/8/mixes/daily_mixtape", data=data)


def test_daily_mixtape_blends_familiar_and_fresh_tracks_capped_per_artist_and_spread(client, gateway, clock, pal):
    for i in range(40):
        artist_with(gateway, clock, f"Band{i}", familiar=4, fresh=4, once=2)
    artist_with(gateway, clock, "Unplayed", fresh=10)  # never played: not fresh
    artist_with(gateway, clock, "Various Artists", familiar=2, fresh=10)  # its unplayed tracks never count as fresh

    previous = []
    for _ in range(4):
        refresh(client)
        items = list(mixtape(gateway).items)
        artists, kinds = [artist_of(k) for k in items], [kind_of(k) for k in items]
        assert len(items) == 50 and len(set(items)) == 50
        assert kinds.count("familiar") == 30 and kinds.count("fresh") == 20
        assert not {a for a, k in zip(artists, kinds) if k == "fresh"} & {"Unplayed", "Various Artists"}
        assert max(artists.count(a) for a in artists) <= 2
        assert all(a != b for a, b in zip(artists, artists[1:]))
        runs = "".join(k[1] for k in kinds)  # "a" familiar, "r" fresh: evenly interleaved, 3 familiar to 2 fresh
        assert "rr" not in runs and "aaa" not in runs
        assert not set(items) & set(previous)  # nothing from the previous Daily Mixtape
        previous = items
    assert mixtape(gateway).summary == "Made by ampanion: Daily Mixtape. Refreshed daily; edits are replaced."


def test_familiar_tracks_lean_toward_recent_plays(client, gateway, clock, pal):
    for i in range(50):
        artist_with(gateway, clock, f"Recent{i}", familiar=1, days_ago=1)
        artist_with(gateway, clock, f"Old{i}", familiar=1, days_ago=700)
    save_mixtape(client, length="20", fresh_share="0")

    refresh(client)

    recent = [k for k in mixtape(gateway).items if k.startswith("Recent")]
    assert len(mixtape(gateway).items) == 20 and len(recent) >= 16


def test_a_short_fresh_side_is_filled_with_familiar_tracks(client, gateway, clock, pal):
    for i in range(10):
        artist_with(gateway, clock, f"Band{i}", familiar=2, fresh=1 if i < 3 else 0)
    save_mixtape(client, length="20")

    refresh(client)

    kinds = [kind_of(k) for k in mixtape(gateway).items]
    assert kinds.count("fresh") == 3 and kinds.count("familiar") == 17


def test_a_short_familiar_side_is_filled_with_fresh_tracks(client, gateway, clock, pal):
    for i in range(10):
        artist_with(gateway, clock, f"Band{i}", familiar=1 if i < 2 else 0, fresh=2, once=1)
    save_mixtape(client, length="20", tracks_per_artist="3")

    refresh(client)

    kinds = [kind_of(k) for k in mixtape(gateway).items]
    assert kinds.count("familiar") == 2 and kinds.count("fresh") == 18


def test_tracks_rated_1_star_or_lower_are_left_out(client, gateway, clock, pal):
    keys = artist_with(gateway, clock, "Band", familiar=4, fresh=4)
    gateway.rate(PAL, keys["familiar"][0], stars=1)
    gateway.rate(PAL, keys["fresh"][0], stars=0.5)
    save_mixtape(client, tracks_per_artist="10")

    refresh(client)

    assert sorted(mixtape(gateway).items) == sorted(keys["familiar"][1:] + keys["fresh"][1:])


def test_length_tracks_per_artist_and_fresh_share_are_editable_on_the_mixes_tab(client, gateway, clock, pal):
    for i in range(10):
        artist_with(gateway, clock, f"Band{i}", familiar=3, fresh=3)
    html = client.get("/listeners/8").text
    assert "Daily Mixtape" in html and 'name="fresh_share" value="40"' in html

    save_mixtape(client, length="10", tracks_per_artist="3", fresh_share="70")
    refresh(client)

    items = mixtape(gateway).items
    assert len(items) == 10 and [kind_of(k) for k in items].count("fresh") == 7
    assert max([artist_of(k) for k in items].count(a) for a in map(artist_of, items)) <= 3
    assert save_mixtape(client, fresh_share="101").status_code == 400
