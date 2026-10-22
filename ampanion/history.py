"""History sync: bring a Listener's cached Listening history up to date from Plex.

The first fill reads their whole Plex history; later syncs re-read a trailing window and insert only unseen entries.
Plays deleted in Plex stay. Each Play keeps a track snapshot, and is re-linked by guid when its rating key changes.
"""

from datetime import datetime, timedelta

from ampanion.gateway import Gateway
from ampanion.library import LibraryIndex
from ampanion.store import Play, Store

# Longer than Plexamp's ~60-day retry for Plays made offline, so late arrivals are still picked up.
INCREMENTAL_WINDOW = timedelta(days=90)


def sync(
    store: Store, gateway: Gateway, *, url: str, owner_token: str, account_id: int, history_account_id: int,
    library: LibraryIndex, now: datetime,
) -> None:
    """Raises GatewayError if Plex's history can't be read; nothing is stored then."""
    # No cached Plays means no fill yet (or an empty history, which is as cheap to read again).
    since = now - INCREMENTAL_WINDOW if store.has_plays(account_id) else None
    entries = gateway.history(url, owner_token, history_account_id, since)
    store.add_plays(
        account_id,
        [
            (
                e.history_key,
                Play(
                    e.played_at, e.rating_key, getattr(library.tracks.get(e.rating_key), "guid", None),
                    e.title, e.artist, e.album_artist, e.album,
                ),
            )
            for e in entries
        ],
    )
    stale = {p.guid for p in store.plays(account_id) if p.rating_key not in library.tracks and p.guid in library.by_guid}
    for guid in stale:
        store.relink_plays(account_id, guid, library.by_guid[guid])
