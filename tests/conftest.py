import random
from urllib.parse import parse_qs, urlsplit
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from ampanion.app import create_app
from tests.fakes import FakeClock, FakeGateway


@pytest.fixture
def config_dir(tmp_path):
    """Stands in for the container's /config volume."""
    return tmp_path / "config"


@pytest.fixture
def gateway():
    return FakeGateway()


@pytest.fixture
def clock():
    return FakeClock(datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc))


@pytest.fixture
def start_app(config_dir, gateway, clock):
    """Build ampanion on the fakes. Call it again to simulate a restart on the same /config."""

    def start():
        app = create_app(config_dir=config_dir, gateway=gateway, clock=clock, rng=random.Random(1234))
        return TestClient(app)

    return start


@pytest.fixture
def client(start_app):
    with start_app() as c:
        yield c


@pytest.fixture
def sign_in(gateway):
    """Walk a browser through Plex's PIN flow as the account holding `token`; returns the callback's response."""

    def run(client, token, follow_redirects=True):
        location = client.get("/signin/plex", follow_redirects=False).headers["location"]
        code = parse_qs(urlsplit(location).fragment.removeprefix("?"))["code"][0]
        gateway.approve_pin(code, token)
        return client.get("/signin/callback", follow_redirects=follow_redirects)

    return run


@pytest.fixture
def signed_in(client, gateway, sign_in):
    """`client`, signed in as the Owner (account 42, token "owner-token", owning one server)."""
    gateway.add_account("owner", token="owner-token", account_id=42)
    gateway.add_server(owner_token="owner-token")
    return sign_in(client, "owner-token", follow_redirects=False)
