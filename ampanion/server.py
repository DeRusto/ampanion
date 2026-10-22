"""Choosing the Plex server: only servers the Owner owns, reached through plex.tv's connections or a manual URL.

The choice is made once. v1 has no way to switch server in place (a later Reset starts over).
"""

from urllib.parse import urlsplit, urlunsplit

from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import RedirectResponse

from ampanion.gateway import Gateway, GatewayError, PlexResource
from ampanion.store import Store


def owned_servers(gateway: Gateway, token: str) -> list[PlexResource]:
    return [r for r in gateway.resources(token) if r.owned and r.is_server]


def reach(gateway: Gateway, server: PlexResource, token: str) -> str | None:
    """The first of the server's connections that answers as this server: local ones, then local https ones over
    plain http, then remote, never relay. Each group keeps plex.tv's order."""
    local = [c.uri for c in server.connections if c.local and not c.relay]
    remote = [c.uri for c in server.connections if not c.local and not c.relay]
    # Plex can serve its plex.direct certificate without the intermediate, which requests can't verify; on the LAN
    # the server still answers plain http unless its Secure connections setting is Required.
    insecure = [urlunsplit(urlsplit(uri)._replace(scheme="http")) for uri in local if urlsplit(uri).scheme == "https"]
    for uri in local + insecure + remote:
        try:
            if gateway.server_identity(uri, token) == server.client_identifier:
                return uri
        except GatewayError:
            continue
    return None


def choose(store: Store, gateway: Gateway, server: PlexResource) -> None:
    store.set_server(server.client_identifier, server.name, reach(gateway, server, store.owner_token))


def add_server_routes(app: FastAPI, *, store: Store, gateway: Gateway):
    @app.post("/server")
    def choose_server(machine_id: str = Form()):
        if store.server_machine_id is not None:
            raise HTTPException(409, "The Plex server is already chosen")
        try:
            servers = owned_servers(gateway, store.owner_token)
        except GatewayError:
            raise HTTPException(502, "Couldn't reach Plex. Try again.") from None
        server = next((s for s in servers if s.client_identifier == machine_id), None)
        if server is None:
            raise HTTPException(400, "Not a Plex server you own")
        choose(store, gateway, server)
        return RedirectResponse("/", 303)

    @app.post("/settings/server-url")
    def set_server_url(url: str = Form("")):
        url = url.strip()
        if url and (urlsplit(url).scheme not in ("http", "https") or not urlsplit(url).netloc):
            raise HTTPException(400, "The Plex server URL must start with http:// or https://")
        if url:  # another server there would get this one's Playlist writes
            try:
                answers = gateway.server_identity(url, store.owner_token) == store.server_machine_id
            except GatewayError:
                answers = False
            if not answers:
                raise HTTPException(400, f"{store.server_name or 'The chosen Plex server'} doesn't answer at that URL")
        store.set_server_manual_url(url or None)
        return RedirectResponse("/settings", 303)
