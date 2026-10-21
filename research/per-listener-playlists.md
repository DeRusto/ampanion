# Creating and replacing Playlists for each Listener

Issue: #3. Researched 2026-10-21 against python-plexapi `master` (4.18.3) source and the official Plex Media Server API reference (https://developer.plex.tv/pms/).

## Answer in short

- A Plex playlist belongs to whoever's **token** created it. To give a Listener a Playlist, ampanion calls the server as that Listener, using that Listener's **server access token**.
- With only the **owner's** token, ampanion can fetch an access token for every user the server is shared with, both managed Home users and friends. python-plexapi does this with `PlexServer.switchUser(user)`. So there is no friend-user gap on this server.
- To refresh a Playlist, ampanion clears it in place and adds the new tracks. The playlist keeps its `ratingKey` (its id). There is no need to delete it and create it again.
- Use **regular** ("dumb") playlists. Smart playlists only take filter rules, so they can't hold a track list that ampanion picks from Listening history.

## Who owns a Playlist, and which token to use

| Listener kind | Token ampanion uses |
|---|---|
| Owner (admin) | The owner's own token: `PlexServer(baseurl, ownerToken)` |
| Managed Home user | `plex.switchUser(user)`, which returns the server access token from the share list |
| Shared (friend) user | The same: `plex.switchUser(user)` |

How it works (source: `plexapi/server.py` `switchUser`, `plexapi/myplex.py` `MyPlexUser.get_token`):

- `PlexServer.switchUser(user)` looks the user up with `self.myPlexAccount().user(user)`. It then calls `user.get_token(self.machineIdentifier)` and returns a new `PlexServer` built with that token. Its docstring says: "Only the admin account can switch to other users."
- `MyPlexUser.get_token` queries `https://plex.tv/api/servers/{machineId}/shared_servers` as the owner (`MyPlexAccount.FRIENDINVITE`). It returns the `accessToken` of the entry whose `userID` matches the user.
  - This is a token for **this server only**, not the user's plex.tv account token. ampanion never needs a Listener's password or PIN.
  - `get_token` swallows exceptions and returns `None` when the user has no share on this server. ampanion must treat `None` as "this user is not a Listener here".
- `MyPlexAccount.user()` and `users()` list friends and Home users from `https://plex.tv/api/users/`. **The owner is not in that list**, so ampanion uses the owner's token directly for the owner's own Playlists.
- python-plexapi itself relies on this: `Playlist.copyToUser(user)` is literally `self.create(server=self._server.switchUser(user), title=..., items=...)` (`plexapi/playlist.py`).
- `MyPlexAccount.switchHomeUser(user, pin)` switches the **plex.tv account** to another Home user (a PIN may be needed). ampanion does not need it. The shared_servers token is simpler and works for friends too.

What this means for ampanion:

- ampanion needs exactly one secret: the owner's Plex token (from a plex.tv sign-in).
- Per-Listener tokens are derived at runtime. They can be cached, but they can change, for example when a share is removed and re-added. On a 401, get the token again.

## Creating and replacing a Playlist in place

API (official PMS reference, "Library: Playlists"):

| Operation | Endpoint | Notes |
|---|---|---|
| Create | `POST /playlists?uri=...&type=audio&title=...&smart=0` | `uri` = `server://{machineId}/com.plexapp.plugins.library/library/metadata/{k1,k2,...}` (comma-joined ratingKeys; see `Playlist._create`) |
| Add items | `PUT /playlists/{id}/items?uri=...` | "With a dumb playlist, this adds the specified items" (`Playlist.addItems`) |
| **Clear** | `DELETE /playlists/{id}/items` | "Clears a playlist, only works with dumb playlists. Returns the playlist." |
| Remove one item | `DELETE /playlists/{id}/items/{playlistItemID}` | `Playlist.removeItems`, one request per item |
| Move item | `PUT /playlists/{id}/items/{playlistItemId}/move?after=` | dumb only |
| Edit title/summary | `PUT /playlists/{id}?title=...` | `editTitle` / `editSummary` |
| Delete | `DELETE /playlists/{id}` | 404 "Playlist not found (or user may not have permission to access playlist)" |

- **Replace in place:**
  1. `DELETE /playlists/{id}/items`
  2. `PUT /playlists/{id}/items?uri=...` with the new tracks, in order.
- The playlist's `ratingKey` stays the same, so anything that points at it should survive, such as a Plexamp pin or a download. `addedAt` is unchanged and `updatedAt` changes.
- python-plexapi (4.18.3) has **no `clear()` method**. Call it raw, e.g. `pl._server.query(pl.key, method=pl._server._session.delete)`, where `pl.key` is `/playlists/{id}/items`. The alternative is `removeItems(pl.items())`, which sends one request per track.
- Run every call with **the Listener's own** `PlexServer` (from `switchUser`). The 404 wording shows that playlists are checked against the calling user. The owner's token does not reach a friend's playlists.
- Creating needs at least one item: `Playlist._create` raises `BadRequest` without items. The raw API allows a blank playlist.
- Find a Listener's existing Playlist by its stored `ratingKey`, not by title. Use `userServer.fetchItem(ratingKey)`, or `userServer.playlists(playlistType='audio')` on the Listener's server. If it is gone (the Listener deleted it), create it again and store the new key.

## Regular vs smart playlists

- **Smart** playlists are a `library://` search URI with filters, sort and `limit` (`Playlist._createSmart`, `updateFilters`). Their contents are worked out by the server, per user.
  - `addItems`, `removeItems`, `moveItem` and clear all fail on smart playlists.
  - A Mix choosing tracks from Listening history can't be written as Plex filters in general (for example, Deep Cuts or New Artists).
- **Regular** playlists are a fixed, ordered list of tracks. This is what ampanion needs.
- Media types can't be mixed: `listType` must match, so a music Playlist holds only audio.

## Size limits and other open risks

- **No playlist size limit is documented** in the PMS reference.
- **URL length is the real limit.** Item ratingKeys go in the query string (`uri=` with comma-joined keys).
  - Other Plex-like servers return HTTP 414 when a long list of ids is put in a URI (e.g. jellyfin/jellyfin-web#2750). No Plex-specific figure was found.
  - Suggestion (not verified): add tracks in chunks of a few hundred keys per `PUT`, in order, since `addItems` appends.
- **Clearing and refilling is not atomic.** A Listener whose Plexamp syncs in between may see an empty or partial Playlist. This is a short window; acceptable, or refresh at night.
- **Plexamp pins:** that a pin keys off the playlist `ratingKey` is inferred, not confirmed by a Plex source. Verify by hand when ampanion is first prototyped: pin a Playlist, clear and refill it, and check the pin.
- **Friend users must have the music library shared** with them. Otherwise the tracks are not visible with their token and adding them should fail. This is not tested.
- **plex.tv dependency:** `switchUser` needs plex.tv to be reachable to read shared_servers. An offline server can only serve the owner.

## Sources

- python-plexapi `plexapi/server.py`: `PlexServer.switchUser`, `createPlaylist`, `playlists`. https://github.com/pkkid/python-plexapi/blob/master/plexapi/server.py
- python-plexapi `plexapi/myplex.py`: `MyPlexUser.get_token`, `FRIENDINVITE`, `MyPlexAccount.user`, `users`, `switchHomeUser`. https://github.com/pkkid/python-plexapi/blob/master/plexapi/myplex.py
- python-plexapi `plexapi/playlist.py`: `_create`, `_createSmart`, `addItems`, `removeItems`, `moveItem`, `updateFilters`, `delete`, `copyToUser`. https://github.com/pkkid/python-plexapi/blob/master/plexapi/playlist.py
- Plex Media Server API reference, "Library: Playlists" (Create a Playlist, Adding to a Playlist, Clearing a playlist, Delete a Playlist, Moving items in a playlist). https://developer.plex.tv/pms/
- jellyfin/jellyfin-web#2750 (414 on long id lists in a URI; an analogue only, not Plex). https://github.com/jellyfin/jellyfin-web/issues/2750
