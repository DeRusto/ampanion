# How does owner-only Plex sign-in work for the web UI?

Research for issue #5. Researched 2026-10-21. Terms follow GLOSSARY.md (Mix, Playlist, Listener, Listening history).

## Answer in short

- **Flow:** use Plex's documented PIN "forwarding" flow. The server creates a strong PIN at plex.tv, redirects the browser to `app.plex.tv/auth` with a `forwardUrl` back to ampanion, then exchanges the PIN id for the account's token. python-plexapi's `MyPlexPinLogin(oauth=True)` wraps this, but it is built for polling. A web callback needs only two plain HTTP calls (create PIN, check PIN), so either drive the class's pieces or call plex.tv directly.
- **Owner check:** with the signed-in token, list `https://plex.tv/api/v2/resources`. Accept the person only if the resource whose `clientIdentifier` equals the configured server's machine identifier has `owned="1"`. Home members and shared users see the same server with `owned="0"`. Overseerr and Tautulli do something simpler: they remember the owner's plex.tv account id from setup and compare ids.
- **Session:** a signed, HttpOnly session cookie that holds only the owner's plex.tv account id and a login timestamp, never a Plex token. FastAPI/Starlette `SessionMiddleware` gives that out of the box.
- **Stored:** a stable `X-Plex-Client-Identifier` (UUID generated once), the owner's plex.tv account id, the server's machine identifier, the owner's Plex token (needed for background Playlist refreshes), and a random session-signing secret. All of it lives in the data volume.
- **Token lifetime:** the legacy PIN token has **no documented expiry**. Plex's newer JWT scheme expires every **7 days** and needs a stored Ed25519 key pair to refresh. Registering a JWT key makes the legacy token for that device expire. That is a decision for later (see Open questions).

## 1. The sign-in flow (primary: Plex docs)

Plex documents the flow in "Authenticating with Plex" ([developer.plex.tv/pms, section API-Info/Authenticating-with-Plex](https://developer.plex.tv/pms/#section/API-Info/Authenticating-with-Plex); the older forum copy is [forums.plex.tv/t/authenticating-with-plex/609370](https://forums.plex.tv/t/authenticating-with-plex/609370)):

1. **Client identifier.** Generate a random string or UUID once, store it and reuse it on every request as `X-Plex-Client-Identifier`. Send `X-Plex-Product` too (it is the name the owner sees under Authorized Devices).
2. **Create PIN.** `POST https://plex.tv/api/v2/pins` with `strong=true`, plus the product and client-identifier headers. The response gives `id` (keep it on the server) and `code`. Strong PINs live longer than the 4-character plex.tv/link PINs. No exact duration is documented.
3. **Send the browser to the Auth App:**
   `https://app.plex.tv/auth#?clientID=<clientIdentifier>&code=<pinCode>&forwardUrl=<ampanion callback>&context%5Bdevice%5D%5Bproduct%5D=Ampanion`
   (python-plexapi builds the same URL with the `#!?` variant and extra `context[device][...]` fields; see `MyPlexPinLogin.oauthUrl`.)
4. **Check PIN on return.** In the forwarding flow, the owner lands back on `forwardUrl`. Then call `GET https://plex.tv/api/v2/pins/<id>` (with the client identifier). `authToken` holds the token, or stays `null` if sign-in failed or was abandoned. Native apps poll this about once per second instead.
5. **Validate a token later.** `GET https://plex.tv/api/v2/user` with `X-Plex-Token`: 200 means the token is valid, and 401 means it is invalid and should be discarded. Any other status, or plex.tv being unreachable, is an error, not proof that the token is invalid. Only re-run the PIN flow on 401.

`forwardUrl` is a plain browser redirect, so a LAN address like `http://192.168.1.10:8080/auth/callback` works. plex.tv never has to reach ampanion.

### python-plexapi (v4.18.3, `plexapi/myplex.py`)

Source: [pushingkarmaorg/python-plexapi `plexapi/myplex.py`](https://github.com/pushingkarmaorg/python-plexapi/blob/master/plexapi/myplex.py).

- `MyPlexPinLogin(oauth=True, headers=...)`: `_getCode()` POSTs to `https://plex.tv/api/v2/pins` with `strong=True`. `oauthUrl(forwardUrl)` returns the `app.plex.tv/auth/#!?...` URL. `checkLogin()` / `_checkLogin()` GETs `pins/<id>` and reads `authToken`. `run()` / `waitForLogin()` spin a polling thread (default 120 s timeout).
  - Fit for a web app: the PIN `id` lives in a private attribute (`_id`) of one Python object, so a callback in a different request needs that object kept in server memory, keyed by session. Calling the two plex.tv endpoints directly (with httpx/requests) is about the same amount of code and survives restarts. Don't use `run()` threads per login in a web server.
- `MyPlexJWTLogin` (same file) implements the newer JWT scheme. It needs `pyjwt[crypto]`, generates an Ed25519 key pair, uses `https://clients.plex.tv/api/v2/pins` / `/api/v2/auth`, and has `verifyJWT(refreshWithinDays=1)` and `refreshJWT()`.
- `MyPlexAccount(token=...)` gives the account (`id`, `uuid`, `username`, `email`). `MyPlexAccount.resources()` returns `MyPlexResource` objects whose `owned` field is documented as "True if this resource is one of your own".

## 2. Verifying the person owns the configured server

Recommended check, done once per sign-in and costing one plex.tv call:

```
resources = GET https://plex.tv/api/v2/resources   (X-Plex-Token = new token)
ok = any(r.clientIdentifier == configured_machine_id
         and "server" in r.provides
         and r.owned)
```

- The configured server's machine identifier comes from the PMS itself (`GET http://<pms>/identity` → `machineIdentifier`, or `PlexServer.machineIdentifier` in python-plexapi).
- `owned` comes from `MyPlexResource._loadData` (`data.attrib.get('owned')`). For shared resources, plex.tv also sets `ownerId` / `sourceTitle` to the owner, so a shared user or Home member sees the server with `owned` false. python-plexapi itself uses the same test to find the account's own servers: `[x for x in self.resources() if x.provides == 'server' and x.owned]` (myplex.py, around line 898).
- Simpler alternative (what the prior art does): at first setup, record the owner's plex.tv account `id`. On each later sign-in, call `GET /api/v2/user` and compare `id`. That avoids the resources call, but it trusts that setup was done by the owner.

Prior art:

- **Overseerr** (`server/routes/auth.ts`, [sct/overseerr](https://github.com/sct/overseerr/blob/develop/server/routes/auth.ts)): the **first** Plex account to sign in becomes admin (trust on first use) and its `plexToken` is saved. Later sign-ins match `account.id === mainUser.plexId`. Other users are let in only if the admin's friends list shows them sharing `settings.plex.machineId` (`checkUserAccess`). The browser runs the PIN flow itself: a popup to `app.plex.tv/auth`, then polling `plex.tv/api/v2/pins/<id>` every 1 s from JS (`src/utils/plex.ts`), with the client id kept in `localStorage`.
- **Tautulli** (`plexpy/webauth.py`, `plexpy/plextv.py`, [Tautulli/Tautulli](https://github.com/Tautulli/Tautulli)): the account whose token was configured at setup is stored as `is_admin = 1`. A Plex OAuth sign-in resolves the token to a plex.tv user id and grants admin if that user is `is_admin`. Its `plextv.py` notes that plex.tv echoes the caller's own token as an owned server's `accessToken`, so you cannot tell ownership by comparing tokens. Use `owned` / account id instead.

Home caveat: a Plex Home member (including a managed user) authenticates as their own plex.tv account, with its own `id`, so both checks above reject them. ampanion must not use "can reach the server" or "has a server token" as an owner test, because Home members and friends pass both.

## 3. Browser session

FastAPI uses Starlette's `SessionMiddleware` ([starlette/middleware/sessions.py](https://github.com/encode/starlette/blob/master/starlette/middleware/sessions.py)):

- Cookie data is JSON, base64-encoded and signed with `itsdangerous.TimestampSigner(secret_key)`. It is **signed, not encrypted**, so anyone who can see the cookie can read its contents. Store only `{owner_account_id, login_at}`, never the Plex token.
- Defaults: `max_age` 14 days, `same_site="lax"`, always `HttpOnly`, `https_only=False` (no `Secure` flag). Set `https_only=True` only when served over HTTPS (for example behind a reverse proxy).
- Store the PIN `id` in this session between `/login` and the callback, and don't trust a PIN id from the query string. That ties the callback to the browser that started it (prevents login-CSRF / session fixation).
- Logout: clear the session. Server-side revocation is not possible with signed-cookie sessions alone. If needed, store a session generation number in SQLite and compare it. Overseerr keeps server-side sessions in its database (express-session store). Tautulli issues its own HS256 JWT cookie (60 min, or 30 days with "remember me") and records login tokens in its DB so they can be revoked.

On each request, the dependency checks `session["owner_account_id"] == stored owner id`. It doesn't need to call plex.tv.

## 4. What ampanion stores

| Item | Why | Where |
|---|---|---|
| `X-Plex-Client-Identifier` (UUID) | Plex says to generate it once and reuse it. It keeps ampanion one entry in Authorized Devices | SQLite / config in data volume |
| Session signing secret (random 32+ bytes) | `SessionMiddleware` secret. Generate it on first start and do not hard-code it | File in data volume (0600), or env var override |
| Owner plex.tv account `id` (+ username for display) | Session check and owner re-check | SQLite |
| Configured server `machineIdentifier` + URL | Ownership check, connecting to PMS | SQLite |
| Owner Plex token | Background Playlist refreshes run with no browser present. The owner token is what lets ampanion act for each Listener (e.g. python-plexapi `MyPlexAccount.user(...)` / `switchUser`, covered by a separate ticket) | SQLite in data volume. Never in cookies or logs |
| (If JWT is adopted) Ed25519 private key + current JWT | 7-day refresh | Data volume |

## 5. Token lifetime

- **Legacy PIN token** (what `MyPlexPinLogin` returns today): Plex documents no lifetime. It stays valid until revoked, either by the owner removing the device under Authorized Devices or by `MyPlexAccount.signout()`, which "Invalidates the authentication token". Handle a 401 from `/api/v2/user` by marking the owner token dead, pausing background refreshes, and asking the owner to sign in again in the UI.
- **Plex JWT** (Plex's recommended scheme in current docs): "Tokens expire after 7 days". You refresh by signing a device JWT with your registered Ed25519 key and POSTing to `https://clients.plex.tv/api/v2/auth/token`, which also works after expiry. Keys are registered through the strong PIN flow at `https://clients.plex.tv/api/v2/pins` with a `jwk` body (Plex staff reply, 2025-10-01, in [forums.plex.tv/t/jwt-authentication/931646](https://forums.plex.tv/t/jwt-authentication/931646)). Per the docs' migration note, the legacy token "will expire" once the first JWT is generated. Community reports say requested JWT `scope` had no visible effect, so a JWT is still a full-account credential.
- No deprecation date for legacy tokens is published (checked forum threads above; none states one).

## 6. Security considerations for a LAN-hosted service

- **The owner token is a full plex.tv account credential.** Plex has no working scoped tokens. Anyone who gets it can act as the owner on plex.tv and every one of their servers. Keep it only in SQLite in the data volume, never log it (python-plexapi already masks tokens through `logfilter.add_secret`), and never return it to the browser.
- **Plain HTTP on a LAN** sends the session cookie in clear. That is acceptable for v1 on a trusted LAN, but document putting it behind a reverse proxy with HTTPS (then `https_only=True`). The Plex token itself only travels between ampanion and plex.tv/PMS, and plex.tv calls are HTTPS.
- **Cookies are not isolated by port.** Another web app on the same host (Overseerr on :5055, etc.) is same-site, can read or overwrite cookies for that host name, and is not stopped by `SameSite`. Use a distinctive cookie name (`session_cookie="ampanion_session"`) and don't rely on SameSite alone.
- **CSRF:** `SameSite=Lax` blocks cookies on cross-site POSTs, but not same-site (other LAN apps on the same host). Add a CSRF token for state-changing requests. With HTMX the easy pattern is a per-session token sent as a header via `hx-headers` and checked in a dependency (what Tautulli does with `X-CSRF-Token` and `hmac.compare_digest`). Overseerr uses csurf plus `sameSite: 'strict'` when CSRF protection is on.
- **Login CSRF / fixation:** bind the PIN id to the session (section 3) and rotate (clear and reset) the session on successful sign-in.
- **Owner lockout / first-run hijack:** if ampanion uses trust-on-first-use like Overseerr, whoever reaches the setup page first becomes owner. The `owned` resources check prevents this, because only the real server owner's account passes.
- **Rate limiting:** the sign-in endpoints call plex.tv, so a light per-IP limit avoids hammering plex.tv (Tautulli has `check_rate_limit`). plex.tv returns 429 under load (JWT docs).

## Open questions for later tickets

1. Legacy token vs JWT for the stored owner token. Legacy is simpler (no refresh loop) but is the scheme Plex now calls legacy. JWT needs a stored private key and a refresh job every under-7 days, and turns the legacy token for that client id off. A decision is needed before writing the auth module.
2. Should each web sign-in replace the stored background owner token, or should the stored token come only from setup? If each PIN sign-in reuses the same client identifier, plex.tv should treat it as the same device. That is not verified.
3. Getting per-Listener server tokens from the owner token (for writing Playlists as each Listener) is outside this ticket.

## Not verified

- Exact lifetime of strong vs short PINs (Plex gives none).
- Whether `owned` is ever true for a Home admin who is not the server owner. The docs describe it only as "one of your own". Owner = account that claimed the server is the reading here and in python-plexapi, but it was not tested against a live Home setup.
- No code was run against plex.tv.
