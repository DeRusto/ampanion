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

## Check the Gateway against a live server

`tests/test_live_gateway.py` runs every operation of the real `PlexGateway` against a live test Plex server, and optionally Tautulli. It is skipped unless its environment variables are set, so normal runs and CI never need a server. Run it by hand when you change the Gateway or upgrade Plex:

```sh
export AMPANION_LIVE_PLEX_URL=http://192.168.1.10:32400   # the test server
export AMPANION_LIVE_PLEX_TOKEN=...                       # the Owner's Plex token
# Optional:
export AMPANION_LIVE_FRIEND=someone                       # plex.tv username of a friend (not Home) with a share
export AMPANION_LIVE_TAUTULLI_URL=http://192.168.1.10:8181
export AMPANION_LIVE_TAUTULLI_KEY=...
export AMPANION_LIVE_LATE_PLAY=12345@2026-10-20T18:30     # see below
.venv/bin/python -m pytest tests/test_live_gateway.py -rs
```

- Use a test server: the suite creates Playlists titled `ampanion live check` in the Owner's (and friend's) account and deletes them again.
- Lines marked `[live]` report history depth, track and genre counts. `-rs` lists which checks were skipped for lack of a variable.
- Late Plexamp plays: on the Owner's account, play a track in Plexamp offline, note its rating key and the local time, go online so it syncs, then set `AMPANION_LIVE_LATE_PLAY=<ratingKey>@<time>`. The check fails if Plex logged the Play at its sync time.

## Run it locally

```sh
AMPANION_CONFIG=./config .venv/bin/python -m ampanion   # http://localhost:8484
docker build -t ampanion . && docker run -p 8484:8484 -v ./ampanion:/config ampanion
```
