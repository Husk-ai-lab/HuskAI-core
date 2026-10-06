"""husk-gateway client: Bearer + User-Agent, one renewal on 401, error mapping, rate gate, LLM provider,
decision adapter (`/v1/decide`), entitlement endpoint. No token or text is ever logged."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from types import SimpleNamespace

import httpx
import pytest
import respx

from app.core.metering import Meter, metered
from app.gateways.clerk.oidc import USER_AGENT, TokenSet
from app.gateways.clerk.tokens import AccessTokens, TokenStore
from app.gateways.decisions import questions as q
from app.gateways.decisions.base import DecisionUnavailable, Question
from app.gateways.gateway import client as client_mod
from app.gateways.gateway.client import GatewayClient, RateGate
from app.gateways.gateway.decisions import BATCH, STATE_MAX, GatewayDecisionModel, fit_state, to_wire
from app.gateways.gateway.errors import GatewayError, raise_if_gateway
from app.gateways.gateway.llm import GatewayProvider, LLMNotConfigured, UsageContext
from app.gateways.sqlite.repositories import UsageRepo
from app.helpers.settings import Settings
from test.conftest import ORG, USER

GW = "https://gw.test"


class FakeClerk:
    def __init__(self) -> None:
        self.refreshed = 0

    async def refresh(self, refresh_token: str) -> TokenSet:
        self.refreshed += 1
        return TokenSet(f"tok-{self.refreshed + 1}", "r-next", time.time() + 3600, {})


def _gateway(*, signed_in: bool = True) -> tuple[GatewayClient, TokenStore, FakeClerk]:
    store = TokenStore(use_keyring=False)
    if signed_in:
        store.set_access(USER, "tok-1", time.time() + 3600)
        store.save_refresh(USER, "r-1")
    clerk = FakeClerk()
    settings = Settings(HUSK_ENV="test", HUSK_GATEWAY_URL=GW, NEON_DATA_API_URL=None)
    return GatewayClient(settings, AccessTokens(store, clerk)), store, clerk  # type: ignore[arg-type]


# --- transport -------------------------------------------------------------------------------------
@respx.mock
async def test_requests_carry_the_bearer_token_and_a_user_agent() -> None:
    gw, _, _ = _gateway()
    route = respx.get(f"{GW}/v1/entitlement").mock(return_value=httpx.Response(200, json={"allowed": True, "status": "active", "org_id": "o"}))
    ent = await gw.entitlement(USER)
    assert (ent.allowed, ent.status) == (True, "active")
    req = route.calls.last.request
    assert req.headers["authorization"] == "Bearer tok-1" and req.headers["user-agent"] == USER_AGENT


@respx.mock
async def test_an_invalid_token_is_renewed_once() -> None:
    gw, _, clerk = _gateway()
    route = respx.get(f"{GW}/v1/entitlement").mock(side_effect=[
        httpx.Response(401, json={"error": {"message": "invalid token: ExpiredSignatureError", "code": "unauthorized"}}),
        httpx.Response(200, json={"allowed": True, "status": "active"}),
    ])
    assert (await gw.entitlement(USER)).allowed
    assert clerk.refreshed == 1 and route.call_count == 2
    assert [c.request.headers["authorization"] for c in route.calls] == ["Bearer tok-1", "Bearer tok-2"]


@respx.mock
async def test_no_organisation_is_not_retried_and_is_explained() -> None:
    gw, _, clerk = _gateway()
    route = respx.get(f"{GW}/v1/entitlement").mock(return_value=httpx.Response(401, json={"error": {"message": "organisation claim missing"}}))
    with pytest.raises(GatewayError) as e:
        await gw.entitlement(USER)
    assert e.value.key == "errors.gateway_no_org" and route.call_count == 1 and clerk.refreshed == 0


@respx.mock
@pytest.mark.parametrize(("status", "key"), [(403, "errors.gateway_not_entitled"), (429, "errors.gateway_busy"),
                                             (503, "errors.gateway_unavailable"), (400, "errors.gateway_rejected")])
async def test_gateway_answers_become_localized_errors(status: int, key: str) -> None:
    gw, _, _ = _gateway()
    respx.get(f"{GW}/v1/usage").mock(return_value=httpx.Response(status, json={"error": {"message": "x"}}))
    with pytest.raises(GatewayError) as e:
        await gw.usage(USER)
    assert e.value.key == key


@respx.mock
async def test_without_a_sign_in_the_session_is_expired_and_nothing_is_sent() -> None:
    gw, _, _ = _gateway(signed_in=False)
    route = respx.get(f"{GW}/v1/entitlement").mock(return_value=httpx.Response(200, json={}))
    assert not gw.signed_in(USER)
    with pytest.raises(GatewayError) as e:
        await gw.entitlement(USER)
    assert e.value.key == "errors.session_expired" and e.value.status == 401 and not route.called


@respx.mock
async def test_unreachable_gateway_is_a_clean_error() -> None:
    gw, _, _ = _gateway()
    respx.get(f"{GW}/v1/entitlement").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(GatewayError) as e:
        await gw.entitlement(USER)
    assert e.value.key == "errors.gateway_unavailable"


async def test_rate_gate_keeps_us_under_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [0.0]
    slept: list[float] = []

    async def fake_sleep(s: float) -> None:
        slept.append(s)
        clock[0] += s

    monkeypatch.setattr(client_mod, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(client_mod, "asyncio", SimpleNamespace(Lock=asyncio.Lock, sleep=fake_sleep))
    gate = RateGate(per_minute=3)
    for _ in range(3):
        await gate.acquire()
    assert slept == []
    await gate.acquire()  # the 4th request in the same minute waits for the window to move
    assert slept and sum(slept) >= 59.9


# --- LLM provider ------------------------------------------------------------------------------------
COMPLETION = {"id": "c1", "object": "chat.completion", "created": 1, "model": "gpt-oss-120b",
              "choices": [{"index": 0, "message": {"role": "assistant", "content": "ciao"}, "finish_reason": "stop"}],
              "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}}


@respx.mock
async def test_chat_goes_through_the_gateway_with_the_users_token(services) -> None:  # type: ignore[no-untyped-def]
    gw, _, _ = _gateway()
    route = respx.post(f"{GW}/v1/chat/completions").mock(return_value=httpx.Response(200, json=COMPLETION))
    provider = GatewayProvider(gw.settings, UsageRepo(services.db), gw)
    assert provider.configured_for(USER) and provider.model_name == "gpt-oss-120b"
    assert provider.summary_model_name == "gpt-oss-20b"
    meter = Meter()
    with metered(meter):  # the callback captures the meters current when the model is created
        model = provider.chat_model(UsageContext(ORG, USER), reasoning="low", max_tokens=50)
        msg = await model.ainvoke("ciao")
    assert msg.content == "ciao"
    req = route.calls.last.request
    body = json.loads(req.content)
    assert req.headers["authorization"] == "Bearer tok-1" and req.headers["user-agent"] == USER_AGENT
    assert body["model"] == "gpt-oss-120b" and body["reasoning_effort"] == "low"
    assert 50 in (body.get("max_tokens"), body.get("max_completion_tokens"))  # either spelling: the gateway accepts both
    assert meter.as_dict()["by_model"]["gpt-oss-120b"]["tokens_in"] == 7
    assert UsageRepo(services.db).summary(ORG, USER)[0]["tokens_out"] == 3  # recorded locally too


@respx.mock
async def test_not_entitled_stops_at_once_with_a_localized_error(services, caplog) -> None:  # type: ignore[no-untyped-def]
    gw, _, _ = _gateway()
    route = respx.post(f"{GW}/v1/chat/completions").mock(
        return_value=httpx.Response(403, json={"error": {"message": "organisation not entitled (status: none)", "code": "forbidden"}}))
    caplog.set_level(logging.DEBUG)
    model = GatewayProvider(gw.settings, UsageRepo(services.db), gw).chat_model(UsageContext(ORG, USER))
    with pytest.raises(Exception) as e:
        await model.ainvoke("hello")
    with pytest.raises(GatewayError) as mapped:
        raise_if_gateway(e.value)
    assert mapped.value.key == "errors.gateway_not_entitled" and route.call_count == 1  # 4xx is not retried
    assert "tok-1" not in caplog.text and "hello" not in caplog.text


async def test_no_sign_in_no_model(services) -> None:  # type: ignore[no-untyped-def]
    gw, _, _ = _gateway(signed_in=False)
    provider = GatewayProvider(gw.settings, UsageRepo(services.db), gw)
    assert not provider.configured_for(USER)
    with pytest.raises(LLMNotConfigured):
        provider.chat_model(UsageContext(ORG, USER))
    with pytest.raises(LLMNotConfigured):
        provider.chat_model(None)


# --- decision adapter ---------------------------------------------------------------------------------
def test_yes_no_question_becomes_a_two_option_choice() -> None:
    wire, decode = to_wire("rel_D01_D02", q.doc_related("D01", "D02", "en"))
    assert wire["type"] == "choice" and wire["options"] == ["related", "not related"] and wire["id"] == "rel_D01_D02"
    a = decode({"answer": "related", "p": 0.8, "probabilities": {"related": 0.8, "not related": 0.2}})
    assert a.type == "noul" and a.p == pytest.approx(0.8)
    flipped = decode({"answer": "not related", "p": 0.7, "probabilities": {"related": 0.3, "not related": 0.7}})
    assert flipped.p == pytest.approx(0.3)
    assert decode({"answer": "related", "p": None, "probabilities": None}).p is None  # generative fallback: uncertain = no


def test_choice_maps_descriptions_back_to_keys() -> None:
    wire, decode = to_wire("t", q.relation_type("D01", "D02", "en"))
    assert wire["options"][0] == "Data handoff between departments"
    a = decode({"answer": "Revision or replacement", "p": 0.6, "probabilities": {"Revision or replacement": 0.6, "Data handoff between departments": 0.4}})
    assert a.choice == "revision" and a.probabilities["handoff"] == pytest.approx(0.4) and a.confidence == pytest.approx(0.6)


def test_ten_band_score_uses_the_gateway_score_type() -> None:
    wire, decode = to_wire("repetitive", q.repetitiveness("en"))
    assert wire == {"id": "repetitive", "type": "score", "text": wire["text"]}
    probs = [0.0] * 10
    probs[8] = 1.0
    a = decode({"answer": 0.85, "probabilities": probs})
    assert q.score_value(a.probabilities) == 0.85  # the Explorer's maths read "0".."9"


def test_eight_hour_buckets_become_a_choice_keyed_by_level() -> None:
    qn = q.hours_wasted("en")
    wire, decode = to_wire("hours", qn)
    assert wire["type"] == "choice" and len(wire["options"]) == 8
    a = decode({"answer": wire["options"][3], "p": 1.0, "probabilities": {wire["options"][3]: 1.0}})
    assert a.probabilities["3"] == 1.0
    hours, _ = q.expected_hours_per_week(a.probabilities)
    assert hours == round(7.5 / 4.33, 1)


def test_state_is_trimmed_to_the_gateway_limit() -> None:
    state = "\n".join(f"D{i:02d} | Documento {i}\n   " + ("riassunto " * 120) for i in range(40))
    assert len(state) > STATE_MAX
    fitted = fit_state(state)
    assert len(fitted) <= STATE_MAX and "D39 | Documento 39" in fitted  # the aliases and titles survive
    assert fit_state("short") == "short" and fit_state("") == "-"


@respx.mock
async def test_decide_batches_by_16_and_decodes(services) -> None:  # type: ignore[no-untyped-def]
    gw, _, _ = _gateway()
    sent: list[dict] = []

    def reply(request):  # type: ignore[no-untyped-def]
        body = json.loads(request.content)
        sent.append(body)
        out = {w["id"]: {"answer": w["options"][0], "p": 0.9, "probabilities": {w["options"][0]: 0.9, w["options"][1]: 0.1}}
               for w in body["questions"]}
        return httpx.Response(200, json=out, headers={"X-Decision-Model": gw.settings.decision_model or ""})

    respx.post(f"{GW}/v1/decide").mock(side_effect=reply)
    model = GatewayDecisionModel(gw.settings, gw, UsageRepo(services.db))
    qs = {f"rel_{i}": q.doc_related("D01", "D02", "en") for i in range(BATCH + 4)}
    meter = Meter()
    with metered(meter):
        ans = await model.ask(USER, ORG, "STATE", qs, lang="en")
    assert len(ans) == BATCH + 4 and ans["rel_0"].p == pytest.approx(0.9)
    assert sorted(len(b["questions"]) for b in sent) == [4, BATCH]
    assert sent[0]["state"] == "STATE" and sent[0]["lang"] == "en" and sent[0]["model"] == gw.settings.decision_model
    assert meter.as_dict()["by_model"][gw.settings.decision_model]["calls"] == 2


@respx.mock
async def test_decide_errors_are_localized(services) -> None:  # type: ignore[no-untyped-def]
    gw, _, _ = _gateway()
    model = GatewayDecisionModel(gw.settings, gw, None)
    qs = {"x": Question(type="noul", instructions="?")}
    respx.post(f"{GW}/v1/decide").mock(return_value=httpx.Response(403, json={"error": {"message": "organisation not entitled"}}))
    with pytest.raises(DecisionUnavailable) as e:
        await model.ask(USER, ORG, "s", qs)
    assert e.value.key == "errors.gateway_not_entitled"
    respx.post(f"{GW}/v1/decide").mock(return_value=httpx.Response(502, json={"error": {"message": "upstream_error"}}))
    with pytest.raises(DecisionUnavailable) as e:
        await model.ask(USER, ORG, "s", qs)
    assert e.value.key == "errors.gateway_unavailable"
    offline = GatewayDecisionModel(_gateway(signed_in=False)[0].settings, _gateway(signed_in=False)[0], None)
    with pytest.raises(DecisionUnavailable) as e:
        await offline.ask(USER, ORG, "s", qs)
    assert e.value.key == "errors.session_expired"


# --- entitlement endpoint ------------------------------------------------------------------------------
def test_entitlement_is_cached_for_offline_and_unknown_never_blocks(client, services, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.gateways.gateway.client import Entitlement

    async def paid(user_id: str) -> Entitlement:
        return Entitlement(True, "active", ORG)

    monkeypatch.setattr(services.gateway, "entitlement", paid)
    assert client.get("/api/v1/user/entitlement").json() == {"allowed": True, "status": "active", "source": "gateway"}

    async def down(user_id: str) -> Entitlement:
        raise GatewayError("errors.gateway_unavailable", status=503)

    monkeypatch.setattr(services.gateway, "entitlement", down)
    assert client.get("/api/v1/user/entitlement").json() == {"allowed": True, "status": "active", "source": "cache"}
    services.db.kv_set(f"entitlement:{ORG}", None)  # nothing known yet
    r = client.get("/api/v1/user/entitlement").json()
    assert r["allowed"] is None and r["source"] == "unknown"


def test_not_paid_is_reported_and_expired_sessions_end(client, services, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.gateways.gateway.client import Entitlement

    async def unpaid(user_id: str) -> Entitlement:
        return Entitlement(False, "none", ORG)

    monkeypatch.setattr(services.gateway, "entitlement", unpaid)
    assert client.get("/api/v1/user/entitlement").json()["allowed"] is False

    async def gone(user_id: str) -> Entitlement:
        raise GatewayError("errors.session_expired", status=401)

    monkeypatch.setattr(services.gateway, "entitlement", gone)
    assert client.get("/api/v1/user/entitlement").status_code == 401
    assert client.get("/api/v1/user/me").status_code == 401  # the local session ended with it


@respx.mock
async def test_a_refused_request_logs_the_gateways_reason_but_not_the_input(caplog) -> None:  # type: ignore[no-untyped-def]
    gw, _, _ = _gateway()
    respx.post(f"{GW}/v1/decide").mock(return_value=httpx.Response(
        400, json={"error": {"message": "unknown decision model", "code": "invalid_request"}}))
    caplog.set_level(logging.WARNING)
    with pytest.raises(GatewayError):
        await gw._call("POST", "/v1/decide", USER, json={"state": "TESTO-RISERVATO", "questions": []})
    assert "400 unknown decision model" in caplog.text
    assert "TESTO-RISERVATO" not in caplog.text and "tok-1" not in caplog.text


def test_question_wording_respects_the_gateway_limit() -> None:
    long = Question(type="score", instructions="x" * 900, criteria=[str(i) for i in range(10)])
    assert len(to_wire("a", long)[0]["text"]) == 500
