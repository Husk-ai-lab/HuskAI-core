"""i18n: catalogue parity, every key used in code exists, plurals/fallback, per-request
language for API errors, agents writing in the user's language, public config defaults."""

from __future__ import annotations

import json
import pathlib
import re

import pytest
from fastapi.testclient import TestClient

from app import config
from app.gateways.regolo.provider import pick_model
from app.helpers.settings import Settings
from app.i18n import catalogue, normalize, resolve_lang, t
from test.conftest import ORG, USER, login

ROOT = pathlib.Path(__file__).resolve().parents[1]
LOCALES = ROOT / "app" / "i18n" / "locales"
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _load(lang: str) -> dict[str, str]:
    return json.loads((LOCALES / f"{lang}.json").read_text("utf-8"))


# --- catalogues ------------------------------------------------------------------------------
def test_supported_languages_have_catalogues() -> None:
    assert config.DEFAULT_LANGUAGE == "en"
    for lang in config.SUPPORTED_LANGUAGES:
        assert (LOCALES / f"{lang}.json").exists(), lang


@pytest.mark.parametrize("lang", [x for x in config.SUPPORTED_LANGUAGES if x != config.DEFAULT_LANGUAGE])
def test_same_keys_and_placeholders_as_english(lang: str) -> None:
    en, other = _load("en"), _load(lang)
    assert set(other) == set(en), f"missing: {sorted(set(en) - set(other))}, extra: {sorted(set(other) - set(en))}"
    for key, text in en.items():
        assert set(PLACEHOLDER.findall(other[key])) == set(PLACEHOLDER.findall(text)), key
        assert other[key].strip(), f"{lang}:{key} is empty"


def test_plural_pairs_are_complete() -> None:
    en = _load("en")
    for key in en:
        if key.endswith("_one"):
            assert key[:-4] + "_other" in en, key


def _keys_used_in_code() -> set[str]:
    used: set[str] = set()
    py = re.compile(
        r"(?:\bt\(\s*|api_error\([^,]+,\s*|LocalizedError\(\s*|Rejected\(\s*|BackupError\(\s*|ZitadelError\(\s*"
        r"|NotConfigured\(\s*|key=)\"([a-z_]+\.[a-z0-9_.]+)\""
    )
    for p in (ROOT / "app").rglob("*.py"):
        used |= set(py.findall(p.read_text("utf-8")))
        used |= set(re.findall(r"t\('([a-z_]+\.[a-z0-9_.]+)'", p.read_text("utf-8")))
    ts = re.compile(r"\bt\(\s*\"([a-zA-Z_]+\.[a-zA-Z0-9_.]+)\"")
    for p in (ROOT / "app" / "frontend" / "src").rglob("*.tsx"):
        if "/test/" not in str(p):
            used |= set(ts.findall(p.read_text("utf-8")))
    return used


def test_every_key_used_in_code_exists() -> None:
    en = _load("en")
    plural_bases = {k.rsplit("_", 1)[0] for k in en if k.endswith(("_one", "_other"))}
    missing = sorted(k for k in _keys_used_in_code() if k not in en and k not in plural_bases)
    assert not missing, missing


def test_dynamic_key_families_are_complete() -> None:
    en = _load("en")
    from app.data.models.automation import AutomationStatus
    from app.data.models.discovery import DiscoveryState, DiscoveryStepId, Priority
    from app.data.models.knowledge import FactCategory, NoteKind
    from app.data.models.user import PersonalityId

    families = {
        "discovery.steps": DiscoveryStepId, "enums.automation_status": AutomationStatus, "enums.priority": Priority,
        "enums.discovery_state": DiscoveryState, "enums.fact_category": FactCategory, "enums.note_kind": NoteKind,
    }
    for prefix, enum in families.items():
        for member in enum:
            assert f"{prefix}.{member.value}" in en, f"{prefix}.{member.value}"
    for p in PersonalityId:
        assert f"personalities.{p.value}.label" in en and f"personalities.{p.value}.description" in en


# --- runtime -----------------------------------------------------------------------------------
def test_translate_fallback_plural_and_params() -> None:
    assert t("errors.document_not_found", "it") == "Documento non trovato."
    assert t("errors.document_not_found", "de") == "Document not found."  # unsupported → English
    assert t("no.such.key", "it") == "no.such.key"
    assert t("knowledge.rows", "en", count=1) == "1 row" and t("knowledge.rows", "it", count=3) == "3 righe"
    assert t("errors.maintenance", "it", message={"en": "Update", "it": "Aggiornamento"}) == "Manutenzione in corso: Aggiornamento"
    assert t("common.source", "en") == "Source: {source}"  # missing params stay visible, never crash


def test_language_resolution() -> None:
    assert resolve_lang("it-IT,it;q=0.9,en;q=0.8") == "it"
    assert resolve_lang("fr-FR, de") == "en"
    assert resolve_lang(None) == "en"
    assert normalize("IT_it") == "it" and normalize("xx") == "en"
    assert catalogue("en")["errors.auth_required"]


def test_api_errors_follow_accept_language(client: TestClient) -> None:
    en = client.get("/api/v1/documents/doc_missing", headers={"Accept-Language": "en"})
    it = client.get("/api/v1/documents/doc_missing", headers={"Accept-Language": "it-IT,it;q=0.9"})
    assert en.status_code == it.status_code == 404
    assert en.json()["code"] == it.json()["code"] == "errors.document_not_found"
    assert en.json()["detail"] == "Document not found." and it.json()["detail"] == "Documento non trovato."


def test_personalities_and_steps_localized(client: TestClient) -> None:
    labels_it = {p["id"]: p["label"] for p in client.get("/api/v1/agent/personalities", headers={"Accept-Language": "it"}).json()}
    labels_en = {p["id"]: p["label"] for p in client.get("/api/v1/agent/personalities").json()}
    assert labels_it["ultra_prudente"] == "Ultra-prudente" and labels_en["ultra_prudente"] == "Ultra-careful"


def test_default_user_language_is_english(client: TestClient) -> None:
    assert client.get("/api/v1/user/me").json()["settings"]["language"] == "en"


async def test_agents_write_in_the_users_language(services) -> None:  # type: ignore[no-untyped-def]
    from app.core.agents.explorer.agent import ExplorerAgent
    from app.gateways.sqlite.repositories import UserRepo
    from test.fakes import FakeProvider, default_handler

    login(services)
    seen: list[str] = []

    def recording(messages, tools):  # type: ignore[no-untyped-def]
        seen.append(str(messages[0].content))
        return default_handler(messages, tools)

    services.llm = FakeProvider(recording)
    repo = UserRepo(services.db)
    from app.data.models.user import Language

    repo.save_settings(USER, repo.get_settings(USER).model_copy(update={"language": Language.it}))
    ks = services.knowledge(ORG)
    doc = ks.add("Procedura.md", b"# Procedura\n\nOgni sera il capoturno ricopia i dati a mano.", USER, True)
    ctx = services.agent_context(ORG, USER)
    assert ctx.language == "it"
    await ExplorerAgent(ctx).analyze("run_x", doc)
    assert "in Italian" in seen[-1]
    seen.clear()
    await ExplorerAgent(services.agent_context(ORG, USER, language="en")).analyze("run_y", doc)
    assert "in English" in seen[-1]


# --- public config ---------------------------------------------------------------------------------
def test_public_config_defaults_and_env_override(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    s = Settings(HUSK_ENV="test")
    assert s.zitadel_issuer == config.ZITADEL_ISSUER and s.zitadel_client_id == config.ZITADEL_CLIENT_ID
    assert s.neon_data_api_url == config.NEON_DATA_API_URL and s.regolo_model == config.DEFAULT_REGOLO_MODEL
    monkeypatch.setenv("ZITADEL_ISSUER", "https://staging.example.zitadel.cloud")
    assert Settings(HUSK_ENV="test").zitadel_issuer == "https://staging.example.zitadel.cloud"
    assert Settings(HUSK_ENV="test", ZITADEL_ISSUER=None).zitadel_issuer is None


def test_config_holds_no_secrets() -> None:
    """Only public identifiers: no variable named like a credential."""
    import ast

    tree = ast.parse((ROOT / "app" / "config.py").read_text("utf-8"))
    names = [t.id for node in ast.walk(tree) if isinstance(node, ast.Assign) for t in node.targets if isinstance(t, ast.Name)]
    assert names, "config.py defines nothing"
    for name in names:
        assert not any(w in name for w in ("SECRET", "PASSWORD", "API_KEY", "TOKEN", "DATABASE_URL")), name


def test_model_id_matching() -> None:
    ids = ["meta/Llama-3.3-70B-Instruct", "openai/gpt-oss-120b", "Qwen3-8B"]
    assert pick_model("gpt oss 120B", ids) == "openai/gpt-oss-120b"
    assert pick_model("GPT-OSS-120b", ["gpt-oss-120b"]) == "gpt-oss-120b"
    assert pick_model("not-offered", ["Llama-3.3-70B-Instruct", "x"]) == "Llama-3.3-70B-Instruct"  # preference list
    assert pick_model("not-offered", ["something"]) == "something"
    assert pick_model("anything", []) is None
