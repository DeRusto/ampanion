"""Playlist publisher: put a Mix's computed tracks into the Listener's own Plex Playlist, replacing it in place."""

from dataclasses import replace
from datetime import datetime

from ampanion.gateway import Gateway
from ampanion.mixes import Mix
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


def summary_marker(title: str) -> str:
    # shortcut: always says "daily", the scheduling ticket (#34) names the Mix's cadence
    return f"Made by ampanion: {title}. Refreshed daily; edits are replaced."


def publish(
    gateway: Gateway, store: Store, *, url: str, machine_id: str, token: str, account_id: int,
    mix: Mix, genre: str | None, tracks: list[str], length: int, now: datetime,
) -> str:
    """Publish one Playlist with the Listener's token and return its status line.

    Under MIN_TRACKS nothing is created and an existing Playlist is left untouched.
    """
    status = status_line(len(tracks), length)
    managed = next((p for p in store.managed_playlists(account_id, mix.key) if p.genre == genre), None)
    if len(tracks) < MIN_TRACKS:
        kept = replace(managed, status=status) if managed else ManagedPlaylist(mix.key, genre, None, [], status, now)
        store.save_managed_playlist(account_id, kept)
        return status
    title = playlist_title(mix, genre)
    playlist_id = managed.playlist_id if managed else None
    if playlist_id and gateway.playlist(url, token, playlist_id) is not None:
        gateway.clear_playlist(url, token, playlist_id)
        rest = tracks
    else:
        # shortcut: no takeover by marker or "recreated" note yet, the Playlist lifecycle ticket (#29) adds them
        playlist_id = gateway.create_playlist(url, token, machine_id, title, tracks[:CHUNK])
        # Remember the id at once, so a failure below doesn't lead to a duplicate Playlist next time.
        store.save_managed_playlist(account_id, ManagedPlaylist(mix.key, genre, playlist_id, [], status, now))
        rest = tracks[CHUNK:]
    for i in range(0, len(rest), CHUNK):
        gateway.add_to_playlist(url, token, machine_id, playlist_id, rest[i : i + CHUNK])
    gateway.set_playlist_summary(url, token, playlist_id, summary_marker(title))
    store.save_managed_playlist(account_id, ManagedPlaylist(mix.key, genre, playlist_id, tracks, status, now))
    return status
