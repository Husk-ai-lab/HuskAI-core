"""Public configuration: non-secret identifiers, safe to commit.

These are product-wide defaults compiled into every build. Environment variables (or the
git-ignored `.env`) override them for development and staging. Secrets never go here.
"""

# Login — ZITADEL native app (OIDC + PKCE): public by design, no client secret.
ZITADEL_ISSUER = "https://huskai-main-product-mau5os.eu1.zitadel.cloud"
ZITADEL_CLIENT_ID = "393503088868134289"

# AI — Regolo.ai (Italy, zero retention). Each user supplies the key Husk AI gives them.
REGOLO_BASE_URL = "https://api.regolo.ai/v1"
DEFAULT_REGOLO_MODEL = "gpt-oss-120b"  # matched against Regolo's /models ids at key verification

# Neon husk-core — anonymous, read-only Data API (maintenance + releases views only).
NEON_DATA_API_URL = "https://ep-cold-wind-b19p2mw3.apirest.c-5.eu-central-1.aws.neon.tech/neondb/rest/v1"

# Languages — add a code here and a file in app/i18n/locales/ to support a new language.
SUPPORTED_LANGUAGES = ("en", "it")
DEFAULT_LANGUAGE = "en"
