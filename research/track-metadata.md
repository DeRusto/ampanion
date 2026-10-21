# What track metadata can Mixes use?

Research for issue #6. Answers which Plex music metadata a Mix rule could read, at which level (artist / album / track), whether it is per Listener, how reliable it is, and what it costs to fetch for a large library.

Terms follow `GLOSSARY.md` (Mix, Playlist, Listener, Listening history).

## Sources

- **[plexapi-audio]** python-plexapi `plexapi/audio.py`, master, version 4.18.3: https://github.com/pkkid/python-plexapi/blob/master/plexapi/audio.py
- **[plexapi-library]** python-plexapi `plexapi/library.py` (`LibrarySection.search`, `MusicSection`): https://github.com/pkkid/python-plexapi/blob/master/plexapi/library.py
- **[plexapi-base]** python-plexapi `plexapi/base.py` (`fetchItems`, `PlexPartialObject.__getattribute__`, `_EXCLUDES`): https://github.com/pkkid/python-plexapi/blob/master/plexapi/base.py
- **[plexapi-init]** python-plexapi `plexapi/__init__.py` (`X_PLEX_CONTAINER_SIZE`): https://github.com/pkkid/python-plexapi/blob/master/plexapi/__init__.py
- **[pms-api]** Plex Media Server API reference (OpenAPI, spec version 1.2.3, "supported in PMS >= 1.43.4"): https://developer.plex.tv/pms/
- **[plex-sonic]** Plex support, "Sonic Analysis for Music": https://support.plex.tv/articles/sonic-analysis-music/ (the page returned HTTP 403 to automated fetches; its content was read through search-engine excerpts, so re-check the live page)
- **[forums]** Plex forum threads, used only for agent-behaviour reports that Plex does not document. They are secondary sources and are marked as such:
  - Embedded genre / style tags: https://forums.plex.tv/t/music-styles-tag/564769 and https://forums.plex.tv/t/plex-does-not-display-embedded-genre-metadata/187626
  - Library "Genres" source setting: https://forums.plex.tv/t/music-genre-source-on-plexamp/660428 and https://forums.plex.tv/t/new-music-metadata-handling/469988
  - Popular tracks and Last.fm: https://forums.plex.tv/t/popular-tracks/884638

## Field inventory

"Level" means which Plex object carries the field in python-plexapi's model. A track rule can still filter on artist or album fields through a `libtype` prefix (see "Fetching cost").

| Field | Level | Per Listener? | python-plexapi attribute | Server-side filter / sort | Where the data comes from |
|---|---|---|---|---|---|
| Genre | artist, album, track | no (library-wide) | `.genres` | `genre`, `artist.genre`, `album.genre` | Plex Music agent or embedded tags, chosen per library |
| Style | artist, album only | no | `.styles` | `style`, `artist.style` | Plex Music agent only |
| Mood | artist, album, track (on base `Audio`) | no | `.moods` | `mood` | Plex Music agent only |
| User rating (0-10 = 0-5 stars) | artist, album, track | **yes** | `.userRating`, `.lastRatedAt` | `userRating`, `track.userRating` | set by the Listener in Plex/Plexamp |
| Play count, last played | artist, album, track | **yes** | `.viewCount`, `.lastViewedAt` | `viewCount`, `lastViewedAt` | Plex play tracking |
| Skip count | track | yes (by the same logic; not separately documented) | `.skipCount` | not checked | Plex play tracking |
| Date added | artist, album, track | no | `.addedAt` | `addedAt` (absolute or relative, e.g. `30d`) | file scan time |
| Release date | album | no | `.originallyAvailableAt` (day precision) | not checked | agent or tags |
| Year | album, track | no | `.year` | `year`, `decade` | agent or tags |
| Popularity | track | no | `.ratingCount` | sort `ratingCount:desc`, filter `ratingCount>>` | Last.fm scrobble count via Plex metadata |
| Album subformat (Compilation, Live, ...) | album | no | `.subformats` | `album.subformat` | Plex Music agent |
| Similar artists | artist | no | `.similar` | n/a | Plex Music agent |
| Sonic analysis | artist, album, track | no | `.musicAnalysisVersion`, `.hasSonicAnalysis`, `.sonicallySimilar()`, `Track.sonicAdventure()` | `/nearest`, `/computePath` endpoints | Plex sonic analysis (Plex Pass) |

Evidence for each row:

- **Genres, styles, moods.** `Artist` and `Album` each define `genres` and `styles`. `Track` defines `genres` but not `styles`. `moods` is defined on the shared `Audio` base class, so all three levels have it. [plexapi-audio]
- **Ratings and play counts.** `userRating` is a float, "0.0 - 10.0 equaling 0 stars - 5 stars". `Audio` also carries `lastRatedAt`, `viewCount` and `lastViewedAt`; `Track` adds `skipCount`. [plexapi-audio]
- **Ratings are per account.** The PMS spec describes `userRating` as "When the user has rated an item", and `viewCount` / `lastViewedAt` likewise as per user. The server answers for whichever account's token made the request. [pms-api]
- **Reading another account.** `PlexServer.switchUser(user)` returns a server connection logged in as another user, and only the admin account may call it. [plexapi `server.py`, same repo]
- **Dates.** `addedAt` is epoch seconds for when the item was added to the library. `originallyAvailableAt` is `YYYY-MM-DD` and applies to albums as well. [pms-api] In python-plexapi, `Album` has `originallyAvailableAt` and `year`; `Track` has `year`. [plexapi-audio]
- **Popularity.** `Track.ratingCount` is documented as "Number of listeners who have scrobbled this track, as reported by Last.fm". `Artist.popularTracks()` is just a library search:
  - filters: `ratingCount>>0`, excludes `album.subformat` Compilation and Live, `group=title`
  - sort: `ratingCount:desc`, `limit=100`

  So "popular" is a plain sortable field, not a separate service. [plexapi-audio]
- **Sonic analysis.**
  - `hasSonicAnalysis` is `musicAnalysisVersion == 1`.
  - `sonicallySimilar(limit, maxDistance)` calls `GET /library/metadata/{id}/nearest`. Server defaults are 50 results and maxDistance 0.25. The endpoint also takes `excludeParentID` and `excludeGrandparentID`.
  - `MusicSection.sonicAdventure(start, end)` calls `GET /library/sections/{id}/computePath?startID&endID`, with `count` (default 50) and `maxDistance` (default 0.25).
  - The result items carry a `distance` attribute.
  - The section-level `GET /library/sections/{id}/nearest` returns "the nearest audio tracks to a particular analysis".

  [plexapi-audio] [plexapi-library] [pms-api]

## Reliability with typical libraries

- **Sonic analysis**
  - Needs Plex Pass on the server admin account and PMS 1.24.0 or newer. It runs on Windows, macOS and 64-bit Linux. The support page excerpt says ARM CPUs are not supported, apart from Apple Silicon under Rosetta. [plex-sonic]
  - Analysis runs as a background task and can take days on a large library. Until it finishes, `/nearest` has gaps. Ampanion should therefore check `hasSonicAnalysis` per seed track and treat sonic features as optional.
  - A Pi or other ARM NAS user gets nothing from sonic analysis.
- **Genre**
  - Each library has a "Genres" source setting (Plex Music agent or embedded tags), not both. [forums, secondary]
  - With the Plex Music agent, genres sit on artists and albums. Track-level `genres` is often empty. Forum reports say embedded-tag genre also lands at album level. [forums, secondary] Not verified against a live server.
  - **Implication:** the Genre Mix should match on `artist.genre` / `album.genre` through server-side filters, not only on track genre.
- **Style and mood**
  - These come only from the Plex Music agent. There is no standard file tag for style, and forum reports say embedded mood tags are not read. [forums, secondary]
  - Libraries that are unmatched or set to local-only metadata will have none.
  - Coverage is good for mainstream catalogue and poor for obscure, classical or self-released music.
  - Mixes must cope with empty styles and moods.
- **Popularity (`ratingCount`)**
  - Depends on Plex matching the track and on Last.fm data. Forum users report stale or mismatched values. [forums, secondary]
  - Unmatched tracks have no value. Treat it as a soft signal, e.g. ordering Deep Cuts by low `ratingCount`. Never use it as a hard filter.
- **Date added**
  - Always present.
  - It records scan time, not purchase time. A rescan or migration to a new server resets it, so a whole library can look "new". This matters for New Artists.
- **Release date and year**
  - Album `year` is usually present for matched albums. `originallyAvailableAt` is sometimes only a year or missing.
  - Track `year` is often empty. Use the album value.
- **User rating**
  - Reliable but sparse: most Listeners rate few tracks.
  - It is per account, so ampanion must read it as each Listener. That needs a token per Listener, e.g. the admin calling `switchUser`, or the Listener's own token.
  - Whether `switchUser` works for non-Home shared users is still open and belongs to the auth/Listener ticket.

## Fetching cost for a large library

- **Paging**
  - `LibrarySection.search()` sends `X-Plex-Container-Start` / `X-Plex-Container-Size` and loops in pages. The default page size is `X_PLEX_CONTAINER_SIZE = 100`, configurable through `plexapi.container_size`. [plexapi-base] [plexapi-init]
  - A full scan of 100k tracks is about 1,000 requests at the default size. Passing a larger `container_size` (several thousand) cuts this sharply.
- **Filter on the server**
  - Plex filters run on the server: `filters={...}`, `libtype`-prefixed fields such as `artist.genre`, operators `>>` / `<<` / `!`, `and`/`or` trees, and relative dates such as `addedAt>>30d`.
  - PlexAPI's `__` operators (e.g. `viewCount__gte`) need the server to return *all* rows and filter them in Python. The docstring says the Plex filters "will be faster". [plexapi-library]
  - **Implication:** express Mix rules as server-side filters and sorts where possible.
- **Auto-reload trap (N+1)**
  - Search results are partial objects.
  - Reading an attribute whose value is `None` or `[]` on a partial object makes python-plexapi silently `reload()` that item with one extra HTTP request. Examples: an unrated track's `userRating`, or a track with no `moods`. [plexapi-base]
  - Iterating 100k tracks and reading sparse fields could therefore issue up to 100k extra requests.
  - **Mitigation:** set `plexapi.autoreload = false`, or add the field names to `USER_DONT_RELOAD_FOR_KEYS`. Fetch tag fields through filters, not by reading them per item.
- **Tags in list responses**
  - The full-detail reload uses `_INCLUDES`.
  - `_EXCLUDES` lists `excludeElements=Media,Genre,...,Style,Mood` for lighter requests. [plexapi-base]
  - Not checked: exactly which tag elements a `/all?type=10` list response includes by default. Spike it before relying on reading `track.genres` from a bulk search.
- **Sonic calls**
  - `/nearest` and `/computePath` cost one request per seed and return up to 50 items by default. That is cheap for a Playlist-sized build.
- **Per-Listener fields**
  - A full scan needs one pass per Listener for `userRating` and `viewCount`.
  - Library-wide fields (genre, mood, addedAt, ratingCount) can be scanned once and cached in SQLite, then joined with each Listener's small per-account data.

## Open questions and recommended spike

1. Against a real library, run `section.listFields('track')`, `listFields('album')` and `listFields('artist')` to get the actual filter and sort list for this PMS version. That confirms whether `track.mood` and `ratingCount` can be filtered on.
2. Measure one `searchTracks(container_size=N)` page: which tags come back, and the response size and time.
3. Check whether `switchUser` or a per-Listener token returns that Listener's `userRating` and `viewCount` for a shared (non-Home) user.
