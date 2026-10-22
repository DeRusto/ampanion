from pathlib import Path
from random import Random

from fastapi import Depends, FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from ampanion.auth import SESSION_MAX_AGE, add_sign_in_routes, csrf_token, session_guard
from ampanion.clock import Clock
from ampanion.gateway import Gateway
from ampanion.store import Store

HERE = Path(__file__).parent


def create_app(*, config_dir: Path, gateway: Gateway, clock: Clock, rng: Random) -> FastAPI:
    """Build ampanion. The Gateway, clock and random generator are injected so tests can fake them."""
    store = Store.open(config_dir)
    templates = Jinja2Templates(
        directory=HERE / "templates",
        context_processors=[lambda request: {"csrf_token": csrf_token(store.session_secret, request.session)}],
    )

    app = FastAPI(dependencies=[Depends(session_guard(store))])
    app.add_middleware(
        SessionMiddleware,
        secret_key=store.session_secret,
        session_cookie="ampanion_session",
        max_age=SESSION_MAX_AGE,
        same_site="lax",
    )
    app.state.store = store
    app.state.gateway = gateway
    app.state.clock = clock
    app.state.rng = rng
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    add_sign_in_routes(app, store=store, gateway=gateway, clock=clock, templates=templates)

    @app.get("/health")
    def health():
        store.ping()
        return {"status": "ok"}

    @app.get("/")
    def home(request: Request):
        return templates.TemplateResponse(request, "home.html", {"page": "home"})

    @app.get("/settings")
    def settings(request: Request):
        return templates.TemplateResponse(request, "settings.html", {"page": "settings", "owner": store.owner_username})

    return app
