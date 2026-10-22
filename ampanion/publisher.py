"""Playlist publisher: put a Mix's computed tracks into the Listener's own Plex Playlist, replacing it in place."""

from dataclasses import replace
from datetime import datetime

from ampanion.gateway import Gateway, NotFound
from ampanion.mixes import Mix, mix_cadence
from ampanion.store import ManagedPlaylist, Store

MIN_TRACKS = 5
NOT_ENOUGH = "Not enough history yet"
CHUNK = 200  # rating keys per request: they travel in the URL, so long Playlists go up in pieces


def status_line(count: int, length: int) -> str:
    if count < MIN_TRACKS:
        return NOT_ENOUGH
    return f"{count} tracks" if count >= length else f"{count} of {length} tracks (not enough history)"


def playlist_title(mix: Mix, genre: str | None) -> str:
    return mix.title if genre is None else f"{genre} Mix"


def marker_name(title: str) -> str:
    """The part of the summary marker naming the Mix (and genre): what takeover matches, whatever the cadence."""
    return f"Made by ampanion: {title}."


def summary_marker(title: str, cadence: str) -> str:
    return f"{marker_name(title)} Refreshed {cadence}; edits are replaced."


def publish(
    gateway: Gateway, store: Store, *, url: str, machine_id: str, token: str, account_id: int,
    mix: Mix, genre: str | None, tracks: list[str], length: int, now: datetime,
    rank: int = 0, picked: list[str],
) -> str:
    """Publish one Playlist with the Listener's token and return its status line. `picked` is kept as the pick.

    Under MIN_TRACKS nothing is created and an existing Playlist is left untouched.
    """
    status = status_line(len(tracks), length)
    managed = next((p for p in store.managed_playlists(account_id, mix.key) if p.genre == genre), None)
    if len(tracks) < MIN_TRACKS:
        # The Playlist stays, but the next Refresh excludes only this pick: an exclusion lasts one Refresh.
        kept = replace(managed, status=status, picked=picked, rank=rank) if managed else ManagedPlaylist(mix.key, genre, None, [], status, now, picked, rank)
        store.save_managed_playlist(account_id, kept)
        return status
    title = playlist_title(mix, genre)
    marker, name = summary_marker(title, mix_cadence(store, mix)), marker_name(title)
    stored_id = managed.playlist_id if managed else None
    if stored_id is None:  # e.g. /config was lost: take over the Listener's Playlist still carrying this marker
        playlist_id = next((p.id for p in gateway.playlists(url, token) if p.summary.startswith(name + " ")), None)
    else:
        playlist_id = stored_id if gateway.playlist(url, token, stored_id) is not None else None
    if playlist_id:
        gateway.clear_playlist(url, token, playlist_id)
        rest = tracks
    else:
        if stored_id:
            status += ", recreated (was deleted in Plex)"
        playlist_id = gateway.create_playlist(url, token, machine_id, title, tracks[:CHUNK])
        # Remember the id at once, so a failure below doesn't lead to a duplicate Playlist next time.
        store.save_managed_playlist(account_id, ManagedPlaylist(mix.key, genre, playlist_id, [], status, now, [], rank))
        rest = tracks[CHUNK:]
    for i in range(0, len(rest), CHUNK):
        gateway.add_to_playlist(url, token, machine_id, playlist_id, rest[i : i + CHUNK])
    gateway.set_playlist_summary(url, token, playlist_id, marker)
    store.save_managed_playlist(account_id, ManagedPlaylist(mix.key, genre, playlist_id, tracks, status, now, picked, rank))
    return status


def on_plex(store: Store, account_id: int, mix_key: str) -> list[ManagedPlaylist]:
    """The Mix's Playlists ampanion has published for the Listener."""
    return [p for p in store.managed_playlists(account_id, mix_key) if p.playlist_id]


def delete_playlists(
    gateway: Gateway, store: Store, *, url: str, token: str, account_id: int, mix_key: str, genres: set | None = None
) -> None:
    """Delete the Mix's Playlists (only those of `genres`, if given) from the Listener's Plex account, then forget them."""
    for p in store.managed_playlists(account_id, mix_key):
        if genres is not None and p.genre not in genres:
            continue
        if p.playlist_id:
            try:
                gateway.delete_playlist(url, token, p.playlist_id)
            except NotFound:
                pass  # the Listener already deleted it
        store.forget_managed_playlist(account_id, mix_key, p.genre)
