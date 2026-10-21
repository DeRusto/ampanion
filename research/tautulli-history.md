# Tautulli as an optional Listening history source

Issue: #4. Researched 2026-10-21 against Tautulli `master` (latest release v2.18.2, 2026-09-28) and python-plexapi `master`.

## Short answer

Tautulli adds what Plex's own history lacks: **per-play duration and how far into the track the Listener got**. Plex's history (`/status/sessions/history/all`) records only that an item was viewed (`ratingKey`, `accountID`, `deviceID`, `viewedAt`) [S6]. Tautulli records every session it saw with `started`, `stopped`, `paused_counter`, `play_duration`, `view_offset` and a computed `percent_complete`, plus the client (`product`, `player`, `platform`, `machine_id`) [S1, S2]. That is enough to infer "finished" versus "skipped" plays, which Plex alone cannot tell you.

Use plain HTTP (one `GET /api/v2?cmd=get_history` with an `X-Api-Key` header). A client library isn't worth adding.

## Connecting

- Endpoint: `http(s)://HOST:PORT[/HTTP_ROOT]/api/v2?cmd=<command>`. Default port 8181 [S1].
- Auth: send the API key (Tautulli Settings > Web Interface) in the `X-Api-Key` header (v2.18.0+) or as the `apikey` query param. The header is preferred because query strings end up in logs [S1, S3 `_api_validate`].
- The key is **the admin key**, compared against `CONFIG.API_KEY` [S3]. It also unlocks destructive commands (`delete_history`, `delete_all_user_history`) [S1]. Ampanion should store it as a secret and only ever call read commands.
- Response envelope: `{"response": {"result": "success"|"error", "message": ..., "data": ...}}` [S1].
- To check that Tautulli watches the same Plex server ampanion uses, compare `get_server_identity` -> `machine_identifier` with the Plex server's `machineIdentifier` [S1].

## Getting music Listening history: `get_history`

Useful params [S1, S4 `get_history`]:

| Param | Use |
|---|---|
| `media_type=track` | Music only. Matched against `media_type_live` [S4]. |
| `user_id=<id>` | One Listener. A comma list also works (`IN` clause) [S4]. |
| `grouping=0` | One row per session. With `grouping=1` (or the server default `GROUP_HISTORY_TABLES` when omitted) consecutive sessions of the same item are merged by `reference_id`, so `play_duration` is summed and `percent_complete` is the max [S2]. **Always pass it explicitly.** |
| `after` / `before` / `start_date` | `YYYY-MM-DD`, converted to a timestamp and compared with `started` [S4]. Day granularity only. |
| `section_id` | Limit to the music library. |
| `order_column=date&order_dir=desc` | Default sort [S4]. |
| `start`, `length` | Offset paging. **Default `length` is 25** [S5 `build_datatables_json`]. `length=-1` returns every row (SQLite `LIMIT -1`) [S7]. |
| `include_activity=0` | Leave out sessions that are still playing (they have `state` set and no `row_id`) [S2]. |

Fields per row that matter to ampanion [S1, S2]:

- Identity: `row_id` (stable PK of one play), `reference_id` (group key), `user_id`, `user`, `friendly_name`.
- Item: `rating_key`, `parent_rating_key` (album), `grandparent_rating_key` (artist), `title`, `parent_title`, `grandparent_title`, `original_title` (track artist), `guid`, `year`. Rows do not include `section_id` (it is only a filter).
- Timing: `started`, `stopped`, `date` (epoch seconds), `paused_counter`, `play_duration` = (stopped - started) - paused, in seconds.
- Completion: `percent_complete` = `view_offset / duration * 100`, rounded to an int. `watched_status` is 0 / 0.25 / 0.5 / 0.75 / 1 relative to the **Music Watched Percent** setting (`MUSIC_WATCHED_PERCENT`) [S2].
- Client: `product` (e.g. "Plexamp"), `player`, `platform`, `machine_id`, `location` (lan/wan).

The response also carries `recordsTotal`, `recordsFiltered`, `total_duration` and `filter_duration` [S1].

### Paging strategy

Offset paging over a list sorted newest-first shifts when new plays arrive during the walk. Ampanion should sync incrementally instead: request `after=<last sync date>` with `grouping=0`, either with `length=-1` or in pages, and dedupe by `row_id`. Because `after` only works to the day, re-fetching the boundary day and deduping is expected. The first backfill per Listener can use `length=-1`, or pages of a few thousand rows to keep responses bounded. Tautulli has no documented rate limit. It is a single SQLite-backed process [S7], so be polite (sequential requests, not a burst).

## Mapping Tautulli users to Plex Listeners

- Tautulli's `user_id` is the **plex.tv account id**. `refresh_users` fills the users table from plex.tv's user list, taking `id` from the `<User>`/account XML [S8 `get_full_users_list`, S9 `refresh_users`]. `get_users` / `get_user_names` list `user_id`, `username`, `friendly_name`, `email` and `keep_history` [S1].
- Plex's local server history uses **server-local `accountID`s** (`SystemAccount`), and the **server owner is `accountID=1`** locally [S6 `plexapi/server.py`, "The admin account is accountID=1"]. Managed and shared users usually show their plex.tv id there too. So `Tautulli.user_id == Plex accountID` holds for everyone **except the owner**, whose Tautulli id is their real plex.tv id. Ampanion should map Listeners by plex.tv account id (python-plexapi `MyPlexAccount().id` and `users()`), treat local `accountID=1` as the owner, and match on `username` only as a fallback. *(Not verified against a live server: confirm during the Plex history spike.)*
- `user_id=0` is Tautulli's "Local" user (unauthenticated local playback) [S9]. Ignore it or flag it.

## Caveats that later decisions depend on

1. **Very short skips are not recorded.** A track play is dropped if `real_play_time < 15s` and the track is at least 30s long. This is hard-coded, not configurable, and applies to live sessions only (not imports) [S10 `write_session_history`]. So Tautulli can show "played 15s+ then skipped" (low `percent_complete`) but **not** an instant skip. Absence of a row isn't evidence of a skip.
2. **History only exists from when Tautulli was installed**, and only for sessions it observed while running and connected. Plays during downtime are lost, though Plex's own `viewedAt` history may still have them. Offline/downloaded Plexamp plays are only seen if Plex reports them as sessions (unverified).
3. **Per-user and per-library opt-out:** if `keep_history` is off for a user or library, nothing is logged [S10]. Ampanion can read `keep_history` from `get_users` and tell the Listener.
4. **Retention:** there is no automatic purge setting in the API surface. Rows go away only through manual `delete_history` / `delete_all_user_history` or a database import with `overwrite` [S1]. Treat Tautulli history as possibly edited, and key on `row_id`.
5. **`grouping` silently changes semantics** depending on a server setting if omitted (see table). Always send `grouping=0`.
6. **`percent_complete` comes from the last `view_offset`.** Seeking forward inflates it, and `play_duration` is the better "actually listened" signal. For gapless/crossfade in Plexamp, the final offset may land a few seconds short of `duration`, so use a threshold rather than 100%.
7. Plexamp plays are identifiable by `product` (Plexamp) [S2]. Nothing indicates Tautulli treats them differently from other music clients.

## Python client: plain HTTP

- `tautulli` on PyPI (nwithan8/pytulli), v4.8.2.2180, last upload 2026-09-29. It is actively maintained and covers the whole API [S11]. It adds a dependency and its own models for what is, for ampanion, about 3 GET calls.
- `pytautulli` (ludeeus, the Home Assistant integration's client), v23.1.1, last release 2023-01. It is async and stale [S11].
- Recommendation: use `httpx` (or `requests`, whichever ampanion already uses for HTTP) against `get_history`, `get_users`, `get_server_identity`. The API returns flat JSON and needs no auth flow, so a client buys little.

## Sources

- [S1] Tautulli API reference (wiki): https://github.com/Tautulli/Tautulli/wiki/Tautulli-API-Reference (General structure, `get_history`, `get_users`, `get_user_names`, `get_server_identity`, `delete_history`, `delete_all_user_history`, `import_database`)
- [S2] `plexpy/datafactory.py` `DataFactory.get_datatables_history`: https://github.com/Tautulli/Tautulli/blob/master/plexpy/datafactory.py
- [S3] `plexpy/api2.py` `API2._api_validate`: https://github.com/Tautulli/Tautulli/blob/master/plexpy/api2.py
- [S4] `plexpy/webserve.py` `WebInterface.get_history`: https://github.com/Tautulli/Tautulli/blob/master/plexpy/webserve.py
- [S5] `plexpy/helpers.py` `build_datatables_json`: https://github.com/Tautulli/Tautulli/blob/master/plexpy/helpers.py
- [S6] python-plexapi `plexapi/server.py` (`PlexServer.history`, `/status/sessions/history/all`, accountID=1 admin) and `plexapi/base.py` (`PlexHistory` fields): https://github.com/pkkid/python-plexapi
- [S7] `plexpy/datatables.py` (`LIMIT ? OFFSET ?`, `length < 0 -> -1`): https://github.com/Tautulli/Tautulli/blob/master/plexpy/datatables.py
- [S8] `plexpy/plextv.py` `PlexTV.get_full_users_list`: https://github.com/Tautulli/Tautulli/blob/master/plexpy/plextv.py
- [S9] `plexpy/users.py` `refresh_users`: https://github.com/Tautulli/Tautulli/blob/master/plexpy/users.py
- [S10] `plexpy/activity_processor.py` `ActivityProcessor.write_session_history`: https://github.com/Tautulli/Tautulli/blob/master/plexpy/activity_processor.py
- [S11] PyPI JSON: https://pypi.org/pypi/tautulli/json, https://pypi.org/pypi/pytautulli/json
