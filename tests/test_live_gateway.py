"""The Gateway contract against a live Plex server (and Tautulli): run by hand, never in CI.

Skipped unless AMPANION_LIVE_PLEX_URL and AMPANION_LIVE_PLEX_TOKEN (the Owner's token) are set.
Optional: AMPANION_LIVE_FRIEND (plex.tv username of a friend, not Home, with a share on the server),
AMPANION_LIVE_TAUTULLI_URL + AMPANION_LIVE_TAUTULLI_KEY, and AMPANION_LIVE_LATE_PLAY
("<ratingKey>@<ISO time>" of a track the Owner played offline in Plexamp and synced later).
It writes only Playlists titled "ampanion live check", and deletes them again. See CONTRIBUTING.md.
"""

import os
import uuid
from datetime import datetime, timedelta

import httpx
import pytest

from ampanion.gateway import NotFound, PlexGateway, TokenExpired

URL = os.environ.get("AMPANION_LIVE_PLEX_URL", "")
TOKEN = os.environ.get("AMPANION_LIVE_PLEX_TOKEN", "")
FRIEND = os.environ.get("AMPANION_LIVE_FRIEND")
TAUTULLI_URL = os.environ.get("AMPANION_LIVE_TAUTULLI_URL")
TAUTULLI_KEY = os.environ.get("AMPANION_LIVE_TAUTULLI_KEY")
LATE_PLAY = os.environ.get("AMPANION_LIVE_LATE_PLAY")

pytestmark = pytest.mark.skipif(not (URL and TOKEN), reason="live Gateway check: set AMPANION_LIVE_PLEX_URL and AMPANION_LIVE_PLEX_TOKEN")

OWNER_HISTORY_ID = 1  # the Owner's local account id on every server
TITLE = "ampanion live check"


@pytest.fixture(scope="module")
def gateway():
    return PlexGateway(client_identifier=f"ampanion-live-check-{uuid.uuid4()}")


@pytest.fixture(scope="module")
def machine_id(gateway):
    return gateway.server_identity(URL, TOKEN)


@pytest.fixture(scope="module")
def friend(gateway, machine_id):
    """(plex.tv account, server token) of the friend, via the switchUser path."""
    if not FRIEND:
        pytest.skip("set AMPANION_LIVE_FRIEND to check a friend share")
    account = next((u for u in gateway.users(TOKEN) if u.username == FRIEND), None)
    assert account, f"{FRIEND} is not among the Owner's friends and Home members"
    token = gateway.shared_tokens(TOKEN, machine_id).get(account.id)
    assert token, f"plex.tv has no server token for {FRIEND}: no share on this server?"
    return account, token


@pytest.fixture(scope="module")
def tracks(gateway):
    tracks = gateway.library_tracks(URL, TOKEN)
    assert tracks, "the server has no music tracks"
    return tracks


def report(capsys, *lines):
    with capsys.disabled():
        print("\n" + "\n".join(f"  [live] {line}" for line in lines))


def raw(path: str, token: str = TOKEN) -> dict:
    """The server's own answer, bypassing the Gateway, to compare against."""
    response = httpx.get(f"{URL.rstrip('/')}{path}", headers={"Accept": "application/json", "X-Plex-Token": token}, timeout=30)
    response.raise_for_status()
    return response.json()["MediaContainer"]


# --- plex.tv ---


def test_the_owner_token_names_an_account_and_a_dead_one_is_told_apart(gateway):
    assert gateway.check_token(TOKEN).username
    with pytest.raises(TokenExpired):
        gateway.check_token("not-a-real-token")


def test_a_new_pin_carries_no_token_until_someone_signs_in(gateway):
    pin = gateway.create_pin()
    assert pin.code
    assert gateway.pin_token(pin.id) is None


def test_the_owner_owns_the_server_and_it_answers_at_a_listed_connection(gateway, machine_id):
    server = next(r for r in gateway.resources(TOKEN) if r.client_identifier == machine_id)
    assert server.is_server and server.owned
    assert server.connections


def test_users_and_shared_tokens_are_readable(gateway, machine_id):
    users = gateway.users(TOKEN)
    tokens = gateway.shared_tokens(TOKEN, machine_id)
    assert all(u.id and u.username for u in users)
    assert set(tokens) <= {u.id for u in users}


def test_switch_user_works_for_a_friend_share(gateway, friend):
    _, token = friend
    gateway.playlists(URL, token)  # raises TokenExpired if the server refuses the friend's token


# --- Plex server: reads ---


def test_history_depth_and_the_since_filter(gateway, capsys):
    entries = gateway.history(URL, TOKEN, OWNER_HISTORY_ID, None)
    if not entries:
        pytest.skip("the Owner has no history to check")
    report(capsys, f"Owner history: {len(entries)} Plays, {entries[-1].played_at:%Y-%m-%d} to {entries[0].played_at:%Y-%m-%d}")
    assert entries == sorted(entries, key=lambda e: e.played_at, reverse=True)

    # Checks Plex decodes the URL-encoded "viewedAt>" key: ignored, it would hand back everything.
    cutoff = entries[len(entries) // 2].played_at
    if entries[-1].played_at == cutoff:
        pytest.skip("all Plays share one time, so the since filter can't be told apart from no filter")
    recent = gateway.history(URL, TOKEN, OWNER_HISTORY_ID, cutoff)
    assert recent and all(e.played_at >= cutoff for e in recent)
    assert len(recent) >= sum(e.played_at >= cutoff for e in entries)


def test_a_friends_history_is_read_by_their_plex_tv_id(gateway, friend, capsys):
    account, _ = friend
    entries = gateway.history(URL, TOKEN, account.id, None)
    report(capsys, f"{account.username} history: {len(entries)} Plays" + (f", oldest {entries[-1].played_at:%Y-%m-%d}" if entries else ""))


def test_a_late_synced_plexamp_play_keeps_its_original_time(gateway, capsys):
    if not LATE_PLAY:
        pytest.skip("set AMPANION_LIVE_LATE_PLAY=<ratingKey>@<ISO time> after an offline Plexamp play has synced")
    rating_key, played = LATE_PLAY.split("@", 1)
    played_at = datetime.fromisoformat(played)
    played_at = played_at if played_at.tzinfo else played_at.astimezone()
    plays = [e for e in gateway.history(URL, TOKEN, OWNER_HISTORY_ID, played_at - timedelta(days=1)) if e.rating_key == rating_key]
    assert plays, f"no Play of {rating_key} since a day before {played_at}"
    closest = min(plays, key=lambda e: abs(e.played_at - played_at))
    report(capsys, f"late Play of {rating_key}: played {played_at:%Y-%m-%d %H:%M}, Plex says {closest.played_at.astimezone():%Y-%m-%d %H:%M}")
    assert abs(closest.played_at - played_at) < timedelta(minutes=15), "the Play took its sync time, not its play time"


def test_bulk_track_listings_carry_the_genres_and_popularity_of_the_items_themselves(tracks, capsys):
    sample = tracks[:: max(1, len(tracks) // 20)][:20]
    tagged = 0
    for track in sample:
        detail = {
            level: raw(f"/library/metadata/{key}")["Metadata"][0]
            for level, key in (("track", track.rating_key), ("album", track.album_key), ("artist", track.artist_key))
        }
        tags = {level: tuple(g["tag"] for g in item.get("Genre", [])) for level, item in detail.items()}
        assert (track.track_genres, track.album_genres, track.artist_genres) == (tags["track"], tags["album"], tags["artist"]), track.title
        assert track.popularity == detail["track"].get("ratingCount"), track.title
        tagged += any(tags.values())
    report(
        capsys,
        f"{len(tracks)} tracks; sampled {len(sample)}, {tagged} with genre tags",
        f"tracks with ratingCount: {sum(t.popularity is not None for t in tracks)}",
    )
    if not tagged:
        pytest.skip("no sampled track, album or artist has genre tags, so the bulk listing was not really compared")


def test_ratings_are_only_rated_items_on_the_star_scale(gateway):
    # Checks Plex decodes the "userRating>" key: ignored, unrated items would come back and fail to parse.
    assert all(0 < r <= 10 for r in gateway.ratings(URL, TOKEN).values())


def test_a_friends_ratings_are_read_with_their_shared_token(gateway, friend):
    _, token = friend
    assert all(0 < r <= 10 for r in gateway.ratings(URL, token).values())


# --- Plex server: Playlist writes ---


def playlist_lifecycle(gateway, machine_id, token, tracks):
    keys = [t.rating_key for t in tracks[:3]]
    playlist_id = gateway.create_playlist(URL, token, machine_id, TITLE, keys[:1])
    try:
        assert gateway.playlist(URL, token, playlist_id).title == TITLE
        assert playlist_id in {p.id for p in gateway.playlists(URL, token)}

        gateway.add_to_playlist(URL, token, machine_id, playlist_id, keys[1:])
        assert [str(i["ratingKey"]) for i in raw(f"/playlists/{playlist_id}/items", token).get("Metadata", [])] == keys

        gateway.set_playlist_summary(URL, token, playlist_id, "Made by ampanion: live check.")
        assert gateway.playlist(URL, token, playlist_id).summary == "Made by ampanion: live check."

        gateway.clear_playlist(URL, token, playlist_id)
        assert not raw(f"/playlists/{playlist_id}/items", token).get("Metadata")
        assert gateway.playlist(URL, token, playlist_id) is not None
    finally:
        gateway.delete_playlist(URL, token, playlist_id)
    assert gateway.playlist(URL, token, playlist_id) is None
    with pytest.raises(NotFound):
        gateway.delete_playlist(URL, token, playlist_id)


def test_the_owner_playlist_lifecycle(gateway, machine_id, tracks):
    playlist_lifecycle(gateway, machine_id, TOKEN, tracks)


def test_a_friends_playlist_lifecycle_through_their_shared_token(gateway, machine_id, tracks, friend):
    _, token = friend
    playlist_lifecycle(gateway, machine_id, token, tracks)


# --- Tautulli ---


@pytest.fixture(scope="module")
def tautulli():
    if not (TAUTULLI_URL and TAUTULLI_KEY):
        pytest.skip("set AMPANION_LIVE_TAUTULLI_URL and AMPANION_LIVE_TAUTULLI_KEY to check Tautulli")
    return TAUTULLI_URL, TAUTULLI_KEY


def test_tautulli_watches_this_server_and_lists_track_rows(gateway, machine_id, tautulli, capsys):
    url, key = tautulli
    assert gateway.tautulli_identity(url, key) == machine_id

    owner_id = gateway.check_token(TOKEN).id  # Tautulli keys users by plex.tv id, the Owner's too
    rows = gateway.tautulli_history(url, key, owner_id, None)
    report(capsys, f"Tautulli: {len(rows)} Owner track rows" + (f", oldest {rows[-1].started_at:%Y-%m-%d}" if rows else ""))
    assert rows == sorted(rows, key=lambda r: r.started_at, reverse=True)
    if rows:
        day = rows[len(rows) // 2].started_at.date()
        # A day of slack: Tautulli reads "after" in its own time zone.
        assert all(r.started_at.date() >= day - timedelta(days=1) for r in gateway.tautulli_history(url, key, owner_id, day))
