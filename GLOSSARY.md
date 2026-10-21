# Ampanion

A self-hosted companion to a Plex server that builds personal playlists for Plexamp from each listener's own music library and listening history.

## Language

**Mix**:
A named rule for choosing tracks, such as On Repeat, Deep Cuts, Genre, Daily Mixtape or New Artists.
_Avoid_: Playlist type, recipe, generator, smart playlist

**Playlist**:
The Plex playlist a Mix produces for one Listener, replaced each time it is refreshed.
_Avoid_: Mix (for the output), queue

**Listening history**:
The record of which tracks a Listener has played and when, whichever source it comes from.
_Avoid_: Play history, scrobbles, stats

**Listener**:
A Plex user on the server who gets their own Playlists from their own Listening history.
_Avoid_: Account, member, user (alone)

**Daily Mixtape**:
The Mix that produces a fresh, varied Playlist every day.
_Avoid_: Daily mix, mix tape
