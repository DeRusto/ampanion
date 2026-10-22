"""Listeners: which Plex users on the server can be one, making one, their page, and turning one off.

A Plex user can be a Listener only if plex.tv gives ampanion a server token for them (a library share on this server).
The Owner always can: they use the Owner token directly.
"""

from dataclasses import dataclass

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from ampanion.clock import Clock
from ampanion.gateway import Gateway, GatewayError, PlexAccount
from ampanion.store import Store

NO_SHARE = "No library share on this server"
TABS = ("mixes", "playlists", "history")


@dataclass(frozen=True)
class SidebarUser:
    account_id: int
    name: str
    state: str  # "listener", "available" or "unavailable"


def plex_users(store: Store, gateway: Gateway) -> list[tuple[PlexAccount, bool]]:
    """Every Plex user on the server, the Owner first, each with whether they can be a Listener."""
    tokens = gateway.shared_tokens(store.owner_token, store.server_machine_id)
    owner = PlexAccount(id=store.owner_account_id, username=store.owner_username)
    return [(owner, True)] + [(user, user.id in tokens) for user in gateway.users(store.owner_token)]


def sidebar_users(store: Store, gateway: Gateway) -> tuple[list[SidebarUser], str | None]:
    """The sidebar's Plex users and an error to show; stored Listeners are listed even when Plex can't be reached."""
    listeners = store.listeners()
    ids = {listener.account_id for listener in listeners}
    try:
        # shortcut: asks plex.tv on every page view, cache the list if pages feel slow
        users = [
            SidebarUser(u.id, u.username, "listener" if u.id in ids else "available" if ok else "unavailable")
            for u, ok in plex_users(store, gateway)
        ]
        error = None
    except GatewayError:
        users, error = [], "Couldn't reach Plex to list its users."
    shown = {u.account_id for u in users}
    users += [SidebarUser(l.account_id, l.name, "listener") for l in listeners if l.account_id not in shown]
    return users, error


def add_listener_routes(app: FastAPI, *, store: Store, gateway: Gateway, clock: Clock, templates: Jinja2Templates):
    def listener_or_404(account_id: int):
        listener = store.listener(account_id)
        if listener is None:
            raise HTTPException(404, "Not a Listener")
        return listener

    @app.post("/listeners")
    def add_listener(account_id: int = Form()):
        if store.server_machine_id is None:
            raise HTTPException(409, "Choose the Plex server first")
        try:
            users = plex_users(store, gateway)
        except GatewayError:
            raise HTTPException(502, "Couldn't reach Plex. Try again.") from None
        user = next((u for u, ok in users if ok and u.id == account_id), None)
        if user is None:
            raise HTTPException(400, NO_SHARE)
        store.add_listener(user.id, user.username, clock.now())
        # shortcut: no Refresh on add yet, the Refresh runner ticket starts one here
        return RedirectResponse(f"/listeners/{user.id}", 303)

    @app.get("/listeners/{account_id}")
    def listener_page(request: Request, account_id: int, tab: str = "mixes"):
        context = {"page": "listener", "listener": listener_or_404(account_id), "tabs": TABS, "tab": tab}
        if tab not in TABS:
            raise HTTPException(404, "No such tab")
        return templates.TemplateResponse(request, "listener.html", context)

    @app.get("/listeners/{account_id}/turn-off")
    def confirm_turn_off(request: Request, account_id: int):
        context = {"page": "listener", "listener": listener_or_404(account_id)}
        return templates.TemplateResponse(request, "turn_off.html", context)

    @app.post("/listeners/{account_id}/turn-off")
    def turn_off(account_id: int):
        listener_or_404(account_id)
        # shortcut: their Playlists aren't deleted from Plex yet, the Playlist lifecycle ticket deletes them here first
        store.remove_listener(account_id)
        return RedirectResponse("/", 303)
