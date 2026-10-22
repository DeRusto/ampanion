"""The Gateway's contract: the fake and the real one must agree on results and typed errors."""

from datetime import datetime, timezone

import httpx
import pytest

from ampanion.gateway import (
    GatewayError,
    HistoryEntry,
    PlexAccount,
    PlexConnection,
    PlexGateway,
    PlexPlaylist,
    PlexResource,
    TokenExpired,
    Unreachable,
)
from tests.fakes import FakeGateway


def plex_tv(status: int, body: dict | None = None) -> PlexGateway:
    transport = httpx.MockTransport(lambda request: httpx.Response(status, json=body or {}))
    return PlexGateway(client_identifier="test-client", http=httpx.Client(transport=transport))


def fake_with_owner() -> FakeGateway:
    gateway = FakeGateway()
    gateway.add_account("owner", token="owner-token", account_id=42)
    return gateway


def test_a_valid_token_names_its_account():
    expected = PlexAccount(id=42, username="owner")

    assert fake_with_owner().check_token("owner-token") == expected
    assert plex_tv(200, {"id": 42, "username": "owner"}).check_token("owner-token") == expected


def test_an_expired_owner_token_is_told_apart_from_other_failures():
    fake = fake_with_owner()
    fake.expire_token("owner-token")

    for gateway in (fake, plex_tv(401)):
        with pytest.raises(TokenExpired):
            gateway.check_token("owner-token")


def test_other_failures_are_gateway_errors_but_not_token_expiry():
    with pytest.raises(Unreachable):
        plex_tv(503).check_token("owner-token")

    assert issubclass(TokenExpired, GatewayError)
    assert issubclass(Unreachable, GatewayError)
    assert not issubclass(Unreachable, TokenExpired)


def recording_plex_tv(body) -> tuple[PlexGateway, list[httpx.Request]]:
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=body)

    return PlexGateway(client_identifier="test-client", http=httpx.Client(transport=httpx.MockTransport(handle))), requests


def test_a_sign_in_pin_is_strong_and_its_token_is_read_back_once_plex_attaches_it():
    real, requests = recording_plex_tv({"id": 7, "code": "abcd"})
    assert real.create_pin().code == "abcd"
    assert requests[0].method == "POST" and requests[0].url.params["strong"] == "true"
    assert requests[0].headers["X-Plex-Client-Identifier"] == "test-client"

    assert plex_tv(200, {"id": 7, "authToken": None}).pin_token(7) is None
    assert plex_tv(200, {"id": 7, "authToken": "tok"}).pin_token(7) == "tok"

    fake = fake_with_owner()
    pin = fake.create_pin()
    assert fake.pin_token(pin.id) is None
    fake.approve_pin(pin.code, "owner-token")
    assert fake.pin_token(pin.id) == "owner-token"


def test_resources_say_which_servers_the_account_owns():
    real, requests = recording_plex_tv(
        [
            {
                "name": "Basement",
                "clientIdentifier": "m1",
                "provides": "server",
                "owned": True,
                "connections": [
                    {"uri": "http://192.168.1.20:32400", "local": True, "relay": False},
                    {"uri": "https://relay.plex.direct:8443", "local": False, "relay": True},
                ],
            },
            {"name": "Phone", "clientIdentifier": "p1", "provides": "client,player", "owned": True},
        ]
    )
    resources = real.resources("owner-token")
    assert requests[0].headers["X-Plex-Token"] == "owner-token"
    assert requests[0].url.params["includeRelay"] == "1"
    connections = (
        PlexConnection(uri="http://192.168.1.20:32400", local=True, relay=False),
        PlexConnection(uri="https://relay.plex.direct:8443", local=False, relay=True),
    )
    assert resources[0] == PlexResource(
        name="Basement", client_identifier="m1", provides="server", owned=True, connections=connections
    )

    fake = fake_with_owner()
    fake.add_server(owner_token="owner-token", name="Basement", machine_id="m1", connections=list(connections))
    assert fake.resources("owner-token") == [resources[0]]


def test_a_plex_server_names_its_machine_id_or_is_unreachable():
    real, requests = recording_plex_tv({"MediaContainer": {"machineIdentifier": "m1"}})
    assert real.server_identity("http://192.168.1.20:32400", "owner-token") == "m1"
    assert str(requests[0].url) == "http://192.168.1.20:32400/identity"
    assert requests[0].headers["X-Plex-Token"] == "owner-token"
    with pytest.raises(Unreachable):
        plex_tv(503).server_identity("http://192.168.1.20:32400", "owner-token")

    fake = fake_with_owner()
    fake.add_server(owner_token="owner-token", machine_id="m1")
    assert fake.server_identity("http://m1.lan:32400", "owner-token") == "m1"
    with pytest.raises(Unreachable):
        fake.server_identity("http://elsewhere:32400", "owner-token")


def xml_plex_tv(body: str) -> tuple[PlexGateway, list[httpx.Request]]:
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, text=body, headers={"Content-Type": "application/xml"})

    return PlexGateway(client_identifier="test-client", http=httpx.Client(transport=httpx.MockTransport(handle))), requests


def test_users_are_the_owners_friends_and_home_members_by_display_name():
    real, requests = xml_plex_tv(
        '<MediaContainer><User id="7" title="Kid" username="" />'
        '<User id="8" title="Pal" username="pal1"><Server machineIdentifier="m1" /></User></MediaContainer>'
    )
    expected = [PlexAccount(id=7, username="Kid"), PlexAccount(id=8, username="Pal")]
    assert real.users("owner-token") == expected
    assert str(requests[0].url) == "https://plex.tv/api/users"
    assert requests[0].headers["X-Plex-Token"] == "owner-token"

    fake = fake_with_owner()
    fake.add_user("owner-token", "Kid", account_id=7)
    fake.add_user("owner-token", "Pal", account_id=8)
    assert fake.users("owner-token") == expected


def test_shared_tokens_are_the_server_tokens_of_users_with_a_share_on_that_server():
    real, requests = xml_plex_tv(
        '<MediaContainer><SharedServer userID="8" username="pal1" accessToken="pal-server-token" /></MediaContainer>'
    )
    assert real.shared_tokens("owner-token", "m1") == {8: "pal-server-token"}
    assert str(requests[0].url) == "https://plex.tv/api/servers/m1/shared_servers"
    with pytest.raises(Unreachable):
        xml_plex_tv("<not xml")[0].shared_tokens("owner-token", "m1")
    with pytest.raises(TokenExpired):
        plex_tv(401).shared_tokens("owner-token", "m1")

    fake = fake_with_owner()
    server = fake.add_server(owner_token="owner-token", machine_id="m1")
    fake.add_user("owner-token", "Kid", account_id=7)
    fake.add_user("owner-token", "Pal", account_id=8, share=server)
    assert fake.shared_tokens("owner-token", "m1") == {8: "Pal-server-token"}


URL = "http://pms:32400"


def plex_server(routes) -> tuple[PlexGateway, list[httpx.Request]]:
    """A Plex server answering `routes(request)` with (status, MediaContainer body or None)."""
    requests = []

    def handle(request):
        requests.append(request)
        status, body = routes(request)
        return httpx.Response(status, json={"MediaContainer": body} if body is not None else None)

    return PlexGateway(client_identifier="test-client", http=httpx.Client(transport=httpx.MockTransport(handle))), requests


def music_library(request, listings):
    """/library/sections with one music section "3", and its listings by type, paged like Plex."""
    if request.url.path == "/library/sections":
        return 200, {"Directory": [{"key": "1", "type": "movie"}, {"key": "3", "type": "artist"}]}
    items = listings[request.url.params["type"]]
    start, size = int(request.url.params["X-Plex-Container-Start"]), int(request.url.params["X-Plex-Container-Size"])
    return 200, {"totalSize": len(items), "Metadata": items[start : start + size]}


def test_history_is_read_for_an_account_with_paging_and_an_optional_start():
    entries = [
        {"historyKey": f"/h/{n}", "ratingKey": f"{n}", "type": "track", "title": f"S{n}", "grandparentTitle": "AA",
         "parentTitle": "Al", "viewedAt": 1767614400 - n}
        for n in range(1500)
    ] + [{"historyKey": "/h/m", "ratingKey": "99", "type": "movie", "title": "Film", "viewedAt": 1}]
    def page(request):
        start, size = int(request.url.params["X-Plex-Container-Start"]), int(request.url.params["X-Plex-Container-Size"])
        return 200, {"totalSize": len(entries), "Metadata": entries[start : start + size]}

    real, requests = plex_server(page)

    got = real.history(URL, "owner-token", 8, since=datetime(2026, 1, 1, tzinfo=timezone.utc))

    assert len(got) == 1500 and got[0] == HistoryEntry(
        "/h/0", "0", datetime(2026, 1, 5, 12, tzinfo=timezone.utc), "S0", "AA", "AA", "Al"
    )
    assert requests[0].url.path == "/status/sessions/history/all"
    assert requests[0].url.params["accountID"] == "8"
    assert requests[0].url.params["viewedAt>"] == "1767225600"
    assert requests[0].headers["X-Plex-Token"] == "owner-token"
    real.history(URL, "owner-token", 8, since=None)
    assert "viewedAt>" not in requests[-1].url.params

    fake = fake_with_owner()
    fake.add_track("0", artist="AA", album="Al", title="S0")
    fake.play(8, "0", at=datetime(2026, 1, 5, 12, tzinfo=timezone.utc))
    fake.play(8, "0", at=datetime(2025, 12, 1, tzinfo=timezone.utc))
    [entry] = fake.history(URL, "owner-token", 8, since=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert (entry.rating_key, entry.title, entry.album_artist, entry.album) == ("0", "S0", "AA", "Al")


def test_library_tracks_carry_their_album_and_artist_genres():
    listings = {
        "8": [{"ratingKey": 10, "Genre": [{"tag": "Rock"}]}],
        "9": [{"ratingKey": 20, "Genre": [{"tag": "Indie"}]}, {"ratingKey": 21}],
        "10": [
            {"ratingKey": 30, "guid": "plex://track/a", "title": "A", "grandparentTitle": "Band", "parentTitle": "LP",
             "parentRatingKey": 20, "grandparentRatingKey": 10, "ratingCount": 5000},
            {"ratingKey": 31, "guid": "plex://track/b", "title": "B", "originalTitle": "Guest", "grandparentTitle": "Band",
             "parentTitle": "EP", "parentRatingKey": 21, "grandparentRatingKey": 10},
        ],
    }
    real, _ = plex_server(lambda r: music_library(r, listings))

    a, b = real.library_tracks(URL, "owner-token")

    assert (a.rating_key, a.album_key, a.artist_key, a.genres, a.popularity) == ("30", "20", "10", ("Indie",), 5000)
    assert (b.artist, b.album_artist, b.genres, b.popularity) == ("Guest", "Band", ("Rock",), None)


def test_ratings_are_the_rated_tracks_albums_and_artists_of_the_token_account():
    listings = {"8": [{"ratingKey": 10, "userRating": 2.0}], "9": [], "10": [{"ratingKey": 30, "userRating": 8.0}]}
    real, requests = plex_server(lambda r: music_library(r, listings))

    assert real.ratings(URL, "pal-token") == {"10": 2.0, "30": 8.0}
    assert all(r.headers["X-Plex-Token"] == "pal-token" for r in requests)
    assert all(r.url.params["userRating>"] == "0" for r in requests if r.url.path.endswith("/all"))

    fake = fake_with_owner()
    fake.rate("owner-token", "30", stars=4)
    assert fake.ratings(URL, "owner-token") == {"30": 8.0}


def test_playlists_are_created_read_cleared_filled_and_summarised_with_the_token_account():
    def routes(request):
        match request.method, request.url.path:
            case "POST", "/playlists":
                return 200, {"Metadata": [{"ratingKey": 77}]}
            case "GET", "/playlists/77":
                return 200, {"Metadata": [{"ratingKey": 77, "title": "On Repeat", "summary": "s"}]}
            case "GET", _:
                return 404, None
        return 200, None

    real, requests = plex_server(routes)

    assert real.create_playlist(URL, "pal-token", "m1", "On Repeat", ["1", "2"]) == "77"
    assert requests[-1].url.params["uri"] == "server://m1/com.plexapp.plugins.library/library/metadata/1,2"
    assert (requests[-1].url.params["type"], requests[-1].url.params["smart"]) == ("audio", "0")
    assert real.playlist(URL, "pal-token", "77") == PlexPlaylist("77", "On Repeat", "s")
    assert real.playlist(URL, "pal-token", "78") is None
    real.clear_playlist(URL, "pal-token", "77")
    assert (requests[-1].method, requests[-1].url.path) == ("DELETE", "/playlists/77/items")
    real.add_to_playlist(URL, "pal-token", "m1", "77", ["3"])
    assert (requests[-1].method, requests[-1].url.params["uri"]) == ("PUT", "server://m1/com.plexapp.plugins.library/library/metadata/3")
    real.set_playlist_summary(URL, "pal-token", "77", "Made by ampanion")
    assert (requests[-1].method, requests[-1].url.path, requests[-1].url.params["summary"]) == ("PUT", "/playlists/77", "Made by ampanion")
    assert all(r.headers["X-Plex-Token"] == "pal-token" for r in requests)
    with pytest.raises(TokenExpired):
        plex_server(lambda r: (401, None))[0].clear_playlist(URL, "pal-token", "77")

    fake = fake_with_owner()
    playlist_id = fake.create_playlist(URL, "owner-token", "m1", "On Repeat", ["1", "2"])
    fake.clear_playlist(URL, "owner-token", playlist_id)
    fake.add_to_playlist(URL, "owner-token", "m1", playlist_id, ["3"])
    fake.set_playlist_summary(URL, "owner-token", playlist_id, "s")
    assert fake.playlist(URL, "owner-token", playlist_id) == PlexPlaylist(playlist_id, "On Repeat", "s")
    assert fake.playlists_of("owner-token")[0].items == ["3"]
    assert fake.playlist(URL, "owner-token", "nope") is None
