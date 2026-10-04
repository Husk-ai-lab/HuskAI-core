"""Runtime configuration.

Values come from environment variables or the `.env` file at the repository root
(next to the executable when frozen with PyInstaller). The future desktop shell passes
`HUSK_PORT`, `HUSK_DATA_DIR`, `HUSK_LAUNCH_SECRET` and the Studio URLs as env vars.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app import config


def project_root() -> Path:
    if getattr(sys, "frozen", False):  # PyInstaller one-folder build
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def resource_root() -> Path:
    """Where bundled read-only resources live (PyInstaller unpacks them to sys._MEIPASS)."""
    return Path(getattr(sys, "_MEIPASS", project_root()))



Effort = Literal["low", "medium"]  # never "high" for now

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=project_root() / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    env: Literal["dev", "prod", "test"] = Field("dev", validation_alias=AliasChoices("HUSK_ENV"))
    host: str = Field("127.0.0.1", validation_alias=AliasChoices("HUSK_HOST"))
    port: int = Field(8765, validation_alias=AliasChoices("HUSK_PORT"))
    data_dir: Path | None = Field(None, validation_alias=AliasChoices("HUSK_DATA_DIR"))
    launch_secret: str | None = Field(None, validation_alias=AliasChoices("HUSK_LAUNCH_SECRET"))
    # Where the browser UI lives in development (Vite dev server); in production the
    # UI is served by FastAPI itself from app/frontend/dist.
    frontend_dev_url: str | None = Field(None, validation_alias=AliasChoices("HUSK_FRONTEND_DEV_URL"))

    # --- Regolo.ai (temporary: called directly until husk-gateway exists) -------------
    regolo_api_key: str | None = Field(None, validation_alias=AliasChoices("REGOLO_API_KEY"))
    # --- Decision model (Venice Jev) ---------------------------------------------------
    venice_api_key: str | None = Field(None, validation_alias=AliasChoices("VENICE_API_KEY"))  # dev fallback
    venice_base_url: str = Field(config.VENICE_BASE_URL, validation_alias=AliasChoices("VENICE_BASE_URL"))
    decision_model: str = Field(config.DECISION_MODEL, validation_alias=AliasChoices("HUSK_DECISION_MODEL"))
    gate_related: float = Field(config.GATE_RELATED, validation_alias=AliasChoices("HUSK_GATE_RELATED"))
    gate_deeper: float = Field(config.GATE_DEEPER, validation_alias=AliasChoices("HUSK_GATE_DEEPER"))
    gate_repetitive: float = Field(config.GATE_REPETITIVE, validation_alias=AliasChoices("HUSK_GATE_REPETITIVE"))
    max_network_docs: int = Field(config.MAX_NETWORK_DOCS, validation_alias=AliasChoices("HUSK_MAX_NETWORK_DOCS"))
    regolo_base_url: str = Field(config.REGOLO_BASE_URL, validation_alias=AliasChoices("REGOLO_BASE_URL"))
    regolo_model: str | None = Field(config.DEFAULT_REGOLO_MODEL, validation_alias=AliasChoices("REGOLO_MODEL"))
    summary_model: str | None = Field(config.DEFAULT_SUMMARY_MODEL, validation_alias=AliasChoices("HUSK_SUMMARY_MODEL"))
    llm_temperature: float = Field(0.2, validation_alias=AliasChoices("HUSK_LLM_TEMPERATURE"))
    llm_timeout_s: float = Field(120.0, validation_alias=AliasChoices("HUSK_LLM_TIMEOUT_S"))

    # --- ZITADEL (public values: PKCE needs no client secret) ------------------------
    zitadel_issuer: str | None = Field(config.ZITADEL_ISSUER, validation_alias=AliasChoices("ZITADEL_ISSUER"))
    zitadel_client_id: str | None = Field(config.ZITADEL_CLIENT_ID, validation_alias=AliasChoices("ZITADEL_CLIENT_ID"))
    zitadel_project_id: str | None = Field(None, validation_alias=AliasChoices("ZITADEL_PROJECT_ID"))

    # --- Neon husk-core: anonymous Data API, read-only (maintenance + releases) -------
    # Public by design: no credential. Reads only the views granted to `anonymous`.
    neon_data_api_url: str | None = Field(config.NEON_DATA_API_URL, validation_alias=AliasChoices("NEON_DATA_API_URL"))
    maintenance_poll_s: int = Field(60, validation_alias=AliasChoices("HUSK_MAINTENANCE_POLL_S"))

    # --- Husk Studio (development debugger, optional) --------------------------------
    studio_ui_url: str = Field("http://127.0.0.1:7654", validation_alias=AliasChoices("HUSK_STUDIO_UI_URL"))
    studio_otlp_url: str = Field(
        "http://127.0.0.1:7654", validation_alias=AliasChoices("HUSK_STUDIO_OTLP_URL")
    )
    studio_mcp_url: str = Field(
        "http://127.0.0.1:7655/mcp", validation_alias=AliasChoices("HUSK_STUDIO_MCP_URL")
    )
    studio_run_url_template: str = Field(
        "{base}/runs/{trace_id}", validation_alias=AliasChoices("HUSK_STUDIO_RUN_URL_TEMPLATE")
    )
    studio_probe_s: int = Field(30, validation_alias=AliasChoices("HUSK_STUDIO_PROBE_S"))

    # --- Harness limits ---------------------------------------------------------------
    max_company_facts: int = 30
    discovery_max_docs_deep: int = Field(4, validation_alias=AliasChoices("HUSK_DISCOVERY_MAX_DOCS"))
    max_documents: int = Field(config.MAX_DOCUMENTS_PER_ORG, validation_alias=AliasChoices("HUSK_MAX_DOCUMENTS"))
    discovery_concurrency: int = Field(8, validation_alias=AliasChoices("HUSK_DISCOVERY_CONCURRENCY"))
    builder_concurrency: int = Field(4, validation_alias=AliasChoices("HUSK_BUILDER_CONCURRENCY"))
    # one document never holds up step 3: past this deadline it gets a basic card now and a full one later
    summary_deadline_s: float = Field(25.0, validation_alias=AliasChoices("HUSK_SUMMARY_DEADLINE_S"))
    # then wait up to this long for those late summaries before the Explorer runs (important files stay full)
    summary_late_wait_s: float = Field(20.0, validation_alias=AliasChoices("HUSK_SUMMARY_LATE_WAIT_S"))
    # Reasoning effort per role. Policy: low across discovery, medium for the Builder (it talks to the
    # user) and automation runs. "high" is not allowed for now.
    reasoning_summary: Effort = Field("low", validation_alias=AliasChoices("HUSK_REASONING_SUMMARY"))
    reasoning_explorer: Effort = Field("low", validation_alias=AliasChoices("HUSK_REASONING_EXPLORER"))
    reasoning_planner: Effort = Field("low", validation_alias=AliasChoices("HUSK_REASONING_PLANNER"))
    reasoning_builder: Effort = Field("medium", validation_alias=AliasChoices("HUSK_REASONING_BUILDER"))
    reasoning_runtime: Effort = Field("medium", validation_alias=AliasChoices("HUSK_REASONING_RUNTIME"))
    discovery_max_opportunities: int = Field(8, validation_alias=AliasChoices("HUSK_DISCOVERY_MAX_OPPS"))
    agent_max_iterations: int = Field(10, validation_alias=AliasChoices("HUSK_AGENT_MAX_ITERATIONS"))
    # seconds after the LAST upload before suggestions are computed (one job per batch / folder)
    suggestions_debounce_s: float = Field(15.0, validation_alias=AliasChoices("HUSK_SUGGESTIONS_DEBOUNCE_S"))

    @property
    def llm_configured(self) -> bool:
        return bool(self.regolo_api_key)

    @property
    def zitadel_configured(self) -> bool:
        return bool(self.zitadel_issuer and self.zitadel_client_id)

    @property
    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self.port}/auth/callback"


@lru_cache
def get_settings() -> Settings:
    return Settings()
