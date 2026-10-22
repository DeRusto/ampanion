"""Drives a whole ampanion through the test seam: fake Gateway, fake clock, seeded random generator.

The pattern for every later test: set up the world on `gateway` (and `clock`), then drive the app with `client`
or `start_app`, and check what the Owner or Plex would see.
"""

from tests.test_first_run import client_id


def test_a_fresh_ampanion_comes_up_serves_its_pages_and_survives_a_restart(start_app, gateway, clock, sign_in):
    gateway.add_account("owner", token="owner-token")
    gateway.add_server(owner_token="owner-token")

    with start_app() as client:
        assert client.get("/health").status_code == 200
        sign_in(client, "owner-token")
        assert ">Home<" in client.get("/").text
        assert "<h1>Settings</h1>" in client.get("/settings").text
        identifier = client_id(client)
        cookies = client.cookies

    clock.advance(days=1)

    with start_app() as client:
        assert client.get("/health").status_code == 200
        assert client_id(client) == identifier  # plex.tv still sees the same app
        client.cookies = cookies
        assert ">Home<" in client.get("/").text  # and the Owner is still signed in
