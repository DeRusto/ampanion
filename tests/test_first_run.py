import uuid

from tests.test_sign_in import plex_auth_params


def client_id(client) -> str:
    """The client identifier plex.tv is given when the Owner signs in."""
    return plex_auth_params(client.get("/signin/plex", follow_redirects=False))["clientID"]


def test_first_run_creates_the_database_in_config(start_app, config_dir):
    with start_app():
        pass

    assert (config_dir / "ampanion.db").is_file()


def test_first_run_generates_a_client_identifier(start_app):
    with start_app() as client:
        uuid.UUID(client_id(client))  # a valid UUID


def test_restart_keeps_the_client_identifier_and_the_session_secret(start_app, gateway, sign_in):
    gateway.add_account("dee", token="owner-token")
    gateway.add_server(owner_token="owner-token")
    with start_app() as client:
        sign_in(client, "owner-token")
        first = client_id(client)
        cookies = client.cookies

    with start_app() as client:
        client.cookies = cookies
        assert client_id(client) == first
        # the session cookie, signed with the session secret, still signs the Owner in
        assert client.get("/", follow_redirects=False).status_code == 200
