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
DEFAULT_SUMMARY_MODEL = "gpt-oss-20b"  # document summaries (discovery step 3); main model if not offered

# Decision model — Venice AI's Jev (temporary, until Husk AI's own Serenity ships). Typed decisions with
# calibrated probabilities instead of text. Gates: below them an answer counts as "no" (uncertain = no).
VENICE_BASE_URL = "https://api.venice.ai/api/v1"
DECISION_MODEL = "jev-latest"
VENICE_KEY_PREFIX = "VENICE_INFERENCE_KEY_"  # the combined key = <Regolo key><Venice key>, split on this
GATE_RELATED = 0.75
MAX_NETWORK_DOCS = 4  # larger link components are split at their weakest edges
GATE_DEEPER = 0.6
GATE_REPETITIVE = 0.5

# Neon husk-core — anonymous, read-only Data API (maintenance + releases views only).
NEON_DATA_API_URL = "https://ep-cold-wind-b19p2mw3.apirest.c-5.eu-central-1.aws.neon.tech/neondb/rest/v1"

# Husk Studio — the optional local debugger. Where users can get it when it isn't running.
HUSK_STUDIO_INSTALL_URL = "https://github.com/Husk-ai-lab/husk-ai"

# Knowledge base — hard cap on documents per organisation (for now): keeps discovery one-shot and bounded.
MAX_DOCUMENTS_PER_ORG = 50

# Languages — add a code here and a file in app/i18n/locales/ to support a new language.
SUPPORTED_LANGUAGES = ("en", "it")
DEFAULT_LANGUAGE = "en"
