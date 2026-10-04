"""AI key after login (simplified BYOK): verified with Regolo before saving, stored per user in
local data (user_ai_keys), never returned by the API, never exported in backups."""

from __future__ import annotations

import sqlite3

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.endpoints.deps import SESSION_COOKIE
from app.gateways.regolo.key_store import UserAiKeyStore, mask
from app.gateways.regolo.provider import LLMNotConfigured, RegoloProvider, UsageContext
from app.gateways.sqlite.repositories import UsageRepo
from app.main import create_app
from test.conftest import ORG, USER, login

MODELS = "https://api.regolo.ai/v1/models"
KEY = "rg-test-key-1234567890-abcd"
COMBINED = KEY + "VENICE_INFERENCE_KEY_testpart_0001"  # one string after login: Regolo key + Venice key
OK = httpx.Response(200, json={"data": [{"id": "Llama-3.3-70B-Instruct"}]})


@pytest.fixture
def real(settings, services):  # type: ignore[no-untyped-def]
    services.llm = RegoloProvider(settings, UsageRepo(services.db), services.ai_keys)
    with TestClient(create_app(settings, services), base_url="http://127.0.0.1:8765") as c:
        c.cookies.set(SESSION_COOKIE, login(services))
        yield c, services


def test_no_key_blocks_ai_but_not_local_work(real) -> None:  # type: ignore[no-untyped-def]
    c, _ = real
    assert c.get("/api/v1/user/ai-key").json()["configured"] is False
    r = c.post("/api/v1/discovery/runs")
    assert r.status_code == 503 and r.json()["code"] == "errors.ai_key_missing"
    assert c.get("/api/v1/documents").status_code == 200


@respx.mock
def test_valid_key_is_verified_then_saved_in_user_data(real) -> None:  # type: ignore[no-untyped-def]
    c, services = real
    route = respx.get(MODELS).mock(return_value=OK)
    r = c.put("/api/v1/user/ai-key", json={"api_key": f"  {COMBINED}  "})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["configured"] and body["source"] == "user" and body["hint"] == "••••abcd" and body["verified_at"]
    assert KEY not in r.text and KEY not in c.get("/api/v1/user/ai-key").text  # never echoed back
    assert route.calls.last.request.headers["authorization"] == f"Bearer {KEY}"
    with sqlite3.connect(services.paths.db) as con:  # stored in the user's local data
        row = con.execute("SELECT user_id, org_id, api_key, key_hint, model FROM user_ai_keys").fetchone()
    assert row == (USER, ORG, KEY, "••••abcd", "Llama-3.3-70B-Instruct")
    assert services.llm.key_for(USER) == KEY


@respx.mock
def test_rejected_key_is_not_saved(real) -> None:  # type: ignore[no-untyped-def]
    c, services = real
    respx.get(MODELS).mock(return_value=httpx.Response(401))
    r = c.put("/api/v1/user/ai-key", json={"api_key": COMBINED})
    assert r.status_code == 400 and r.json()["code"] == "errors.ai_key_rejected"
    assert services.llm.keys.get(USER) is None
    assert c.get("/api/v1/user/ai-key").json()["configured"] is False  # the prompt stays


@respx.mock
def test_unverifiable_key_is_not_saved(real) -> None:  # type: ignore[no-untyped-def]
    c, services = real
    respx.get(MODELS).mock(side_effect=httpx.ConnectError("offline"))
    r = c.put("/api/v1/user/ai-key", json={"api_key": COMBINED})
    assert r.status_code == 503 and r.json()["code"] == "errors.ai_key_unverifiable"
    assert services.llm.keys.get(USER) is None


@respx.mock
def test_key_without_models_is_refused(real) -> None:  # type: ignore[no-untyped-def]
    c, services = real
    respx.get(MODELS).mock(return_value=httpx.Response(200, json={"data": []}))
    assert c.put("/api/v1/user/ai-key", json={"api_key": COMBINED}).status_code == 400
    assert services.llm.keys.get(USER) is None


def test_input_validation_and_delete(real) -> None:  # type: ignore[no-untyped-def]
    c, services = real
    assert c.put("/api/v1/user/ai-key", json={"api_key": "short"}).status_code == 422
    assert c.put("/api/v1/user/ai-key", json={"api_key": "has space inside key"}).status_code == 400
    services.llm.keys.set(USER, ORG, KEY, "m")
    assert c.delete("/api/v1/user/ai-key").json()["configured"] is False


def test_keys_are_per_user(settings, services) -> None:  # type: ignore[no-untyped-def]
    llm = RegoloProvider(settings, UsageRepo(services.db), UserAiKeyStore(services.db))
    llm.model_name = "m"
    login(services, "user_a", "org_a")
    login(services, "user_b", "org_a")
    llm.keys.set("user_a", "org_a", "key-for-user-a-0001", "m")
    assert llm.key_for("user_a") == "key-for-user-a-0001" and llm.key_for("user_b") is None
    model = llm.chat_model(UsageContext("org_a", "user_a"))
    assert model.openai_api_key.get_secret_value() == "key-for-user-a-0001"  # type: ignore[attr-defined]
    with pytest.raises(LLMNotConfigured):
        llm.chat_model(UsageContext("org_a", "user_b"))


@respx.mock
def test_key_never_exported_but_survives_import(real) -> None:  # type: ignore[no-untyped-def]
    c, services = real
    respx.get(MODELS).mock(return_value=OK)
    c.put("/api/v1/user/ai-key", json={"api_key": COMBINED})
    data = c.get("/api/v1/user/data/export").content
    assert KEY.encode() not in data  # stripped from the backup file
    r = c.post("/api/v1/user/data/import", files={"file": ("b.db", data)}, data={"confirm": "true"})
    assert r.status_code == 200, r.text
    assert services.llm.key_for(USER) == KEY  # the importing user keeps their own key


def test_env_key_is_a_developer_fallback(settings, services) -> None:  # type: ignore[no-untyped-def]
    settings.regolo_api_key = "env-dev-key-9999"
    llm = RegoloProvider(settings, UsageRepo(services.db), UserAiKeyStore(services.db))
    login(services)
    assert llm.key_for(USER) == "env-dev-key-9999"
    llm.keys.set(USER, ORG, "user-key-for-me-1111", None)
    assert llm.key_for(USER) == "user-key-for-me-1111"  # the verified user key wins


def test_mask() -> None:
    assert mask("abcdefgh1234") == "••••1234" and mask("abc") == "••••"


def test_sign_out_removes_only_the_signing_out_users_key(real) -> None:  # type: ignore[no-untyped-def]
    c, services = real
    services.ai_keys.set(USER, ORG, KEY, "m")
    login(services, "user_other", ORG)
    services.ai_keys.set("user_other", ORG, "key-of-another-user-0001", "m")
    assert c.post("/api/v1/user/auth/logout").status_code == 200
    assert services.ai_keys.get(USER) is None  # entered again at the next sign-in
    assert services.ai_keys.get("user_other") is not None
    c.cookies.set(SESSION_COOKIE, login(services))
    assert c.get("/api/v1/user/ai-key").json()["configured"] is False


@respx.mock
def test_changing_to_a_rejected_key_keeps_the_old_one(real) -> None:  # type: ignore[no-untyped-def]
    c, services = real
    services.ai_keys.set(USER, ORG, KEY, "m")
    respx.get(MODELS).mock(return_value=httpx.Response(401))
    r = c.put("/api/v1/user/ai-key", json={"api_key": "rg-wrong-key-000000"})
    assert r.status_code == 400
    assert services.ai_keys.get(USER).api_key == KEY
