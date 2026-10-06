# Login (Clerk) and husk-gateway

Husk signs users in with **Clerk** (OAuth 2.0 authorisation code + PKCE, public client, no secret) and sends the resulting access token to **husk-gateway** for every AI call. There is no sign-up path and no guest mode.

Source of truth for the gateway: `Company_memory/HuskAI/md/Gateway_execution.md` (Oct 6, 2026).

## How a login runs

```
Husk window (webview or browser)         Husk backend (127.0.0.1:8765)            System browser        Clerk
 "Continue with your Husk account" ──▶  POST /api/v1/user/auth/login
                                         → pending login, state + PKCE verifier
                                         → opens the default browser ─────────────────────────────────▶ /oauth/authorize
                                       ◀── { url, poll_id } (url shown as a fallback)                    sign in (+ organisation)
 polls POST /auth/poll {poll_id}         GET /callback?code&state ◀──────────────────────────────────── redirect
   "pending" …                           → exchange the code, verify the JWT, upsert user/org,
                                           keep the refresh token in the OS keychain,
                                           POST gateway /v1/me, redirect the browser to /auth/done
   "ok" + Set-Cookie husk_session  ◀──   (the callback sets NO cookie)             "Authenticated. You can close this tab."
```

* The **backend** opens the browser (a webview often ignores `window.open`). The Tauri shell never logs in.
* The browser never receives the Husk session: the app window redeems `poll_id` once, so a web page cannot steal it (it has no `poll_id`, and writes are origin-checked by `LocalGuard`).
* `/auth/done` is a normal UI route outside the login gate: same fonts, colours, light/dark, English/Italian.
* The Clerk **User-Agent trap**: Cloudflare answers `403 1010` to Python's default User-Agent on `POST /oauth/token`. Every Clerk and gateway call sends `User-Agent: Husk/<version>` and the JWKS is fetched with httpx.

## Identity

* `user_id` = `sub`, `org_id` = the `org_id` claim of the access token (RS256 JWT, verified against `<issuer>/.well-known/jwks.json`, issuer and `client_id` checked). No `org_id` → login stops with "your account does not belong to an organisation yet".
* The token has no email: it comes from `/oauth/userinfo` (best effort). It has no organisation name or roles either, so the UI shows the email and everybody is a `member`.
* The access token lives 24 h in memory; the refresh token (never expires) is in the OS keychain (`ai.husk.desktop`). `AccessTokens.get(user_id)` refreshes before expiry; the gateway transport renews once on a 401.
* Sign-out deletes the session and the tokens and revokes the refresh token at Clerk.

## What to configure

| Where | What |
| --- | --- |
| Clerk dashboard → Organizations | Enabled, **membership required** (the default for new instances), so every user creates or joins an organisation while signing in |
| Clerk dashboard → OAuth applications → "HuskAI" | Public client, PKCE required, **JWT** access tokens (an opaque token would be rejected by the gateway), scopes `openid profile email offline_access user:org:read` |
| Same app | Redirect URI **exactly** `http://127.0.0.1:8765/callback`. Husk must run on port 8765 (`HUSK_PORT`), or each port must be registered |
| `app/config.py` | `CLERK_ISSUER` (the instance's Frontend API URL), `CLERK_CLIENT_ID`, `GATEWAY_URL` — public values, committed. Override with `CLERK_ISSUER`, `CLERK_CLIENT_ID`, `HUSK_GATEWAY_URL` |
| Railway (gateway) | `AUTH_CLIENT_ID` = the client id above |
| Neon (billing) | A `billing.billing_state` row with `status = 'active'` for the organisation (see the gateway report, section 5.4). Without it every AI call is `403` and Husk shows "this installation is not active yet" |

The `pk_test_…` publishable key is **not** used by the app: it only encodes the issuer, which is stored directly. The current instance is a Clerk *development* instance; production needs its own domain, a recreated OAuth app, a new issuer and client id.

## What the app calls on the gateway

| Call | Used for |
| --- | --- |
| `POST /v1/me` | After login: registers the user and organisation in Neon (best effort) |
| `GET /v1/entitlement` | Opening the app: allowed → open; not allowed → the "installation not active" screen. The last answer is cached per organisation so Husk still opens offline |
| `POST /v1/chat/completions` | Every LLM call (LangChain `ChatOpenAI` pointed at the gateway). Models: `gpt-oss-120b` (main), `gpt-oss-20b` (summaries) |
| `POST /v1/decide` | Discovery decisions (adapter in `gateways/gateway/decisions.py`, see `agents/DECISION_MODEL.md`) |

Limits: 60 requests per minute per user (a client-side sliding window of 55 queues the rest; a full discovery therefore takes a few minutes), `max_tokens` ≤ 8192, 16 questions and 12 000 characters of state per decision request.

Error mapping (all localized): `401 organisation claim missing` → "no organisation"; `401 invalid token` → refresh once, then "sign in again" (the local session ends); `403` → "installation not active"; `429` → "busy"; `5xx`/network → "unavailable".

## Verifying by hand

The gateway verification needs a real token, which only you should handle (never paste one into a chat or a commit; they last 24 h). Sign in through the app, or use the gateway repo's `scripts/get_token.py`, then:

```bash
curl -s -H "Authorization: Bearer $TOKEN" https://huskai-gateway-production.up.railway.app/v1/entitlement
```

* After login: `Settings → usage` and the discovery run exercise `chat` and `decide`. After changing the model, re-run `uv run python -m benchmarks.report`.
* After signing out, logging in again may reuse the browser's Clerk session without asking for a password; if that is a problem, check whether Clerk honours `prompt=login`.
