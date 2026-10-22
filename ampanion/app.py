from datetime import timezone, tzinfo
from pathlib import Path
from random import Random

from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from ampanion.auth import PUBLIC_PATHS, SESSION_MAX_AGE, add_sign_in_routes, csrf_token, session_guard
from ampanion.clock import Clock
from ampanion.gateway import Gateway, GatewayError
from ampanion.listeners import add_listener_routes, sidebar_people
from ampanion.mixes import MIXES, mix_cadence
from ampanion.schedule import Scheduler, add_schedule_routes
from ampanion.server import add_server_routes, choose, owned_servers
from ampanion.store import Store

HERE = Path(__file__).parent


def create_app(
    *, config_dir: Path, gateway: Gateway, clock: Clock, rng: Random, tz: tzinfo = timezone.utc
) -> FastAPI:
    """Build ampanion. The Gateway, clock and random generator are injected so tests can fake them.

    `tz` is the local time zone (TZ). The scheduler's thread isn't started here: the caller runs
    `app.state.scheduler.serve_forever()` on one, or a test calls `tick()`.
    """
    store = Store.open(config_dir)
    scheduler = Scheduler(store, gateway, clock, rng, tz)

    def sidebar(request: Request) -> dict:
        if request.url.path in PUBLIC_PATHS or store.server_machine_id is None:
            return {"sidebar_people": [], "sidebar_error": None}
        people, error = sidebar_people(store, gateway)
        return {"sidebar_people": people, "sidebar_error": error}

    templates = Jinja2Templates(
        directory=HERE / "templates",
        context_processors=[
            lambda request: {"csrf_token": csrf_token(store.session_secret, request.session)},
            sidebar,
            lambda request: {"scheduler": scheduler, "cadences": {k: mix_cadence(store, m) for k, m in MIXES.items()}},
            lambda request: {"plex_expired": store.owner_token_expired},
        ],
    )
    templates.env.filters["local"] = lambda dt: dt.astimezone(tz)

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
    app.state.scheduler = scheduler
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    add_sign_in_routes(app, store=store, gateway=gateway, clock=clock, templates=templates, scheduler=scheduler)
    add_server_routes(app, store=store, gateway=gateway)
    add_listener_routes(app, store=store, gateway=gateway, clock=clock, templates=templates, scheduler=scheduler)
    add_schedule_routes(app, store=store, scheduler=scheduler, templates=templates)

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
            "server": store.server_machine_id is not None and not store.owner_token_expired,
            "tautulli": store.tautulli_url is not None,
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

    def settings_page(request: Request, status_code: int = 200, **extra):
        context = {"page": "settings", "store": store, "mixes": MIXES.values()} | extra
        return templates.TemplateResponse(request, "settings.html", context, status_code=status_code)

    @app.get("/settings")
    def settings(request: Request):
        return settings_page(request)

    @app.post("/settings/tautulli")
    def connect_tautulli(request: Request, url: str = Form(""), api_key: str = Form("")):
        """Test, then save: only a Tautulli watching the chosen Plex server is kept. A blank key keeps the saved one."""
        url, api_key = url.strip(), api_key.strip() or store.tautulli_api_key or ""
        if urlsplit(url).scheme not in ("http", "https") or not urlsplit(url).netloc:
            error = "The Tautulli URL must start with http:// or https://"
        elif not api_key:
            error = "Enter Tautulli's API key"
        elif store.server_machine_id is None:
            error = "Choose the Plex server first"
        else:
            try:
                same = gateway.tautulli_identity(url, api_key) == store.server_machine_id
                error = None if same else "That Tautulli isn't for this Plex server"
            except GatewayError as e:
                error = f"Couldn't reach Tautulli with that URL and API key ({e})"
        if error:
            return settings_page(request, 400, tautulli_error=error, tautulli_url=url)
        store.set_tautulli(url, api_key)
        return RedirectResponse("/settings", 303)

    @app.post("/settings/tautulli/remove")
    def remove_tautulli():
        store.set_tautulli(None, None)  # Skips already synced stay, unused until Tautulli is connected again
        return RedirectResponse("/settings", 303)

    @app.get("/settings/reset")
    def confirm_reset(request: Request):
        return templates.TemplateResponse(request, "reset.html", {"page": "settings"})

    @app.post("/settings/reset")
    def reset(request: Request):
        def wipe():
            store.reset()  # Playlists on Plex stay: re-adding a Listener on the same server takes them over by marker
            scheduler.mark_slot_done()  # like a fresh install, there's no missed run to catch up

        if not scheduler.when_idle(wipe):
            raise HTTPException(409, "A Refresh is running. Reset once it has finished.")
        request.session.clear()
        return RedirectResponse("/signin", 303)

    return app
