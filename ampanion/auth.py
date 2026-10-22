"""Owner sign-in: Plex's PIN forwarding flow, the first-run Owner claim, the session cookie and CSRF.

The session is a signed (not encrypted) cookie holding only the Owner's account id and login time, never a token.
The CSRF token is derived from the session, so it needs no storage and changes with every sign-in.
"""

import hashlib
import hmac
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from ampanion.clock import Clock
from ampanion.gateway import Gateway, GatewayError
from ampanion.schedule import Scheduler
from ampanion.store import Store

SESSION_MAX_AGE = 14 * 24 * 3600
PUBLIC_PATHS = {"/health", "/signin", "/signin/plex", "/signin/callback"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
NO_SERVER = "This Plex account doesn't own a Plex Media Server"
NOT_SERVER_OWNER = "This Plex account doesn't own ampanion's Plex server"


def csrf_token(secret: str, session: dict) -> str | None:
    if "owner_account_id" not in session:
        return None
    message = f"{session['owner_account_id']}:{session['login_at']}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def session_guard(store: Store):
    """A dependency for every route: a session of the stored Owner, and a CSRF token on state-changing requests."""

    async def guard(request: Request) -> None:
        if request.url.path in PUBLIC_PATHS:
            return
        owner = request.session.get("owner_account_id")
        if owner is None or owner != store.owner_account_id:
            raise HTTPException(303, headers={"Location": "/signin"})
        if request.method not in SAFE_METHODS:
            sent = request.headers.get("X-CSRF-Token") or (await request.form()).get("csrf_token")
            expected = csrf_token(store.session_secret, request.session)
            if not isinstance(sent, str) or not hmac.compare_digest(sent, expected):
                raise HTTPException(403, "Missing or invalid CSRF token")

    return guard


def add_sign_in_routes(
    app: FastAPI, *, store: Store, gateway: Gateway, clock: Clock, templates: Jinja2Templates, scheduler: Scheduler
):
    def page(request: Request, error: str | None = None, status_code: int = 200):
        return templates.TemplateResponse(request, "signin.html", {"page": "signin", "error": error}, status_code)

    @app.get("/signin")
    def signin(request: Request):
        return page(request)

    @app.get("/signin/plex")
    def signin_plex(request: Request):
        try:
            pin = gateway.create_pin()
        except GatewayError:
            return page(request, "Couldn't reach Plex. Try again.", 502)
        request.session["pin_id"] = pin.id  # server-side only: the callback never trusts a PIN from the URL
        query = urlencode(
            {
                "clientID": store.client_identifier,
                "code": pin.code,
                "forwardUrl": str(request.url_for("signin_callback")),
                "context[device][product]": "ampanion",
            }
        )
        return RedirectResponse(f"https://app.plex.tv/auth#?{query}", 303)

    @app.get("/signin/callback")
    def signin_callback(request: Request):
        pin_id = request.session.pop("pin_id", None)
        try:
            token = gateway.pin_token(pin_id) if pin_id is not None else None
            if not token:
                return page(request, "Plex sign-in wasn't completed. Try again.", 403)
            account = gateway.check_token(token)
            if store.owner_account_id is None:
                if not any(r.owned and r.is_server for r in gateway.resources(token)):
                    return page(request, NO_SERVER, 403)
            elif account.id != store.owner_account_id:
                return page(request, "Only ampanion's Owner can sign in.", 403)
            elif store.server_machine_id is not None:
                chosen = store.server_machine_id
                if not any(r.owned and r.is_server and r.client_identifier == chosen for r in gateway.resources(token)):
                    return page(request, NOT_SERVER_OWNER, 403)
        except GatewayError:
            return page(request, "Couldn't reach Plex. Try again.", 502)

        resume = store.owner_token_expired
        store.set_owner(account.id, account.username, token)
        if resume:
            scheduler.catch_up()
        request.session.clear()
        request.session.update(owner_account_id=account.id, login_at=clock.now().isoformat())
        return RedirectResponse("/", 303)

    @app.post("/signout")
    def signout(request: Request):
        request.session.clear()
        return RedirectResponse("/signin", 303, headers={"HX-Redirect": "/signin"})
