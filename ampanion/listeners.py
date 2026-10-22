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
from ampanion.mixes import MIXES, mix_config
from ampanion.schedule import Scheduler
from ampanion.store import Store

NO_SHARE = "No library share on this server"
TABS = ("mixes", "playlists", "history")


@dataclass(frozen=True)
class SidebarUser:
    account_id: int
    name: str
    state: str  # "listener", "available" or "unavailable"
    status: str | None = None  # a Listener's latest Refresh: "refreshing", "failed" or "ok"


def plex_users(store: Store, gateway: Gateway) -> list[tuple[PlexAccount, bool]]:
    """Every Plex user on the server, the Owner first, each with whether they can be a Listener."""
    tokens = gateway.shared_tokens(store.owner_token, store.server_machine_id)
    owner = PlexAccount(id=store.owner_account_id, username=store.owner_username)
    return [(owner, True)] + [(user, user.id in tokens) for user in gateway.users(store.owner_token)]


def sidebar_users(store: Store, gateway: Gateway) -> tuple[list[SidebarUser], str | None]:
    """The sidebar's Plex users and an error to show; stored Listeners are listed even when Plex can't be reached."""
    listeners = store.listeners()
    ids = {listener.account_id for listener in listeners}
    states = store.refresh_states()
    try:
        # shortcut: asks plex.tv on every page view, cache the list if pages feel slow
        users = [
            SidebarUser(u.id, u.username, "listener" if u.id in ids else "available" if ok else "unavailable", states.get(u.id))
            for u, ok in plex_users(store, gateway)
        ]
        error = None
    except GatewayError:
        users, error = [], "Couldn't reach Plex to list its users."
    shown = {u.account_id for u in users}
    users += [
        SidebarUser(l.account_id, l.name, "listener", states.get(l.account_id)) for l in listeners if l.account_id not in shown
    ]
    return users, error


def add_listener_routes(
    app: FastAPI, *, store: Store, gateway: Gateway, clock: Clock, templates: Jinja2Templates, scheduler: Scheduler
):
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
        scheduler.request([user.id])  # their first Refresh, with the full first fill of their history
        return RedirectResponse(f"/listeners/{user.id}", 303)

    @app.get("/listeners/{account_id}")
    def listener_page(request: Request, account_id: int, tab: str = "mixes"):
        context = {"page": "listener", "listener": listener_or_404(account_id), "tabs": TABS, "tab": tab}
        if tab not in TABS:
            raise HTTPException(404, "No such tab")
        context["mixes"] = [
            (mix, *mix_config(store, account_id, mix), store.managed_playlists(account_id, mix.key))
            for mix in MIXES.values()
        ]
        context["refreshes"] = store.refreshes(account_id)
        return templates.TemplateResponse(request, "listener.html", context)

    @app.post("/listeners/{account_id}/mixes/{mix_key}")
    async def save_mix(request: Request, account_id: int, mix_key: str):
        listener_or_404(account_id)
        if mix_key not in MIXES:
            raise HTTPException(404, "No such Mix")
        mix, form, settings = MIXES[mix_key], await request.form(), {}
        for s in mix.settings:
            try:
                value = int(form.get(s.key, ""))
            except ValueError:
                raise HTTPException(400, f"{s.label} must be a whole number") from None
            if not s.min <= value <= s.max:
                raise HTTPException(400, f"{s.label} must be between {s.min} and {s.max}")
            settings[s.key] = value
        # shortcut: turning a Mix off neither confirms nor deletes its Playlists, the Playlist lifecycle ticket (#29) does
        was_enabled, enabled = mix_config(store, account_id, mix)[0], form.get("enabled") == "on"
        store.set_mix_settings(account_id, mix.key, enabled, settings)
        if enabled and not was_enabled:
            scheduler.request([account_id])
        return RedirectResponse(f"/listeners/{account_id}", 303)

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
