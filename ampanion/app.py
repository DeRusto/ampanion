from pathlib import Path
from random import Random

from fastapi import Depends, FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from ampanion.auth import PUBLIC_PATHS, SESSION_MAX_AGE, add_sign_in_routes, csrf_token, session_guard
from ampanion.clock import Clock
from ampanion.gateway import Gateway, GatewayError
from ampanion.listeners import add_listener_routes, sidebar_users
from ampanion.refresh import add_refresh_routes
from ampanion.server import add_server_routes, choose, owned_servers
from ampanion.store import Store

HERE = Path(__file__).parent


def create_app(*, config_dir: Path, gateway: Gateway, clock: Clock, rng: Random) -> FastAPI:
    """Build ampanion. The Gateway, clock and random generator are injected so tests can fake them."""
    store = Store.open(config_dir)

    def sidebar(request: Request) -> dict:
        if request.url.path in PUBLIC_PATHS or store.server_machine_id is None:
            return {"sidebar_users": [], "sidebar_error": None}
        users, error = sidebar_users(store, gateway)
        return {"sidebar_users": users, "sidebar_error": error}

    templates = Jinja2Templates(
        directory=HERE / "templates",
        context_processors=[
            lambda request: {"csrf_token": csrf_token(store.session_secret, request.session)},
            sidebar,
        ],
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
    add_server_routes(app, store=store, gateway=gateway)
    add_listener_routes(app, store=store, gateway=gateway, clock=clock, templates=templates)
    add_refresh_routes(app, store=store, gateway=gateway, clock=clock, rng=rng)

    @app.get("/health")
    def health():
        store.ping()
        return {"status": "ok"}

    @app.get("/")
    def home(request: Request):
        servers, plex_error = [], None
        if store.server_machine_id is None:
            try:
                servers = owned_servers(gateway, store.owner_token)
            except GatewayError:
                plex_error = "Couldn't reach Plex to list your servers. Try again."
            if len(servers) == 1:
                choose(store, gateway, servers[0])
        checklist = {
            "server": store.server_machine_id is not None,
            "tautulli": False,  # shortcut: Tautulli isn't connectable yet, its ticket ticks this
            "listener": bool(store.listeners()),
        }
        context = {
            "page": "home",
            "checklist": checklist,
            # Tautulli is optional, so setup is complete without it.
            "setup_complete": checklist["server"] and checklist["listener"],
            "servers": servers,
            "plex_error": plex_error,
            "refreshes": store.refreshes(),
        }
        return templates.TemplateResponse(request, "home.html", context)

    @app.get("/settings")
    def settings(request: Request):
        return templates.TemplateResponse(request, "settings.html", {"page": "settings", "store": store})

    return app
