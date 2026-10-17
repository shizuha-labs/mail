# Standalone deployment — "Sign in with Shizuha ID" (PLAT-353)

mail deploys independently of the shizuha-stack monorepo. Auth = OIDC against
Shizuha ID (authz-code + PKCE, BFF; RS256 validated via JWKS).

## Env contract (backend)
| Var | Value |
|---|---|
| SHIZUHA_OAUTH_CLIENT_ID / SECRET | from k8s Secret `shizuha-mail-oauth` (client registered on ID) |
| MAIL_CREDENTIAL_ENCRYPTION_KEY | from k8s Secret `shizuha-mail-credentials` key `ENCRYPTION_KEY`; this is the Fernet KEK for MAIL-9 credential envelopes and must not be stored in the DB or repo |
| MAIL_CREDENTIAL_KEY_ID | non-secret key identifier embedded in credential envelopes, e.g. `mail-credentials-v1` |
| MAIL_CREDENTIAL_ENCRYPTION_KEYS | optional JSON mapping of historical/current key IDs to Fernet keys for a rotation window, e.g. `{"mail-credentials-v1":"...","mail-credentials-v2":"..."}` |
| SHIZUHA_OAUTH_ISSUER | ID base URL (token endpoint = `{issuer}/api/oauth/token`) |
| SHIZUHA_OAUTH_AUTHORIZE_URL | browser-facing consent page, e.g. `https://…/id/oauth/authorize` |
| SHIZUHA_OAUTH_JWKS_URL | `{issuer}/.well-known/jwks.json` |
| SHIZUHA_EXPECTED_ISSUER / SHIZUHA_SERVICE_NAME | iss check / aud=`mail` |
| MAIL_PUBLIC_URL / MAIL_FRONTEND_URL | public origin (callback) / SPA landing |
| SHIZUHA_AUD_ENFORCE=true | reject tokens not minted for mail |
| MAIL_DISABLE_HS256=1 | **final cutover only** — see below |

## HS256 grace (IMPORTANT)
Agents authenticate to the mail MCP with HS256 service tokens today. RS256-only
mode (`MAIL_DISABLE_HS256=1`) locks them out (verified live: 401). Keep the
grace (default) until agent M2M tokens migrate to OAuth client-credentials,
then flip the env var. Human sign-in is already fully OIDC either way.

## MAIL-9 credential key boundary

Persisted IMAP/SMTP credentials are stored as encrypted envelopes in the Mail
database. The envelope ciphertext is useless without the Fernet key material,
which is injected separately from Kubernetes Secret `shizuha-mail-credentials`;
do not put keys in config maps, checked-in manifests with literal values, or the
database.

Safe rotation requires a dual-key window:
1. Mount both old and new Fernet keys via `MAIL_CREDENTIAL_ENCRYPTION_KEYS`.
2. Set `MAIL_CREDENTIAL_KEY_ID` + `MAIL_CREDENTIAL_ENCRYPTION_KEY` to the new
   key.
3. Run `python manage.py migrate_mail_credentials`; existing envelopes with the
   old `key_id` are decrypted with the old key and re-enveloped with the new
   key.
4. Verify every account decrypts under the new envelope, then remove the old key
   from `MAIL_CREDENTIAL_ENCRYPTION_KEYS`.

If a stored value has Fernet-token shape but cannot be decrypted with the
configured key set, the migration aborts instead of treating it as plaintext.
That fail-closed behavior prevents wrong-key rotation from silently
re-encrypting garbage and corrupting credentials.

## Frontend
Build with `/login` routed (standalone "Sign in with Shizuha ID" page);
`VITE_LOGIN_URL` reverts to `/id/login` for platform-integrated deployments.
