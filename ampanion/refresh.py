"""Refresh runner: one Refresh per Listener. Sync history, read Ratings, then compute and publish every enabled, due Mix.

When Refreshes run, and one run at a time, is the scheduler's job (ampanion/schedule.py).
"""

import logging
from collections.abc import Callable
from datetime import timedelta
from random import Random

from ampanion import history
from ampanion.clock import Clock
from ampanion.gateway import Gateway, GatewayError, TokenExpired
from ampanion.library import LibraryIndex, Ratings
from ampanion.mixes import MIXES, Mix, MixInput, mix_config
from ampanion.publisher import delete_playlists, playlist_title, publish
from ampanion.store import Listener, Store

log = logging.getLogger(__name__)

KEEP_REFRESHES = timedelta(days=90)
OWNER_HISTORY_ACCOUNT = 1  # the Owner's account id in the server's own history, whatever their plex.tv id


class RefreshFailed(Exception):
    """The Listener's Refresh stops before any Playlist is touched; the message goes on the Refresh record."""


class OwnerTokenExpired(Exception):
    """A call with the Owner token got a 401: the whole run stops, and Refreshes pause until the Owner signs in again."""


class _WatchOwnerToken:
    """The Gateway, raising OwnerTokenExpired (not a GatewayError, so nothing on the way catches it) when a call made
    with the Owner token gets a 401. A 401 on a Listener's own token stays an ordinary failure of their Refresh."""

    def __init__(self, gateway: Gateway, owner_token: str):
        self._gateway, self._owner_token = gateway, owner_token

    def __getattr__(self, name):
        call = getattr(self._gateway, name)

        def watched(*args, **kwargs):
            try:
                return call(*args, **kwargs)
            except TokenExpired as e:
                if self._owner_token in (*args, *kwargs.values()):
                    raise OwnerTokenExpired() from e
                raise

        return watched


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


Due = Callable[[int, Mix], bool]  # (account id, Mix) -> rebuild it in this Refresh?


def run(
    store: Store, gateway: Gateway, clock: Clock, rng: Random, listeners: list[Listener],
    due: Due = lambda account_id, mix: True, on_listener: Callable[[int, Listener], None] = lambda i, listener: None,
) -> None:
    """Refresh each Listener in turn, writing a Refresh record for each. One Listener's failure doesn't stop the rest.

    A 401 on the Owner token does: the Listener's record is dropped and OwnerTokenExpired raised.
    """
    store.prune_refreshes(clock.now() - KEEP_REFRESHES)
    with gateway.connections():  # one Plex server connection per token for the whole run
        _run(store, _WatchOwnerToken(gateway, store.owner_token), clock, rng, listeners, due, on_listener)


def _run(store: Store, gateway: Gateway, clock: Clock, rng: Random, listeners: list[Listener], due: Due, on_listener) -> None:
    library = None
    for i, listener in enumerate(listeners):
        on_listener(i, listener)
        record = store.start_refresh(listener.account_id, clock.now())
        mixes, error = [], None
        try:
            if store.server_url is None:
                raise RefreshFailed("ampanion can't reach the Plex server")
            if library is None:  # loaded once per run and shared by every Listener in it
                library = step("Couldn't read the music library from Plex", LibraryIndex.load, gateway, store.server_url, store.owner_token)
            mixes = refresh_listener(store, gateway, clock, rng, listener, library, due)
        except OwnerTokenExpired:
            store.delete_refresh(record)  # the run's one record is "Plex sign-in expired", written by the scheduler
            raise
        except RefreshFailed as e:
            error = str(e)
        except Exception as e:  # never leave a record stuck on "refreshing"
            log.exception("Refresh for %s failed", listener.name)
            error = f"Unexpected error: {e}"
        failed = error is not None or any(m["outcome"] == "failed" for m in mixes)
        store.finish_refresh(record, clock.now(), "failed" if failed else "ok", mixes, error)


def refresh_listener(
    store: Store, gateway: Gateway, clock: Clock, rng: Random, listener: Listener, library: LibraryIndex, due: Due
) -> list[dict]:
    url, account_id = store.server_url, listener.account_id
    token = listener_token(store, gateway, account_id)
    # shortcut: filters by whole music library only, not a share's label (track-level) restrictions; upgrade when a Listener's share uses them
    sections = step("Couldn't read the Listener's music libraries from Plex", gateway.music_sections, url, token)
    library = library.in_sections(set(sections))
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

    outcomes, skips = [], []
    if store.tautulli_url:  # without Tautulli, Mixes behave as if there were no Skips, even ones kept from before
        try:
            history.sync_skips(
                store, gateway, url=store.tautulli_url, api_key=store.tautulli_api_key, account_id=account_id,
                library=library, now=clock.now(),
            )
        except GatewayError as e:  # Mixes still refresh, with the Skips already kept; the Refresh is marked failed
            outcomes.append({"mix": "skips", "outcome": "failed", "detail": f"Couldn't read Skips from Tautulli: {e}"})
        skips = store.skips(account_id)

    keys_by_guid: dict[str, list[str]] = {}  # every copy of a recording, not just the last one by_guid keeps
    for t in library.tracks.values():
        keys_by_guid.setdefault(t.guid or t.rating_key, []).append(t.rating_key)

    for mix in MIXES.values():
        enabled, settings = mix_config(store, account_id, mix)
        if not enabled or not due(account_id, mix):
            continue
        try:
            # Picks are kept by guid, so a rescan that gives a track a new rating key doesn't lift its exclusion.
            managed = store.managed_playlists(account_id, mix.key)
            previous = {p.genre: [k for g in p.picked for k in keys_by_guid.get(g, [g])] for p in managed}
            # Every Playlist of the Mix is computed before any is touched.
            playlists = mix.rule(MixInput(plays, skips, library, ratings, settings, previous, clock.now(), rng))
            for rank, (genre, tracks) in enumerate(playlists.items()):  # a Genre Mix rule returns them best first
                status = publish(
                    gateway, store, url=url, machine_id=store.server_machine_id, token=token, account_id=account_id,
                    mix=mix, genre=genre, tracks=tracks, length=settings["length"], now=clock.now(), rank=rank,
                    picked=[library.tracks[k].guid or k for k in tracks],
                )
                outcomes.append({"mix": mix.key, "outcome": "ok", "detail": f"{playlist_title(mix, genre)}: {status}"})
            if dropped := previous.keys() - playlists.keys():  # e.g. a genre no longer a Top Genre
                delete_playlists(gateway, store, url=url, token=token, account_id=account_id, mix_key=mix.key, genres=dropped)
            store.set_mix_built(account_id, mix.key, clock.now())
        except OwnerTokenExpired:
            raise
        except Exception as e:
            if not isinstance(e, GatewayError):
                log.exception("%s for %s failed", mix.title, listener.name)
            outcomes.append({"mix": mix.key, "outcome": "failed", "detail": f"{mix.title}: failed ({e})"})
    return outcomes
