# Can Plex alone supply each Listener's Listening history?

Research for issue #2. Researched 2026-10-21 against the official Plex Media Server API reference (published by Plex at https://developer.plex.tv/pms/, OpenAPI 3.1), python-plexapi `master` source, Tautulli source, and Plex forum threads. Terms follow `GLOSSARY.md` (Listener, Listening history, Mix, Playlist).

## Short answer

**Yes, for completed plays.** With the server owner's token, one endpoint (`GET /status/sessions/history/all`) returns the play events of **every** Listener on the server, filterable by `accountID`. Each event is one track, one Listener, one timestamp, one device. It has no duration, no completion percentage and no skips. Per-Listener counters on tracks (`viewCount`, `lastViewedAt`, `userRating`) also exist. They are only returned for the account whose token makes the request, so ampanion has to request each Listener's server token to read them.

## 1. Who can be read with the owner token

- The official spec for `GET /status/sessions/history/all` says: "List all playback history (Admin can see all users, others can only see their own)." [PMS API, Status > List Playback History]
- The `accountID` query parameter restricts results to one account. Other filters are `viewedAt` (for example `viewedAt>=<epoch>`), `librarySectionID` and `metadataItemID`, plus `sort` (for example `viewedAt:desc,accountID`). The endpoint supports pagination through `X-Plex-Container-Start/Size`. [PMS API]
- python-plexapi wraps it as `PlexServer.history(maxresults, mindate, ratingKey, accountID, librarySectionID)`. That builds `/status/sessions/history/all?sort=viewedAt:desc&accountID=…&librarySectionID=…&viewedAt>=…` and pages through all results. [python-plexapi `plexapi/server.py` `history()`]
- **Account IDs:** the server's local account list is `GET /accounts`, exposed as `PlexServer.systemAccounts()` with `SystemAccount.id` and `name`. [python-plexapi `server.py`] The owner is always `accountID=1` on their own server. python-plexapi hard-codes this (`MyPlexAccount.history()` passes `accountID=1`; the bandwidth filter comment says "The admin account is accountID=1"). A 2022 forum thread confirms that history shows the admin as `1` while other users' IDs match their plex.tv user ids. [python-plexapi `myplex.py`; forums.plex.tv/t/796964]
  - The same model covers Plex Home managed users, Plex Home members with full accounts, and shared (friend) users. All of them are local accounts on the server, and all of their plays are recorded with their own `accountID`. python-plexapi's `MyPlexServerShare.history()` passes the plex.tv user id straight through as `accountID`. [python-plexapi `myplex.py`]
- **Caveat for the design:** ampanion should map a Listener to `accountID` once (owner → 1, everyone else → plex.tv user id / `SystemAccount.id`) and store it. It should call `PlexServer.history(accountID=…)` directly. It should not use `MyPlexUser.history()`, which goes back through plex.tv and `resource().connect()`.

## 2. What each history entry contains

From the spec's response schema [PMS API] and python-plexapi's `PlexHistory` / `TrackHistory` [`base.py`, `audio.py`]:

| Field | Meaning |
|---|---|
| `historyKey` | `/status/sessions/history/<id>`: a unique id for this play event, usable as a dedupe key |
| `ratingKey`, `key` | the track played (`/library/metadata/<ratingKey>`) |
| `librarySectionID` | the library the track is in |
| `title`, `type` (`track`), `thumb`, `originallyAvailableAt` | display metadata. Track entries also carry the usual Track parent/grandparent (album/artist) attributes, which python-plexapi parses into a `TrackHistory` |
| `viewedAt` | epoch seconds when the play was recorded |
| `accountID` | which Listener |
| `deviceID` | which device (`/devices`, `SystemDevice`) |

**Not present:** play duration, percentage completed, skip events, and per-event play counts. A play count is derived by counting entries. `TrackHistory.source()` returns `None` when the track no longer exists on the server [python-plexapi `base.py`], so history rows can outlive the media they point to.

**What counts as a play:** the server decides. Elan (Plexamp's developer) said on the forum in 2019 that "you have to listen to 90% before it's marked as a play" and that "it's a server-determined thing". He prefixed this with "IIRC", and Plex does not document it officially. [forums.plex.tv/t/463186] Clients report progress through `POST /:/timeline` every ~10 s (20 s on cellular), and the server turns that into a view. [PMS API, Timeline] So the history holds roughly the plays a Listener completed, not every play they started.

## 3. Per-Listener counters on tracks (`viewCount`, `lastViewedAt`, …)

- The spec defines `viewCount` as "When a user has completed watched or listened to an item, this attribute contains the number of consumptions". It defines `lastViewedAt` as that user's last consumption time. [PMS API, metadata schema] These are **per-user** values, returned for the account of the token making the request.
- python-plexapi parses `viewCount`, `lastViewedAt`, `userRating`, `lastRatedAt` (Audio) and `skipCount`, `viewOffset` (Track). [`audio.py`] `skipCount` does not appear in the official spec.
- **With only the owner token, library browsing returns the owner's counters.** To read another Listener's counters, ampanion needs that Listener's server token:
  - `PlexServer.switchUser(user)` looks up the user's server `accessToken` from plex.tv `GET /api/servers/{machineId}/shared_servers` (owner token). It then returns a `PlexServer` that acts as that user. [python-plexapi `server.py` `switchUser`, `myplex.py` `MyPlexUser.get_token`] Tautulli gets per-user server tokens the same way. [Tautulli `plexpy/plextv.py` `get_full_users_list`]
  - This only works for users that have a `shared_servers` entry for this server. That covers friends and Home users the libraries were shared with. Treat a missing token as "counters unavailable for this Listener". `get_token` logs and returns `None` on failure.
  - Plex Home users can also be switched to with `MyPlexAccount.switchHomeUser(user, pin)`, which needs the user's PIN if one is set. [`myplex.py`]
- **Counters and history can disagree.** `PUT /:/scrobble` (mark played) "does not create any view history of this item but rather just sets the state as played" [PMS API, Timeline], and unscrobble resets the state. `viewCount` can therefore include manual marks that history lacks. History can also include plays that were later unmarked.

## 4. How far back it goes

- The official spec documents **no retention limit** on view history. Entries persist until deleted. `GET/DELETE /status/sessions/history/{historyId}` are admin-only. [PMS API] python-plexapi exposes deletion as `PlexHistory.delete()`.
- A 2015 forum reply claimed history "disappears as the log fills". That claim predates the current database-backed history, and nothing official supports it. Forum answers since then tell users to set the Dashboard to "All Time" per user. [forums.plex.tv/t/125913, /t/866833] **Unverified:** check depth empirically on the real server, using the oldest `viewedAt` per `accountID`.
- History starts when the Listener first played on **this** server. Plays on other servers and other apps are not included.

## 5. What is missing or unreliable

- **Plexamp offline/downloaded plays:** Plexamp stores plays locally and sends them later. Elan said "tries very hard" to sync and "should retry for up to 60 days after the view" (2021). Users have still reported gaps after multi-day outages, and in 2025 a looped track lost plays across a network drop. [forums.plex.tv/t/680429, /t/903011] Late-arriving plays mean ampanion must not treat "since last sync" as complete. It should re-read a trailing window and dedupe on `historyKey`. Whether a late play keeps its original `viewedAt` is **unverified**.
- **Cleared history:** the admin can delete individual entries through the API, Plex Web or Plex Dash. Deleted entries are gone, and Plex keeps no tombstone. Forum reports say direct database edits leave dashboard totals inconsistent. [forums.plex.tv/t/862642]
- **Partial plays and skips:** these are not in history (see §2). `skipCount` is only a per-user counter, and its semantics are undocumented.
- **Manual "mark played":** this changes `viewCount` without creating history (see §3).
- **Plays of tracks since deleted or re-matched:** history rows remain, but `ratingKey` may no longer resolve.

## Implications for ampanion (for downstream tickets)

1. **Source of Listening history:** use `/status/sessions/history/all` with the owner token, one call per Listener (`accountID`) or one call for all of them. It is enough for count- and recency-based Mixes (On Repeat, Deep Cuts, New Artists).
2. **Store history locally** in SQLite, keyed on `historyKey`, with an incremental `viewedAt>=` pull plus a trailing re-read window for late Plexamp syncs. Admin deletions are silent, so do not mirror deletions blindly unless a later decision says to.
3. **Per-Listener `viewCount` / `userRating`** need per-Listener tokens from `shared_servers`. They are only needed if a Mix uses ratings or manual played-state. Otherwise derive counts from history.
4. **No completion or skip signal:** if a Mix needs "skipped" or "partial" behaviour, Plex history alone cannot supply it.

## Sources

- Plex Media Server API reference (official, OpenAPI 3.1): https://developer.plex.tv/pms/. Sections used: Status > List Playback History / Get / Delete Single History Item; Timeline > `/:/timeline`, `/:/scrobble`; metadata schema `viewCount`, `lastViewedAt`
- python-plexapi (master, fetched 2026-10-21): https://github.com/pkkid/python-plexapi. Files: `plexapi/server.py` (`history`, `switchUser`, `systemAccounts`, `SystemAccount`), `plexapi/base.py` (`PlexHistory`, `PlexPartialObject.history`), `plexapi/audio.py` (`Audio`, `Track`, `TrackHistory`), `plexapi/myplex.py` (`MyPlexAccount.history`, `switchHomeUser`, `MyPlexUser.get_token`, `MyPlexServerShare.history`)
- Tautulli `plexpy/plextv.py`: https://github.com/Tautulli/Tautulli/blob/master/plexpy/plextv.py
- Forum, accountID 1 for admin: https://forums.plex.tv/t/api-users-play-history/796964
- Forum, play threshold (elan): https://forums.plex.tv/t/plexamp-play-counts-skips-how-does-it-count/463186
- Forum, offline play sync, 60-day retry (elan): https://forums.plex.tv/t/syncing-offline-plays/680429
- Forum, missing plays across a network drop (2025): https://forums.plex.tv/t/plexamp-play-counts-missing/903011
- Forum, 2015 partial-history claim: https://forums.plex.tv/t/api-query-to-view-watch-history/125913
- Forum, clearing a user's history: https://forums.plex.tv/t/clear-watch-history-for-specific-user/862642

### Not verified (needs a live server)

- Actual history depth on the target server
- Whether late-synced Plexamp plays keep their original `viewedAt`
- Whether every managed Home user has a `shared_servers` token on the owner's server
- The exact play threshold for music
