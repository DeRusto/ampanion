"""Owner sign-in: Plex's PIN forwarding flow, the first-run Owner claim, the session cookie and CSRF."""

import base64
import json
import logging
from urllib.parse import parse_qs, urlsplit

NO_SERVER = "This Plex account doesn&#39;t own a Plex Media Server"


def plex_auth_params(response) -> dict[str, str]:
    """The query that sign-in hands to Plex's auth page (it lives in the URL fragment)."""
    location = urlsplit(response.headers["location"])
    assert (location.scheme, location.netloc, location.path) == ("https", "app.plex.tv", "/auth")
    return {k: v[0] for k, v in parse_qs(location.fragment.removeprefix("?")).items()}


def session_payload(client) -> dict:
    signed = client.cookies["ampanion_session"]
    data = signed.split(".")[0]
    return json.loads(base64.b64decode(data + "=" * (-len(data) % 4)))


def test_sign_in_sends_the_browser_to_plex_with_a_callback_on_the_url_it_used(client, gateway):
    response = client.get("http://ampanion.lan:8484/signin/plex", follow_redirects=False)

    params = plex_auth_params(response)
    assert params["forwardUrl"] == "http://ampanion.lan:8484/signin/callback"
    assert params["clientID"]  # ampanion's own client identifier (see test_first_run.py)
    assert gateway.pin_by_code(params["code"])  # a PIN made on plex.tv


def test_fresh_install_the_first_account_owning_a_server_becomes_the_owner(start_app, gateway, sign_in):
    gateway.add_account("dee", token="owner-token", account_id=42)
    gateway.add_server(owner_token="owner-token", name="Basement")

    with start_app() as client:
        response = sign_in(client, "owner-token")
        assert response.url.path == "/"
        assert "<h1>Home</h1>" in response.text

    with start_app() as client:  # stored: the Owner signs in again after a restart
        assert sign_in(client, "owner-token").url.path == "/"
        assert "dee" in client.get("/settings").text


def test_fresh_install_an_account_owning_no_server_is_refused_and_nothing_is_stored(client, gateway, sign_in):
    gateway.add_account("friend", token="friend-token")
    gateway.add_account("owner", token="owner-token")
    gateway.add_server(owner_token="owner-token")

    response = sign_in(client, "friend-token")

    assert response.status_code == 403
    assert NO_SERVER in response.text
    assert client.get("/", follow_redirects=False).headers["location"] == "/signin"
    # Nothing was stored: a real Owner can still claim this install.
    assert sign_in(client, "owner-token").url.path == "/"


def test_later_sign_ins_by_any_other_account_are_refused(client, gateway, sign_in):
    gateway.add_account("owner", token="owner-token")
    gateway.add_server(owner_token="owner-token")
    gateway.add_account("other-owner", token="other-token")
    gateway.add_server(owner_token="other-token")
    sign_in(client, "owner-token")
    client.cookies.clear()

    response = sign_in(client, "other-token")

    assert response.status_code == 403
    assert client.get("/", follow_redirects=False).headers["location"] == "/signin"


def test_an_abandoned_plex_sign_in_is_not_a_session(client, gateway):
    client.get("/signin/plex", follow_redirects=False)  # PIN never approved on plex.tv

    response = client.get("/signin/callback")

    assert response.status_code == 403
    assert client.get("/", follow_redirects=False).headers["location"] == "/signin"


def test_the_callback_reads_the_pin_from_the_session_not_the_url(client, gateway, sign_in):
    gateway.add_account("owner", token="owner-token")
    gateway.add_server(owner_token="owner-token")
    params = plex_auth_params(client.get("/signin/plex", follow_redirects=False))
    gateway.approve_pin(params["code"], "owner-token")
    client.cookies.clear()  # a different browser lands on the callback

    assert client.get("/signin/callback").status_code == 403


def test_every_page_but_sign_in_and_health_needs_a_session(client):
    assert client.get("/health").status_code == 200
    assert client.get("/signin").status_code == 200
    for path in ("/", "/settings"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/signin"


def test_the_session_cookie_is_signed_httponly_lax_14_days_and_holds_no_token(client, signed_in):
    cookie = next(h for k, h in signed_in.headers.multi_items() if k == "set-cookie" and "ampanion_session" in h)

    attributes = cookie.lower()
    assert "httponly" in attributes
    assert "samesite=lax" in attributes
    assert f"max-age={14 * 24 * 3600}" in attributes
    assert set(session_payload(client)) == {"owner_account_id", "login_at"}
    assert "owner-token" not in cookie


def test_a_tampered_session_cookie_is_not_a_session(client, signed_in):
    signed = client.cookies["ampanion_session"]
    forged = base64.b64encode(json.dumps({"owner_account_id": 42, "login_at": "x"}).encode()).decode()
    client.cookies.set("ampanion_session", forged + "." + signed.split(".", 1)[1])

    assert client.get("/", follow_redirects=False).status_code == 303


def test_state_changing_requests_without_a_valid_csrf_token_are_rejected(client, signed_in):
    assert client.post("/signout").status_code == 403
    assert client.post("/signout", headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert client.get("/").status_code == 200  # still signed in


def test_htmx_requests_carry_the_csrf_token(client, signed_in):
    html = client.get("/").text
    token = json.loads(html.split("hx-headers='")[1].split("'")[0])["X-CSRF-Token"]

    response = client.post("/signout", headers={"X-CSRF-Token": token}, follow_redirects=False)

    assert response.status_code == 303


def test_sign_out_ends_the_session(client, signed_in):
    client.post("/signout", data={"csrf_token": csrf_from(client.get("/settings").text)})

    assert client.get("/", follow_redirects=False).headers["location"] == "/signin"


def test_the_owner_token_never_reaches_the_browser_or_the_logs(client, gateway, sign_in, caplog):
    gateway.add_account("owner", token="owner-token")
    gateway.add_server(owner_token="owner-token")
    caplog.set_level(logging.DEBUG)

    responses = [client.get("/signin/plex", follow_redirects=False)]
    sign_in(client, "owner-token")
    responses += [client.get(p) for p in ("/", "/settings", "/signin", "/health")]

    for response in responses:
        assert "owner-token" not in response.text
        assert "owner-token" not in str(response.headers)
    assert "owner-token" not in caplog.text


def csrf_from(html: str) -> str:
    return html.split('name="csrf_token" value="')[1].split('"')[0]
