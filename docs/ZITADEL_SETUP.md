# ZITADEL setup (login only)

Oct 3, 2026 · @Marco

Husk uses ZITADEL's hosted login with **OpenID Connect authorization code + PKCE**. The password is typed on ZITADEL's page in the browser, never in Husk. No client secret ships in the app.

1. **Project**: create a project "Husk". Optional roles: `owner`, `admin`, `member`. Enable "Assert roles on authentication".
2. **Application**: type **Native**, auth method **PKCE**.
   * Redirect URI: `http://127.0.0.1:8765/auth/callback` (one per port you use; the shell's port in production).
   * Post-logout URI: `http://127.0.0.1:8765/accesso`.
   * Enable refresh tokens.
3. **No sign-up (login only)**: Instance/Organization → Login behaviour → **disable "User registration allowed"** and external IdP self-registration. Users are created by an admin (or via the gateway later).
4. **One organisation per customer**: Husk scopes all local data by the user's `resourceowner` organisation. The scope `urn:zitadel:iam:user:resourceowner` is requested automatically.
5. Public values live in `app/config.py` (committed). Override them in `.env` only for another instance (e.g. staging):
   ```env
   ZITADEL_ISSUER=https://<instance>.zitadel.cloud
   ZITADEL_CLIENT_ID=<client id of the native app>
   ZITADEL_PROJECT_ID=<project id>   # optional, for roles in the token
   ```

Tokens: ID token verified against ZITADEL's JWKS (issuer, audience, expiry, nonce). Refresh token in the OS keychain; access token kept in memory and lent to Husk Studio through `/internal/studio-token`.
