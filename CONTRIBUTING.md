# Contributing

## Run the tests

```sh
python -m venv .venv
.venv/bin/pip install -e ".[dev]"     # Windows: .venv\Scripts\pip
.venv/bin/python -m pytest            # Windows: .venv\Scripts\python -m pytest
```

## How tests are written

- Tests drive ampanion from the outside, through FastAPI's test client, and check what the Owner or Plex would see.
- The **Gateway** (`ampanion/gateway.py`) is the one test seam: ampanion's only route to plex.tv, the Plex server and Tautulli. Tests use the in-memory `FakeGateway` in `tests/fakes.py`, plus a `FakeClock` and a seeded `random.Random`, all injected through `create_app` (`ampanion/app.py`).
- Fixtures in `tests/conftest.py`: `gateway` and `clock` set up the world, `client` is a running app on a temporary `/config`, `start_app()` starts (or restarts) one on the same `/config`.
- Everything else is real: SQLite, the app, the templates. When a ticket needs a new Gateway operation, add it to the `Gateway` protocol, `PlexGateway` and `FakeGateway` together.

## Run it locally

```sh
AMPANION_CONFIG=./config .venv/bin/python -m ampanion   # http://localhost:8484
docker build -t ampanion . && docker run -p 8484:8484 -v ./ampanion:/config ampanion
```
