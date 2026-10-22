"""Run ampanion for real: `python -m ampanion`. Config lives in $AMPANION_CONFIG (default /config)."""

import os
import random
from pathlib import Path

import uvicorn

from ampanion.app import create_app
from ampanion.clock import SystemClock
from ampanion.gateway import PlexGateway
from ampanion.store import Store


def main() -> None:
    config_dir = Path(os.environ.get("AMPANION_CONFIG", "/config"))
    gateway = PlexGateway(client_identifier=Store.open(config_dir).client_identifier)
    app = create_app(config_dir=config_dir, gateway=gateway, clock=SystemClock(), rng=random.Random())
    uvicorn.run(app, host="0.0.0.0", port=8484)


if __name__ == "__main__":
    main()
