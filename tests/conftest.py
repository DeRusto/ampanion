import random
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
