"""Deterministic stand-ins for external services, used by the test suite and by the
benchmarks' offline mode. The scripted LLM exercises every code path of the harness
(structured outputs, tool loops, note validation retries) without network access.
It does NOT measure AI quality — benchmarks label offline results accordingly."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool

Handler = Callable[[list[BaseMessage], list[str]], AIMessage]


class ScriptedChatModel(BaseChatModel):
    handler: Any = None
    tool_names: list[str] = []
    tool_chars: int = 0
    estimate: bool = False  # benchmarks: report tokens ≈ characters / 4 instead of a fixed count

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> ScriptedChatModel:  # type: ignore[override]
        specs = [convert_to_openai_tool(t) for t in tools]
        return self.model_copy(update={"tool_names": [s["function"]["name"] for s in specs],
                                       "tool_chars": len(json.dumps(specs))})

    def _generate(self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kw: Any) -> ChatResult:
        msg = self.handler(messages, self.tool_names)
        if self.estimate:
            tin = (sum(len(str(m.content)) for m in messages) + self.tool_chars) // 4
            tout = (len(str(msg.content)) + len(json.dumps(getattr(msg, "tool_calls", []) or []))) // 4
        else:
            tin, tout = 100, 20
        msg.usage_metadata = {"input_tokens": tin, "output_tokens": tout, "total_tokens": tin + tout}
        return ChatResult(generations=[ChatGeneration(message=msg)])


class FakeProvider:
    def __init__(self, handler: Handler | None = None, estimate: bool = False,
                 summary_model: str | None = "scripted-summary-model"):
        self.handler = handler or default_handler
        self.model_name = "scripted-test-model"
        self.summary_model_name = summary_model
        self.estimate = estimate
        self.calls = 0
        self.models_used: list[str] = []
        self.efforts: list[str | None] = []

    @property
    def configured(self) -> bool:
        return True

    def key_for(self, user_id: str | None) -> str | None:
        return "scripted-key"

    def configured_for(self, user_id: str) -> bool:
        return True

    async def resolve_model(self, api_key: str | None = None) -> str:
        return self.model_name

    async def list_models(self, api_key: str) -> list[str]:
        return [self.model_name]

    def chat_model(self, usage: Any = None, temperature: float | None = None, model: str | None = None,
                   reasoning: str | None = None, max_tokens: int | None = None,
                   timeout: float | None = None, max_retries: int | None = None) -> BaseChatModel:
        from app.core.metering import MeterCallback

        self.calls += 1
        model_id = model or self.model_name
        self.models_used.append(model_id)
        self.efforts.append(reasoning)
        return ScriptedChatModel(handler=self.handler, estimate=self.estimate, callbacks=[MeterCallback(model_id)])


def _call(name: str, args: dict[str, Any]) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call_{uuid.uuid4().hex[:8]}"}])


def _text(messages: list[BaseMessage]) -> str:
    return "\n".join(str(m.content) for m in messages if isinstance(m, (HumanMessage, SystemMessage)))


def _last_human(messages: list[BaseMessage]) -> str:
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            return str(m.content)
    return ""


def default_handler(messages: list[BaseMessage], tools: list[str]) -> AIMessage:
    text = _text(messages)
    tool_msgs = [m for m in messages if isinstance(m, ToolMessage)]

    # --- structured outputs (with_structured_output binds the schema as the only tool) ---
    if tools == ["CardDraft"]:
        doc = re.search(r"DOCUMENT (doc_\w+) — (\S+)", text)
        name = doc.group(2) if doc else "documento"
        chunks = re.findall(r"\[(doc_\w+#c\d+)\]", text)
        manual = len(re.findall(r"a mano|ricopi|ogni sera|ogni|manual", text, re.I))
        quotes = [{"chunk_id": chunks[0], "quote": "a mano"}] if (manual and chunks) else []
        quotes.append({"chunk_id": "doc_inventato#c9", "quote": "x"})  # invalid: must be dropped
        return _call("CardDraft", {
            "sentence": f"Documento {name} con attività operative ricorrenti.",
            "summary": "I dati vengono controllati e ricopiati a mano nel gestionale ogni giorno dall'ufficio tecnico.",
            "signals": {"rep": min(0.2 + 0.15 * manual, 0.95), "manual": bool(manual), "freq": "ogni giorno",
                        "dept": ["Produzione"], "vol": None, "sys": ["gestionale"]},
            "quotes": quotes,
            "facts": [{"text": f"L'azienda usa il file {name} nel lavoro quotidiano.", "category": "processo",
                       "importance": 0.6, "quote": name}],
        })
    if tools == ["Brief"]:
        alias = re.search(r"· (D\d+)", text)
        chunk = re.search(r"\[(doc_\w+#c\d+)\]", text)
        return _call("Brief", {"title": "Report giornaliero automatico", "prio": "media", "hrs": 3,
                               "prompt": "Ogni giorno alle 18 raccogli i dati del turno, controlla le anomalie e invia un report.",
                               "notes": [{"title": "Ricopiatura dati", "claim": "I dati vengono ricopiati a mano nel gestionale ogni giorno.",
                                          "freq": "ogni giorno", "dept": ["Produzione"], "hrs": 3,
                                          "doc": alias.group(1) if alias else "D01", "chunk": chunk.group(1) if chunk else None,
                                          "quote": "a mano"}]})
    if tools == ["FlowDraft"]:
        doc_ids = re.findall(r'"(doc_\w+)"', text)
        return _call("FlowDraft", {
            "title": "Confronto revisioni distinta base",
            "description": "Confronta ogni nuova revisione con la precedente e segnala anomalie.",
            "trigger": {"type": "schedule", "label": "Ogni giorno alle 18:00", "subtitle": "pianificato", "cron": "0 18 * * 1-5"},
            "steps": [
                {"ref": "analisi", "kind": "agent", "label": "Analisi differenze", "after": "trigger",
                 "instructions": "Confronta la revisione e conta le anomalie (fornitori o pesi mancanti).",
                 "attachments": [{"role": "modello", "label": "LLM locale", "subtitle": "modello"},
                                 {"role": "memoria", "label": "Revisioni precedenti", "subtitle": "memoria", "document_ids": doc_ids[:1]}]},
                {"ref": "anomalie", "kind": "condition", "label": "Anomalie trovate?", "after": "analisi",
                 "field": "anomalie", "op": ">", "value": 0},
                {"ref": "avvisa", "kind": "action", "label": "Avvisa Ufficio tecnico", "after": "anomalie", "branch": "vero",
                 "action": "email", "target": "Ufficio tecnico"},
                {"ref": "archivia", "kind": "action", "label": "Archivia report PDF", "after": "anomalie", "branch": "falso",
                 "action": "pdf_report"},
            ],
        })
    if tools == ["AgentNodeOutput"]:
        return _call("AgentNodeOutput", {"text": "Trovate 2 anomalie: 1 fornitore mancante, 1 peso mancante.",
                                         "values": {"anomalie": 2}})
    if tools == ["_Choice"]:
        opts = re.search(r"OPTIONS: (.*)", text)
        first = opts.group(1).split(",")[0].strip() if opts else "vero"
        return _call("_Choice", {"answer": first, "confidence": 0.9, "reason": "test"})
    if tools == ["SuggestionMap"]:
        chunks = re.findall(r"\[(doc_\w+#c\d+)\]", text)
        autos = re.findall(r"- (auto_\w+) ·", text)
        if not chunks:
            return _call("SuggestionMap", {"suggestions": []})
        sugg = [
            # invalid evidence (unknown passage) and low confidence: the engine must drop both
            {"kind": "new_automation", "title": "Inventata", "body": "x", "prompt": "x",
             "evidence": [{"chunk_id": "doc_nope#c9", "quote": "x"}], "confidence": 0.9},
            {"kind": "new_automation", "title": "Debole", "body": "x", "prompt": "x",
             "evidence": [{"chunk_id": chunks[0], "quote": "x"}], "confidence": 0.3},
        ]
        if autos:
            sugg.append({"kind": "modify_automation", "automation_id": autos[0], "title": "Avvisa anche gli Acquisti",
                         "body": "La nuova procedura chiede di avvisare gli Acquisti a ogni anomalia.",
                         "prompt": "Il documento nuovo chiede di avvisare anche gli Acquisti: aggiungi l'avviso.",
                         "evidence": [{"chunk_id": chunks[0], "quote": "avvisare gli Acquisti"}], "confidence": 0.85})
        return _call("SuggestionMap", {"suggestions": sugg})

    # --- builder chat --------------------------------------------------------------------
    if "add_node" in tools:
        ask = _last_human(messages)
        done = [m for m in tool_msgs if m.name in ("add_node", "update_node")]
        studio_done = [m for m in tool_msgs if m.name.startswith("studio_")]
        trace = re.search(r"trace ([0-9a-f]{32})", text)
        if "studio_explain_failure" in tools and "self-heal request" in ask and not studio_done:
            return _call("studio_explain_failure", {"run_id": trace.group(1) if trace else ""})
        if "Acquisti" in ask and not done:
            cond = re.search(r"'id': '(\w+)', 'label': '[^']*', [^\n]*'kind': 'condition'", text)
            return _call("add_node", {"node": {"kind": "action", "label": "Avvisa Acquisti", "action": "email", "target": "Acquisti"},
                                      "after": cond.group(1) if cond else "anomalie", "branch": "vero"})
        if "soglia" in ask and not done:
            return _call("update_node", {"node_id": "anomalie", "changes": {"value": 3}})
        return AIMessage(content="Fatto: ho aggiornato il flusso." if done else "Questa automazione usa la distinta base.")

    # --- plain text (summaries) -------------------------------------------------------------
    return AIMessage(content="Questa rete collega la distinta base al gestionale. [[nota]]")


class FakeDecisionModel:
    """Deterministic stand-in for the decision model (Jev / Serenity): word overlap between the documents'
    lines decides relatedness; scores and depth follow simple, documented rules. Metered like the real one."""

    model_name = "jev-fake"

    def __init__(self, configured: bool = True):
        self.configured = configured
        self.calls: list[dict] = []

    def key_for(self, user_id: str | None) -> str | None:
        return "fake-venice-key" if self.configured else None

    def configured_for(self, user_id: str) -> bool:
        return self.configured

    async def verify(self, key: str) -> None:
        return None

    async def ask(self, user_id: str, org_id: str, state: str, questions: dict):  # type: ignore[no-untyped-def]
        from app.core.metering import current_meters
        from app.gateways.decisions.base import Answer

        self.calls.append({"state": state, "questions": dict(questions)})
        for m in current_meters():
            m.add(self.model_name, len(state) // 4 + 40 * len(questions), 0)
        text: dict[str, str] = {}
        current = None
        for line in state.splitlines():
            head = re.match(r"^(D\d+) \| ", line)
            if head:
                current = head.group(1)
            if current:
                text[current] = text.get(current, "") + " " + line.lower()

        def words(a: str) -> set[str]:
            return {w for w in re.findall(r"[a-zà-ù]{5,}", text.get(a, ""))} - {"documento", "attività", "ricorrenti"}

        out: dict = {}
        for qid in questions:
            if qid.startswith("rel_"):
                _, a, b = qid.split("_")
                out[qid] = Answer(type="noul", p=0.9 if len(words(a) & words(b)) >= 2 else 0.2)
            elif qid.startswith("type_"):
                out[qid] = Answer(type="choice", choice="same_item", probabilities={"same_item": 0.8, "handoff": 0.2},
                                  confidence=0.7)
            elif qid == "repetitive":
                band = "8" if ("manual=yes" in state or "rows=" in state) else "2"
                out[qid] = Answer(type="score", score=float(band), probabilities={band: 1.0}, confidence=0.8)
            elif qid == "hours":
                out[qid] = Answer(type="score", score=3.0, probabilities={"3": 1.0}, confidence=0.7)
            elif qid.startswith("deeper_"):
                first = next(iter(sorted(q for q in questions if q.startswith("deeper_"))), None)
                out[qid] = Answer(type="noul", p=0.8 if qid == first else 0.2)
        return out
