# Ampanion

A self-hosted companion to a Plex server that builds personal playlists for Plexamp from each listener's own music library and listening history.

## Language

**Mix**:
A named rule for choosing tracks, such as On Repeat, Deep Cuts, Genre Mix, Daily Mixtape or New Artists.
_Avoid_: Playlist type, recipe, generator, smart playlist

**Playlist**:
The Plex playlist a Mix produces for one Listener, replaced each time it is refreshed.
_Avoid_: Mix (for the output), queue

**Listening history**:
The record of a Listener's Plays, and their Skips where known, whichever source they come from.
_Avoid_: Play history, scrobbles, stats

**Play**:
One time a Listener listened to a track to the end, on any client.
_Avoid_: Scrobble, view, listen

**Skip**:
One time a Listener started a track but stopped early; known only when a source can see partial listens.
_Avoid_: Partial play

**Rating**:
A Listener's current star rating of a track, album or artist; no Rating means no opinion.
_Avoid_: Like, score, favourite

**Listener**:
A Plex user on the server who gets their own Playlists from their own Listening history.
_Avoid_: Account, member, user (alone)

**On Repeat**:
The Mix of the tracks a Listener has played most often lately.
_Avoid_: Top tracks, most played

**Top Artist**:
An artist a Listener has played most across their Listening history.
_Avoid_: Favourite artist, loved artist

**Deep Cuts**:
The Mix of tracks a Listener rarely or never plays by their Top Artists.
_Avoid_: B-sides, rarities, hidden gems

**Top Genre**:
A genre a Listener has played most across their Listening history.
_Avoid_: Favourite genre

**Genre Mix**:
The Mix that gives a Listener one Playlist for each of their Top Genres.
_Avoid_: Genre radio, genre station

**Daily Mixtape**:
The Mix that produces a fresh, varied Playlist every day.
_Avoid_: Daily mix, mix tape
