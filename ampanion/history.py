"""History sync: bring a Listener's cached Listening history up to date, Plays from Plex and Skips from Tautulli.

The first fill reads their whole Plex history; later syncs re-read a trailing window and insert only unseen entries.
Plays deleted in Plex stay. Each Play and Skip keeps a track snapshot, and is re-linked by guid when its rating key changes.
"""

from datetime import datetime, timedelta

from ampanion.gateway import Gateway
from ampanion.library import LibraryIndex
from ampanion.store import Play, Skip, Store

# Longer than Plexamp's ~60-day retry for Plays made offline, so late arrivals are still picked up.
INCREMENTAL_WINDOW = timedelta(days=90)
SKIP_BELOW = 50  # a Tautulli row under this percent complete is a Skip


def sync(
    store: Store, gateway: Gateway, *, url: str, owner_token: str, account_id: int, history_account_id: int,
    library: LibraryIndex, now: datetime,
) -> None:
    """Raises GatewayError if Plex's history can't be read; nothing is stored then."""
    since = now - INCREMENTAL_WINDOW if store.first_fill_done(account_id) else None
    entries = gateway.history(url, owner_token, history_account_id, since)
    store.add_plays(
        account_id,
        [
            (
                e.history_key,
                Play(
                    e.played_at, e.rating_key, getattr(library.tracks.get(e.rating_key), "guid", None),
                    e.title, e.artist, e.album_artist, e.album, getattr(library.tracks.get(e.rating_key), "genres", ()),
                ),
            )
            for e in entries
        ],
    )
    kept = [*store.plays(account_id), *store.skips(account_id)]
    stale = {e.guid for e in kept if e.rating_key not in library.tracks and e.guid in library.by_guid}
    for guid in stale:
        store.relink(account_id, guid, library.by_guid[guid])


def sync_skips(
    store: Store, gateway: Gateway, *, url: str, api_key: str, account_id: int, library: LibraryIndex, now: datetime
) -> None:
    """Add the Listener's new Skips from Tautulli, whose user ids are plex.tv account ids (the Owner's too, unlike Plex).

    Reads from the day before the last sync (Tautulli filters by whole days, in its own time zone); rows already kept are
    left. Raises GatewayError if Tautulli can't be read; nothing is stored then.
    """
    synced = store.skips_synced(account_id)
    rows = gateway.tautulli_history(url, api_key, account_id, synced and (synced - timedelta(days=1)).date())
    skips = [
        (r.row_id, Skip(r.started_at, r.rating_key, getattr(library.tracks.get(r.rating_key), "guid", None),
                        r.title, r.artist, r.album_artist, r.album, r.percent_complete))
        for r in rows
        if r.percent_complete < SKIP_BELOW
    ]
    store.add_skips(account_id, skips, now)
