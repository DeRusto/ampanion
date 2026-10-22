"""Refresh runner: one Refresh per Listener. Sync history, read Ratings, then compute and publish every enabled Mix.

shortcut: runs synchronously in the request, with no run lock or schedule; the scheduling ticket (#34) adds both.
"""

import logging
from random import Random

from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse

from ampanion import history
from ampanion.clock import Clock
from ampanion.gateway import Gateway, GatewayError
from ampanion.library import LibraryIndex, Ratings
from ampanion.mixes import MIXES, MixInput, mix_config
from ampanion.publisher import playlist_title, publish
from ampanion.store import Listener, Store

log = logging.getLogger(__name__)

OWNER_HISTORY_ACCOUNT = 1  # the Owner's account id in the server's own history, whatever their plex.tv id


class RefreshFailed(Exception):
    """The Listener's Refresh stops before any Playlist is touched; the message goes on the Refresh record."""


def step(error: str, call, *args):
    try:
        return call(*args)
    except GatewayError as e:
        raise RefreshFailed(f"{error}: {e}") from e


def listener_token(store: Store, gateway: Gateway, account_id: int) -> str:
    """The token that owns the Listener's Playlists: the Owner token for the Owner, else their server share token."""
    if account_id == store.owner_account_id:
        return store.owner_token
    tokens = step("Couldn't get the Listener's Plex token", gateway.shared_tokens, store.owner_token, store.server_machine_id)
    if account_id not in tokens:
        raise RefreshFailed("No library share on this server")
    return tokens[account_id]


def run(store: Store, gateway: Gateway, clock: Clock, rng: Random, listeners: list[Listener]) -> None:
    """Refresh each Listener in turn, writing a Refresh record for each. One Listener's failure doesn't stop the rest."""
    library = None
    for listener in listeners:
        record = store.start_refresh(listener.account_id, clock.now())
        mixes, error = [], None
        try:
            if store.server_url is None:
                raise RefreshFailed("ampanion can't reach the Plex server")
            if library is None:  # loaded once per run and shared by every Listener in it
                library = step("Couldn't read the music library from Plex", LibraryIndex.load, gateway, store.server_url, store.owner_token)
            mixes = refresh_listener(store, gateway, clock, rng, listener, library)
        except RefreshFailed as e:
            error = str(e)
        except Exception as e:  # never leave a record stuck on "refreshing"
            log.exception("Refresh for %s failed", listener.name)
            error = f"Unexpected error: {e}"
        failed = error is not None or any(m["outcome"] == "failed" for m in mixes)
        store.finish_refresh(record, clock.now(), "failed" if failed else "ok", mixes, error)


def refresh_listener(
    store: Store, gateway: Gateway, clock: Clock, rng: Random, listener: Listener, library: LibraryIndex
) -> list[dict]:
    url, account_id = store.server_url, listener.account_id
    token = listener_token(store, gateway, account_id)
    history_account = OWNER_HISTORY_ACCOUNT if account_id == store.owner_account_id else account_id
    try:
        history.sync(
            store, gateway, url=url, owner_token=store.owner_token, account_id=account_id,
            history_account_id=history_account, library=library, now=clock.now(),
        )
    except GatewayError as e:
        raise RefreshFailed(f"Couldn't read Listening history from Plex: {e}") from e
    ratings = Ratings(step("Couldn't read Ratings from Plex", gateway.ratings, url, token))
    plays = store.plays(account_id)

    outcomes = []
    for mix in MIXES.values():
        enabled, settings = mix_config(store, account_id, mix)
        if not enabled:
            continue
        try:
            previous = {p.genre: p.tracks for p in store.managed_playlists(account_id, mix.key)}
            # Every Playlist of the Mix is computed before any is touched.
            playlists = mix.rule(MixInput(plays, [], library, ratings, settings, previous, clock.now(), rng))
            for genre, tracks in playlists.items():
                status = publish(
                    gateway, store, url=url, machine_id=store.server_machine_id, token=token, account_id=account_id,
                    mix=mix, genre=genre, tracks=tracks, length=settings["length"], now=clock.now(),
                )
                outcomes.append({"mix": mix.key, "outcome": "ok", "detail": f"{playlist_title(mix, genre)}: {status}"})
        except Exception as e:
            if not isinstance(e, GatewayError):
                log.exception("%s for %s failed", mix.title, listener.name)
            outcomes.append({"mix": mix.key, "outcome": "failed", "detail": f"{mix.title}: failed ({e})"})
    return outcomes


def add_refresh_routes(app: FastAPI, *, store: Store, gateway: Gateway, clock: Clock, rng: Random):
    @app.post("/listeners/{account_id}/refresh")
    def refresh_now(account_id: int):
        listener = store.listener(account_id)
        if listener is None:
            raise HTTPException(404, "Not a Listener")
        run(store, gateway, clock, rng, [listener])
        return RedirectResponse(f"/listeners/{account_id}", 303)
