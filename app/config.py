"""Public configuration: non-secret identifiers, safe to commit.

These are product-wide defaults compiled into every build. Environment variables (or the
git-ignored `.env`) override them for development and staging. Secrets never go here.
"""

# Login — Clerk OAuth application (authorization code + PKCE, public client): public by design, no secret.
# The issuer is the Clerk Frontend API host of the instance (development instance for now: pk_test_…).
CLERK_ISSUER = "https://electric-sculpin-5651.clerk.accounts.dev"
CLERK_CLIENT_ID = "W5mese1fmaGheY3M"

# husk-gateway: the only door to paid AI. Called with the signed-in user's Clerk access token (Bearer).
GATEWAY_URL = "https://huskai-gateway-production.up.railway.app"

# AI — every call goes through husk-gateway (Regolo.ai for text, Venice for decisions). Models the gateway allows.
DEFAULT_LLM_MODEL = "gpt-oss-120b"
DEFAULT_SUMMARY_MODEL = "gpt-oss-20b"  # document summaries (discovery step 3)

# Decision model — typed decisions with calibrated probabilities instead of text, via the gateway (`/v1/decide`).
DECISION_MODEL = "jev-latest"
GATE_RELATED = 0.75
MAX_NETWORK_DOCS = 4  # larger link components are split at their weakest edges
GATE_DEEPER = 0.6
GATE_REPETITIVE = 0.5

# Neon husk-core — anonymous, read-only Data API (maintenance + releases views only). The Data API accepts
# only JWTs: the app fetches a short-lived `anonymous` token from Neon Auth (public, no secret) for each session.
NEON_DATA_API_URL = "https://ep-cold-wind-b19p2mw3.apirest.c-5.eu-central-1.aws.neon.tech/neondb/rest/v1"
# Neon Auth base URL of the husk-core branch (Neon console → Auth, anonymous tokens on). Unset = Neon checks off.
NEON_AUTH_URL: str | None = None

# Husk Studio — the optional local debugger. Where users can get it when it isn't running.
HUSK_STUDIO_INSTALL_URL = "https://github.com/Husk-ai-lab/husk-ai"

# Knowledge base — hard cap on documents per organisation (for now): keeps discovery one-shot and bounded.
MAX_DOCUMENTS_PER_ORG = 50

# Languages — add a code here and a file in app/i18n/locales/ to support a new language.
SUPPORTED_LANGUAGES = ("en", "it")
DEFAULT_LANGUAGE = "en"
