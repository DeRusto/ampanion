"""The Gateway's contract: the fake and the real one must agree on results and typed errors.

The real one is driven at the HTTP layer each library uses: a requests adapter under python-plexapi
(plex.tv and the Plex server answer XML), an httpx MockTransport under the PIN and Tautulli calls.
"""

from datetime import date, datetime, timezone
from urllib.parse import parse_qsl, urlsplit
from xml.sax.saxutils import quoteattr

import httpx
import pytest
import requests

from ampanion.gateway import (
    GatewayError,
    HistoryEntry,
    NotFound,
    PlexAccount,
    PlexConnection,
    PlexGateway,
    PlexPlaylist,
    PlexResource,
    TautulliRow,
    TokenExpired,
    Unreachable,
)
from tests.fakes import FakeGateway

URL = "http://pms:32400"


class PlexHTTP(requests.adapters.BaseAdapter):
    """plex.tv and a Plex server as python-plexapi sees them: `routes(request)` answers (status, XML) or None for a 404."""

    def __init__(self, routes):
        super().__init__()
        self.routes, self.requests = routes, []

    def send(self, request, **kwargs):
        self.requests.append(request)
        status, body = self.routes(request) or _defaults(request) or (404, "")
        response = requests.Response()
        response.status_code, response._content, response.url, response.request = status, body.encode(), request.url, request
        response.headers["Content-Type"] = "application/xml"
        return response

    def close(self):
        pass


def _defaults(request):
    """What plexapi reads before the call under test: the account behind a token, and a server's root."""
    match host(request), path(request):
        case "plex.tv", "/api/v2/user":
            token = quoteattr(request.headers.get("X-Plex-Token", ""))  # plexapi carries on with the token plex.tv hands back
            return 200, f'<user id="42" username="owner" authToken={token} scrobbleTypes="1"><subscription /><profile /></user>'
        case "pms:32400", "/":
            return 200, '<MediaContainer machineIdentifier="m1" />'
        case "pms:32400", "/library":
            return 200, "<MediaContainer />"


def host(request) -> str:
    return urlsplit(request.url).netloc


def path(request) -> str:
    return urlsplit(request.url).path


def params(request) -> dict[str, str]:
    return dict(parse_qsl(urlsplit(request.url).query))


def plex(routes=lambda request: None) -> tuple[PlexGateway, list[requests.PreparedRequest]]:
    adapter = PlexHTTP(routes)
    session = requests.Session()
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return PlexGateway(client_identifier="test-client", session=session), adapter.requests


def answering(status: int, body: str = ""):
    return lambda request: (status, body)


def fake_with_owner() -> FakeGateway:
    gateway = FakeGateway()
    gateway.add_account("owner", token="owner-token", account_id=42)
    return gateway


def test_a_valid_token_names_its_account():
    expected = PlexAccount(id=42, username="owner")
    real, requests_ = plex()

    assert fake_with_owner().check_token("owner-token") == expected
    assert real.check_token("owner-token") == expected
    headers = requests_[0].headers
    assert (headers["X-Plex-Token"], headers["X-Plex-Client-Identifier"], headers["X-Plex-Product"]) == ("owner-token", "test-client", "ampanion")


def test_an_expired_owner_token_is_told_apart_from_other_failures():
    fake = fake_with_owner()
    fake.expire_token("owner-token")

    for gateway in (fake, plex(answering(401))[0]):
        with pytest.raises(TokenExpired):
            gateway.check_token("owner-token")


def test_other_failures_are_gateway_errors_but_not_token_expiry():
    for routes in (answering(503), answering(404), answering(200, "<not xml")):
        with pytest.raises(Unreachable):
            plex(routes)[0].check_token("owner-token")

    def refused(request):
        raise requests.ConnectionError("no route to plex.tv")

    with pytest.raises(Unreachable):
        plex(refused)[0].check_token("owner-token")

    assert issubclass(TokenExpired, GatewayError)
    assert issubclass(Unreachable, GatewayError)
    assert not issubclass(Unreachable, TokenExpired)


def plex_tv(status: int, body: dict | None = None) -> PlexGateway:
    transport = httpx.MockTransport(lambda request: httpx.Response(status, json=body or {}))
    return PlexGateway(client_identifier="test-client", http=httpx.Client(transport=transport))


def recording_plex_tv(body) -> tuple[PlexGateway, list[httpx.Request]]:
    requests_ = []

    def handle(request):
        requests_.append(request)
        return httpx.Response(200, json=body)

    return PlexGateway(client_identifier="test-client", http=httpx.Client(transport=httpx.MockTransport(handle))), requests_


def test_a_sign_in_pin_is_strong_and_its_token_is_read_back_once_plex_attaches_it():
    real, requests_ = recording_plex_tv({"id": 7, "code": "abcd"})
    assert real.create_pin().code == "abcd"
    assert requests_[0].method == "POST" and requests_[0].url.params["strong"] == "true"
    assert requests_[0].headers["X-Plex-Client-Identifier"] == "test-client"

    assert plex_tv(200, {"id": 7, "authToken": None}).pin_token(7) is None
    assert plex_tv(200, {"id": 7, "authToken": "tok"}).pin_token(7) == "tok"
    with pytest.raises(Unreachable):
        plex_tv(503).create_pin()

    fake = fake_with_owner()
    pin = fake.create_pin()
    assert fake.pin_token(pin.id) is None
    fake.approve_pin(pin.code, "owner-token")
    assert fake.pin_token(pin.id) == "owner-token"


def test_resources_say_which_servers_the_account_owns():
    real, requests_ = plex(
        lambda r: path(r) == "/api/v2/resources"
        and (
            200,
            '<resources><resource name="Basement" clientIdentifier="m1" provides="server" owned="1"><connections>'
            '<connection uri="http://192.168.1.20:32400" local="1" relay="0" />'
            '<connection uri="https://relay.plex.direct:8443" local="0" relay="1" />'
            '</connections></resource><resource name="Phone" clientIdentifier="p1" provides="client,player" owned="1" />'
            "</resources>",
        )
    )
    resources = real.resources("owner-token")
    assert requests_[-1].headers["X-Plex-Token"] == "owner-token"
    assert params(requests_[-1])["includeRelay"] == "1"
    connections = (
        PlexConnection(uri="http://192.168.1.20:32400", local=True, relay=False),
        PlexConnection(uri="https://relay.plex.direct:8443", local=False, relay=True),
    )
    assert resources[0] == PlexResource(
        name="Basement", client_identifier="m1", provides="server", owned=True, connections=connections
    )
    assert resources[1] == PlexResource(name="Phone", client_identifier="p1", provides="client,player", owned=True)

    fake = fake_with_owner()
    fake.add_server(owner_token="owner-token", name="Basement", machine_id="m1", connections=list(connections))
    assert fake.resources("owner-token") == [resources[0]]


def test_a_plex_server_names_its_machine_id_or_is_unreachable():
    real, requests_ = plex()
    assert real.server_identity(URL, "owner-token") == "m1"
    assert requests_[0].url == f"{URL}/" and requests_[0].headers["X-Plex-Token"] == "owner-token"
    for status in (503, 401):
        with pytest.raises(Unreachable):
            plex(answering(status))[0].server_identity(URL, "owner-token")

    fake = fake_with_owner()
    fake.add_server(owner_token="owner-token", machine_id="m1")
    assert fake.server_identity("http://m1.lan:32400", "owner-token") == "m1"
    with pytest.raises(Unreachable):
        fake.server_identity("http://elsewhere:32400", "owner-token")


def test_users_are_the_owners_friends_and_home_members_by_display_name():
    real, requests_ = plex(
        lambda r: path(r) == "/api/users/"
        and (
            200,
            '<MediaContainer><User id="7" title="Kid" username="" />'
            '<User id="8" title="Pal" username="pal1"><Server machineIdentifier="m1" /></User></MediaContainer>',
        )
    )
    expected = [PlexAccount(id=7, username="Kid"), PlexAccount(id=8, username="Pal")]
    assert real.users("owner-token") == expected
    assert requests_[-1].headers["X-Plex-Token"] == "owner-token"

    fake = fake_with_owner()
    fake.add_user("owner-token", "Kid", account_id=7)
    fake.add_user("owner-token", "Pal", account_id=8)
    assert fake.users("owner-token") == expected


def test_shared_tokens_are_the_server_tokens_of_users_with_a_share_on_that_server():
    def shares(body):
        return lambda r: path(r) == "/api/servers/m1/shared_servers" and (200, body)

    real, requests_ = plex(
        shares(
            '<MediaContainer><SharedServer userID="8" username="pal1" accessToken="pal-server-token" />'
            '<SharedServer userID="9" username="pending" /></MediaContainer>'
        )
    )
    assert real.shared_tokens("owner-token", "m1") == {8: "pal-server-token"}
    assert requests_[-1].headers["X-Plex-Token"] == "owner-token"
    with pytest.raises(Unreachable):
        plex(shares("<not xml"))[0].shared_tokens("owner-token", "m1")
    with pytest.raises(TokenExpired):
        plex(lambda r: path(r) == "/api/servers/m1/shared_servers" and (401, ""))[0].shared_tokens("owner-token", "m1")

    fake = fake_with_owner()
    server = fake.add_server(owner_token="owner-token", machine_id="m1")
    fake.add_user("owner-token", "Kid", account_id=7)
    fake.add_user("owner-token", "Pal", account_id=8, share=server)
    assert fake.shared_tokens("owner-token", "m1") == {8: "Pal-server-token"}


def xml(tag: str, items: list[dict], children=lambda item: "") -> list[str]:
    return [f"<{tag} {' '.join(f'{k}={quoteattr(str(v))}' for k, v in item.items())}>{children(item)}</{tag}>" for item in items]


def paged(request, elements: list[str]) -> tuple[int, str]:
    """A listing paged like Plex: by the X-Plex-Container-Start/Size headers, with its totalSize."""
    start, size = int(request.headers["X-Plex-Container-Start"]), int(request.headers["X-Plex-Container-Size"])
    return 200, f'<MediaContainer totalSize="{len(elements)}">{"".join(elements[start : start + size])}</MediaContainer>'


def genres(item: dict) -> str:
    return "".join(f'<Genre tag="{g}" />' for g in item.pop("genres", []))


SECTIONS = (
    '<MediaContainer><Directory key="1" type="movie" title="Films" />'
    '<Directory key="3" type="artist" title="Music" /></MediaContainer>'
)
META = (  # what /library/sections/3/all?includeMeta=1 tells plexapi it may filter on
    '<MediaContainer size="0"><Meta>'
    '<Type type="artist"><Field key="userRating" type="integer" /></Type>'
    '<Type type="album"><Field key="album.userRating" type="integer" /></Type>'
    '<Type type="track"><Field key="track.userRating" type="integer" /></Type>'
    '<FieldType type="integer"><Operator key="&gt;&gt;=" title="is greater than" /></FieldType>'
    "</Meta></MediaContainer>"
)


def music_library(listings: dict[str, list[str]]):
    """/library/sections with one music section "3", and its listings by type number."""

    def routes(request):
        if path(request) == "/library/sections":
            return 200, SECTIONS
        if "includeMeta" in params(request):
            return 200, META if path(request).endswith("/all") else '<MediaContainer size="0" />'
        if path(request) == "/library/sections/3/all":
            return paged(request, listings[params(request)["type"]])

    return routes


def test_history_is_read_for_an_account_with_paging_and_an_optional_start():
    entries = xml(
        "Track",
        [
            {"historyKey": f"/h/{n}", "ratingKey": n, "type": "track", "title": f"S{n}", "grandparentTitle": "AA",
             "parentTitle": "Al", "viewedAt": 1767614400 - n, "accountID": 8}
            for n in range(1500)
        ],
    ) + xml("Video", [{"historyKey": "/h/m", "ratingKey": 99, "type": "movie", "title": "Film", "viewedAt": 1}])
    real, requests_ = plex(lambda r: path(r) == "/status/sessions/history/all" and paged(r, entries))

    got = real.history(URL, "owner-token", 8, since=datetime(2026, 1, 1, tzinfo=timezone.utc))

    assert len(got) == 1500 and got[0] == HistoryEntry(
        "/h/0", "0", datetime(2026, 1, 5, 12, tzinfo=timezone.utc), "S0", "AA", "AA", "Al"
    )
    history = [r for r in requests_ if path(r) == "/status/sessions/history/all"]
    assert (params(history[0])["accountID"], params(history[0])["viewedAt>"]) == ("8", "1767225600")
    assert all(r.headers["X-Plex-Token"] == "owner-token" for r in requests_)
    real.history(URL, "owner-token", 8, since=None)
    assert "viewedAt>" not in params(requests_[-1])
    with pytest.raises(TokenExpired):
        plex(lambda r: path(r) == "/status/sessions/history/all" and (401, ""))[0].history(URL, "owner-token", 8, None)

    fake = fake_with_owner()
    fake.add_track("0", artist="AA", album="Al", title="S0")
    fake.play(8, "0", at=datetime(2026, 1, 5, 12, tzinfo=timezone.utc))
    fake.play(8, "0", at=datetime(2025, 12, 1, tzinfo=timezone.utc))
    [entry] = fake.history(URL, "owner-token", 8, since=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert (entry.rating_key, entry.title, entry.album_artist, entry.album) == ("0", "S0", "AA", "Al")


def test_library_tracks_carry_their_album_and_artist_genres():
    listings = {
        "8": xml("Directory", [{"type": "artist", "ratingKey": 10, "genres": ["Rock"]}], genres),
        "9": xml("Directory", [{"type": "album", "ratingKey": 20, "genres": ["Indie"]}, {"type": "album", "ratingKey": 21}], genres),
        "10": xml("Track", [
            {"type": "track", "ratingKey": 30, "guid": "plex://track/a", "title": "A", "grandparentTitle": "Band",
             "parentTitle": "LP", "parentRatingKey": 20, "grandparentRatingKey": 10, "ratingCount": 5000},
            {"type": "track", "ratingKey": 31, "guid": "plex://track/b", "title": "B", "originalTitle": "Guest",
             "grandparentTitle": "Band", "parentTitle": "EP", "parentRatingKey": 21, "grandparentRatingKey": 10},
        ]),
    }
    real, requests_ = plex(music_library(listings))

    a, b = real.library_tracks(URL, "owner-token")

    assert (a.rating_key, a.guid, a.album_key, a.artist_key, a.genres, a.popularity) == ("30", "plex://track/a", "20", "10", ("Indie",), 5000)
    assert (b.artist, b.album_artist, b.album, b.genres, b.popularity) == ("Guest", "Band", "EP", ("Rock",), None)
    listing = [r for r in requests_ if path(r) == "/library/sections/3/all"]
    assert {r.headers["X-Plex-Container-Size"] for r in listing} == {"1000"}  # a large container_size
    assert not [r for r in requests_ if path(r).startswith("/library/metadata")]  # autoreload off: no request per item


def test_ratings_are_the_rated_tracks_albums_and_artists_of_the_token_account():
    listings = {
        "8": xml("Directory", [{"type": "artist", "ratingKey": 10, "userRating": 2.0}]),
        "9": [],
        "10": xml("Track", [{"type": "track", "ratingKey": 30, "userRating": 8.0}]),
    }
    real, requests_ = plex(music_library(listings))

    assert real.ratings(URL, "pal-token") == {"10": 2.0, "30": 8.0}
    assert all(r.headers["X-Plex-Token"] == "pal-token" for r in requests_)
    searches = [params(r) for r in requests_ if path(r) == "/library/sections/3/all" and "includeMeta" not in params(r)]
    assert len(searches) == 3 and all(  # filtered by the server: rated items only
        any(k.endswith("userRating>>") and v == "0" for k, v in p.items()) for p in searches
    )

    fake = fake_with_owner()
    fake.rate("owner-token", "30", stars=4)
    assert fake.ratings(URL, "owner-token") == {"30": 8.0}


PLAYLIST = '<MediaContainer size="1"><Playlist ratingKey="77" key="/playlists/77/items" type="playlist" playlistType="audio" smart="0" title="On Repeat" summary="s" /></MediaContainer>'


def test_playlists_are_created_read_cleared_filled_and_summarised_with_the_token_account():
    def routes(request):
        match request.method, path(request):
            case "POST", "/playlists":
                return 200, PLAYLIST
            case "GET", "/playlists/77":
                return 200, PLAYLIST
            case "DELETE" | "PUT", "/playlists/77/items" | "/playlists/77":
                return 200, ""

    real, requests_ = plex(routes)

    assert real.create_playlist(URL, "pal-token", "m1", "On Repeat", ["1", "2"]) == "77"
    created = params(requests_[-1])
    assert created["uri"] == "server://m1/com.plexapp.plugins.library/library/metadata/1,2"
    assert (created["type"], created["smart"], created["title"]) == ("audio", "0", "On Repeat")
    assert real.playlist(URL, "pal-token", "77") == PlexPlaylist("77", "On Repeat", "s")
    assert real.playlist(URL, "pal-token", "78") is None
    real.clear_playlist(URL, "pal-token", "77")
    assert (requests_[-1].method, path(requests_[-1])) == ("DELETE", "/playlists/77/items")
    real.add_to_playlist(URL, "pal-token", "m1", "77", ["3", "4"])
    assert (requests_[-1].method, params(requests_[-1])["uri"]) == ("PUT", "server://m1/com.plexapp.plugins.library/library/metadata/3,4")
    real.set_playlist_summary(URL, "pal-token", "77", "Made by ampanion")
    assert (requests_[-1].method, path(requests_[-1]), params(requests_[-1])["summary.value"]) == ("PUT", "/playlists/77", "Made by ampanion")
    assert all(r.headers["X-Plex-Token"] == "pal-token" for r in requests_)
    assert not [r for r in requests_ if path(r).startswith("/library/metadata")]  # tracks are named, never fetched
    with pytest.raises(TokenExpired):
        plex(lambda r: r.method == "DELETE" and (401, ""))[0].clear_playlist(URL, "pal-token", "77")
    with pytest.raises(NotFound):
        plex()[0].add_to_playlist(URL, "pal-token", "m1", "78", ["3"])
    with pytest.raises(Unreachable):
        plex(lambda r: r.method == "DELETE" and (500, ""))[0].clear_playlist(URL, "pal-token", "77")

    fake = fake_with_owner()
    playlist_id = fake.create_playlist(URL, "owner-token", "m1", "On Repeat", ["1", "2"])
    fake.clear_playlist(URL, "owner-token", playlist_id)
    fake.add_to_playlist(URL, "owner-token", "m1", playlist_id, ["3"])
    fake.set_playlist_summary(URL, "owner-token", playlist_id, "s")
    assert fake.playlist(URL, "owner-token", playlist_id) == PlexPlaylist(playlist_id, "On Repeat", "s")
    assert fake.playlists_of("owner-token")[0].items == ["3"]
    assert fake.playlist(URL, "owner-token", "nope") is None


def test_the_token_accounts_audio_playlists_are_listed_and_deleted():
    def routes(request):
        match request.method, path(request):
            case "GET", "/playlists":
                return 200, PLAYLIST
            case "GET", "/playlists/77":
                return 200, PLAYLIST
            case "DELETE", "/playlists/77":
                return 200, ""

    real, requests_ = plex(routes)

    assert real.playlists(URL, "pal-token") == [PlexPlaylist("77", "On Repeat", "s")]
    assert params(requests_[-1])["playlistType"] == "audio"
    real.delete_playlist(URL, "pal-token", "77")
    assert (requests_[-1].method, path(requests_[-1])) == ("DELETE", "/playlists/77")
    with pytest.raises(NotFound):
        real.delete_playlist(URL, "pal-token", "78")

    fake = fake_with_owner()
    playlist_id = fake.create_playlist(URL, "owner-token", "m1", "On Repeat", ["1"])
    assert fake.playlists(URL, "owner-token") == [PlexPlaylist(playlist_id, "On Repeat", "")]
    fake.delete_playlist(URL, "owner-token", playlist_id)
    assert fake.playlists(URL, "owner-token") == []
    with pytest.raises(NotFound):
        fake.delete_playlist(URL, "owner-token", playlist_id)


def tautulli(routes) -> tuple[PlexGateway, list[httpx.Request]]:
    """A Tautulli answering `routes(request)` with (status, envelope data, or None for an error envelope)."""
    requests = []

    def handle(request):
        requests.append(request)
        status, data = routes(request)
        result = "success" if data is not None else "error"
        return httpx.Response(status, json={"response": {"result": result, "message": None, "data": data}})

    return PlexGateway(client_identifier="test-client", http=httpx.Client(transport=httpx.MockTransport(handle))), requests


def test_tautulli_names_its_plex_server_with_the_key_only_in_the_header():
    real, requests = tautulli(lambda r: (200, {"machine_identifier": "machine-1"}))

    assert real.tautulli_identity("http://tautulli:8181/root/", "key-123") == "machine-1"
    assert requests[0].url.path == "/root/api/v2" and requests[0].url.params["cmd"] == "get_server_identity"
    assert requests[0].headers["X-Api-Key"] == "key-123" and "key-123" not in str(requests[0].url)

    fake = FakeGateway()
    fake.add_tautulli("http://tautulli:8181", "key-123", "machine-1")
    assert fake.tautulli_identity("http://tautulli:8181", "key-123") == "machine-1"


def test_tautulli_failures_are_unreachable_never_token_expiry():
    for status, data in ((401, None), (200, None), (500, {})):
        real, _ = tautulli(lambda r: (status, data))
        with pytest.raises(Unreachable):
            real.tautulli_identity("http://tautulli:8181", "key")

    fake = FakeGateway()
    fake.add_tautulli("http://tautulli:8181", "key", "machine-1")
    with pytest.raises(Unreachable):
        fake.tautulli_identity("http://tautulli:8181", "wrong")


def test_tautulli_history_reads_ungrouped_track_rows_for_a_user_after_a_date_in_pages():
    rows = [
        {"row_id": n, "rating_key": n, "started": 1767614400 - n, "title": f"S{n}", "original_title": "",
         "grandparent_title": "AA", "parent_title": "Al", "percent_complete": 20}
        for n in range(2500)
    ]

    def page(request):
        start, length = int(request.url.params["start"]), int(request.url.params["length"])
        return 200, {"recordsFiltered": len(rows), "data": rows[start : start + length]}

    real, requests = tautulli(page)

    got = real.tautulli_history("http://tautulli:8181", "key", 42, after=date(2026, 1, 1))

    assert len(got) == 2500 and got[0] == TautulliRow(
        0, "0", datetime(2026, 1, 5, 12, tzinfo=timezone.utc), "S0", "AA", "AA", "Al", 20
    )
    params = requests[0].url.params
    assert (params["cmd"], params["media_type"], params["user_id"], params["grouping"], params["after"]) == (
        "get_history", "track", "42", "0", "2026-01-01"
    )
    assert {r.url.params["cmd"] for r in requests} == {"get_history"}  # read commands only
    real.tautulli_history("http://tautulli:8181", "key", 42, after=None)
    assert "after" not in requests[-1].url.params
