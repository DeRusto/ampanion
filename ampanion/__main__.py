"""Run ampanion for real: `python -m ampanion`. Config lives in $AMPANION_CONFIG (default /config)."""

import logging
import os
import random
import threading
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import uvicorn

from ampanion.app import create_app
from ampanion.clock import SystemClock
from ampanion.gateway import PlexGateway
from ampanion.store import Store


def local_tz() -> ZoneInfo:
    """$TZ (an IANA name such as Europe/Berlin), else UTC."""
    try:
        return ZoneInfo(os.environ.get("TZ") or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        logging.warning("Unknown TZ %r, using UTC", os.environ["TZ"])
        return ZoneInfo("UTC")


def main() -> None:
    config_dir = Path(os.environ.get("AMPANION_CONFIG", "/config"))
    gateway = PlexGateway(client_identifier=Store.open(config_dir).client_identifier)
    app = create_app(config_dir=config_dir, gateway=gateway, clock=SystemClock(), rng=random.Random(), tz=local_tz())
    threading.Thread(target=app.state.scheduler.serve_forever, name="scheduler", daemon=True).start()
    uvicorn.run(app, host="0.0.0.0", port=8484)


if __name__ == "__main__":
    main()
