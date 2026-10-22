from pathlib import Path
from random import Random

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ampanion.clock import Clock
from ampanion.gateway import Gateway
from ampanion.store import Store

HERE = Path(__file__).parent


def create_app(*, config_dir: Path, gateway: Gateway, clock: Clock, rng: Random) -> FastAPI:
    """Build ampanion. The Gateway, clock and random generator are injected so tests can fake them."""
    store = Store.open(config_dir)
    templates = Jinja2Templates(directory=HERE / "templates")

    app = FastAPI()
    app.state.store = store
    app.state.gateway = gateway
    app.state.clock = clock
    app.state.rng = rng
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    @app.get("/health")
    def health():
        store.ping()
        return {"status": "ok"}

    @app.get("/")
    def home(request: Request):
        return templates.TemplateResponse(request, "home.html", {"page": "home"})

    @app.get("/settings")
    def settings(request: Request):
        return templates.TemplateResponse(request, "settings.html", {"page": "settings"})

    return app
