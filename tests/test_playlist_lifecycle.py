"""Playlist lifecycle: ampanion keeps managing its Playlists whatever the Listener or Owner does to them."""

from tests.test_on_repeat import PAL, listen, on_repeat, pal, refresh  # noqa: F401 (pal is a fixture)
from tests.test_sign_in import csrf_from

URL = "http://unused"
MARKER = "Made by ampanion: On Repeat. Refreshed daily; edits are replaced."


def five_plays(gateway, clock):
    for n in range(1, 6):
        listen(gateway, clock, 8, f"t{n}", times=2, days_ago=n)


def test_a_listeners_rename_survives_and_the_marker_is_put_back(client, gateway, clock, pal):
    five_plays(gateway, clock)
    refresh(client)
    playlist = on_repeat(gateway)
    gateway.rename_playlist(PAL, playlist.id, "My jams")
    gateway.set_playlist_summary(URL, PAL, playlist.id, "mine now")

    refresh(client)

    again = next(p for p in gateway.playlists_of(PAL) if p.id == playlist.id)
    assert (again.id, again.title, again.summary) == (playlist.id, "My jams", MARKER)


def test_a_playlist_deleted_in_plex_is_recreated_and_the_refresh_says_so(client, gateway, clock, pal):
    five_plays(gateway, clock)
    refresh(client)
    old = on_repeat(gateway).id
    gateway.delete_playlist(URL, PAL, old)

    refresh(client)

    assert on_repeat(gateway).id != old
    assert "On Repeat: 5 of 30 tracks (not enough history), recreated (was deleted in Plex)" in client.get("/listeners/8?tab=history").text
    refresh(client)
    assert "recreated" not in client.get("/listeners/8").text  # only the Refresh that did it says so


def leftover(gateway, title, summary):
    """A Playlist in Pal's account from before ampanion lost /config."""
    playlist_id = gateway.create_playlist(URL, PAL, "m", title, ["t40"])
    gateway.set_playlist_summary(URL, PAL, playlist_id, summary)
    return playlist_id


def test_with_no_stored_id_a_playlist_whose_marker_names_the_mix_is_taken_over(client, gateway, clock, pal):
    other = leftover(gateway, "Mine", MARKER.replace("On Repeat", "Someone else's Mix"))
    mine = leftover(gateway, "Renamed", MARKER)
    five_plays(gateway, clock)

    refresh(client)

    assert on_repeat(gateway) is None  # no new one was made
    playlists = {p.id: p for p in gateway.playlists_of(PAL)}
    assert (playlists[mine].title, playlists[mine].items) == ("Renamed", ["t1", "t2", "t3", "t4", "t5"])
    assert playlists[other].items == ["t40"]
    assert "recreated" not in client.get("/listeners/8?tab=history").text


def test_a_playlist_whose_marker_was_changed_is_not_taken_over(client, gateway, clock, pal):
    edited = leftover(gateway, "On Repeat", "my own notes")
    five_plays(gateway, clock)

    refresh(client)

    kept, new = [p for p in gateway.playlists_of(PAL) if p.title == "On Repeat"]
    assert (kept.id, kept.items) == (edited, ["t40"])
    assert new.items == ["t1", "t2", "t3", "t4", "t5"]


def mix_off(client, **extra):
    csrf = csrf_from(client.get("/listeners/8").text)
    data = {"csrf_token": csrf, "window_days": "30", "length": "30"} | extra
    return client.post("/listeners/8/mixes/on_repeat", data=data)


def test_turning_a_mix_off_asks_first_then_deletes_its_playlists_from_plex(client, gateway, clock, pal):
    five_plays(gateway, clock)
    refresh(client)

    asked = mix_off(client)
    assert "Turn On Repeat off?" in asked.text and "deleted from Plex" in asked.text
    assert on_repeat(gateway) is not None  # asking changed nothing
    assert "checked" in client.get("/listeners/8").text.split('name="enabled"')[1].split(">")[0]

    mix_off(client, confirm="yes", csrf_token=csrf_from(asked.text))

    assert on_repeat(gateway) is None
    assert "checked" not in client.get("/listeners/8").text.split('name="enabled"')[1].split(">")[0]
    refresh(client)
    assert on_repeat(gateway) is None


def test_turning_a_listener_off_deletes_all_their_playlists_from_plex(client, gateway, clock, pal):
    five_plays(gateway, clock)
    refresh(client)
    theirs = leftover(gateway, "Road trip", "not ampanion's")

    client.post("/listeners/8/turn-off", data={"csrf_token": csrf_from(client.get("/listeners/8/turn-off").text)})

    assert [p.id for p in gateway.playlists_of(PAL)] == [theirs]
    assert client.get("/listeners/8").status_code == 404


def test_the_playlists_tab_lists_what_ampanion_has_on_plex(client, gateway, clock, pal):
    assert "hasn't made any Playlists" in client.get("/listeners/8?tab=playlists").text
    five_plays(gateway, clock)
    refresh(client)
    jams = on_repeat(gateway).id
    gateway.rename_playlist(PAL, jams, "My jams")
    leftover(gateway, "Road trip", "not ampanion's")

    html = client.get("/listeners/8?tab=playlists").text

    assert "<td>My jams</td><td>5</td><td>2026-01-05 12:00</td>" in html
    assert "Road trip" not in html
    gateway.delete_playlist(URL, PAL, jams)  # gone from Plex: no longer listed
    assert "My jams" not in client.get("/listeners/8?tab=playlists").text


def test_a_mix_with_no_playlist_on_plex_turns_off_without_asking(client, gateway, clock, pal):
    response = mix_off(client)

    assert response.url.path == "/listeners/8"
    assert "checked" not in response.text.split('name="enabled"')[1].split(">")[0]
