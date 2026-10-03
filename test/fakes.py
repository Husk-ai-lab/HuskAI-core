"""Deterministic stand-ins for external services, used by the test suite and by the
benchmarks' offline mode. The scripted LLM exercises every code path of the harness
(structured outputs, tool loops, note validation retries) without network access.
It does NOT measure AI quality — benchmarks label offline results accordingly."""

from __future__ import annotations

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

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> ScriptedChatModel:  # type: ignore[override]
        names = [convert_to_openai_tool(t)["function"]["name"] for t in tools]
        return self.model_copy(update={"tool_names": names})

    def _generate(self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kw: Any) -> ChatResult:
        msg = self.handler(messages, self.tool_names)
        msg.usage_metadata = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}
        return ChatResult(generations=[ChatGeneration(message=msg)])


class FakeProvider:
    def __init__(self, handler: Handler | None = None):
        self.handler = handler or default_handler
        self.model_name = "scripted-test-model"
        self.calls = 0

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

    def chat_model(self, usage: Any = None, temperature: float | None = None) -> BaseChatModel:
        self.calls += 1
        return ScriptedChatModel(handler=self.handler)


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
    if tools == ["DocumentAnalysis"]:
        doc = re.search(r"DOCUMENT (doc_\w+) — (\S+)", text)
        name = doc.group(2) if doc else "documento"
        manual = len(re.findall(r"a mano|ricopi|ogni sera|ogni|manual", text, re.I))
        return _call("DocumentAnalysis", {
            "doc_type": "distinta base" if "distinta" in name.lower() else "procedura",
            "department": "Ufficio tecnico" if "distinta" in name.lower() else "Produzione",
            "summary": f"Documento {name} con attività operative ricorrenti.",
            "automation_signal": min(0.2 + 0.15 * manual, 0.95),
            "signal_reasons": ["ricopiatura manuale"] if manual else [],
            "facts": [{"text": f"L'azienda usa il file {name} nel lavoro quotidiano.", "category": "processo",
                       "importance": 0.6, "quote": name}],
        })
    if tools == ["HandoffMap"]:
        ids = re.findall(r"- (note_\w+) \|", text)
        links = [{"note_a": ids[i], "note_b": ids[i + 1], "reason": "stessi dati"} for i in range(len(ids) - 1)]
        return _call("HandoffMap", {"links": links[:5]})
    if tools == ["OpportunityMap"]:
        ids = list(dict.fromkeys(re.findall(r"`(note_\w+)`", text)))
        if not ids:
            return _call("OpportunityMap", {"opportunities": []})
        half = max(1, len(ids) // 2)
        opps = [{"title": "Confronto revisioni distinta base", "explanation": "La distinta viene ricopiata a mano nel gestionale ogni revisione.",
                 "note_ids": ids[:half], "hours_per_week": 6, "priority": "alta"}]
        if len(ids) > 1:
            opps.append({"title": "Report giornaliero di produzione", "explanation": "I dati di turno vengono incollati a mano ogni sera.",
                         "note_ids": ids[half:], "hours_per_week": 4, "priority": "media"})
        return _call("OpportunityMap", {"opportunities": opps})
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
    if tools == ["NotesExtraction"]:
        doc = re.search(r"DOCUMENT (doc_\w+)", text)
        return _call("NotesExtraction", {"notes": [{
            "title": "Ricopiatura manuale", "claim": "I dati vengono ricopiati a mano nel gestionale ogni giorno.",
            "frequency": "ogni giorno", "actors": ["Ufficio tecnico"], "evidence_document_id": doc.group(1) if doc else "x",
            "hours_per_week": 3}]})

    # --- explorer research loop -------------------------------------------------------
    if "write_note" in tools:
        doc = re.search(r"starting from document (doc_\w+)", text)
        net = re.search(r"notes space\): (net_\w+)", text)
        if not tool_msgs and doc and net:
            return _call("search_knowledge", {"query": "a mano gestionale", "k": 3})
        written = [m for m in tool_msgs if m.name == "write_note"]
        if doc and net and len(written) == 0:
            # first attempt is deliberately truncated: the validator must reject it
            return _call("write_note", {"network_id": net.group(1), "title": "Ricopiatura dati",
                                        "claim": "I dati della revisione vengono ricopiati", "frequency": "a ogni revisione",
                                        "evidence": [{"document_id": doc.group(1)}]})
        if doc and net and len(written) == 1 and "ERROR" in str(written[0].content):
            return _call("write_note", {"network_id": net.group(1), "title": "Ricopiatura dati nel gestionale",
                                        "claim": "A ogni nuova revisione i dati vengono ricopiati a mano nel gestionale dall'ufficio tecnico.",
                                        "frequency": "a ogni revisione", "actors": ["Ufficio tecnico"], "hours_per_week": 3,
                                        "evidence": [{"document_id": doc.group(1), "quote": "ricopiato nel gestionale"}]})
        return AIMessage(content="Ho trovato un'attività ripetitiva di ricopiatura.")

    # --- builder chat --------------------------------------------------------------------
    if "add_node" in tools:
        ask = _last_human(messages)
        done = [m for m in tool_msgs if m.name in ("add_node", "update_node")]
        if "Acquisti" in ask and not done:
            cond = re.search(r"'id': '(\w+)', 'label': '[^']*', [^\n]*'kind': 'condition'", text)
            return _call("add_node", {"node": {"kind": "action", "label": "Avvisa Acquisti", "action": "email", "target": "Acquisti"},
                                      "after": cond.group(1) if cond else "anomalie", "branch": "vero"})
        if "soglia" in ask and not done:
            return _call("update_node", {"node_id": "anomalie", "changes": {"value": 3}})
        return AIMessage(content="Fatto: ho aggiornato il flusso." if done else "Questa automazione usa la distinta base.")

    # --- plain text (summaries) -------------------------------------------------------------
    return AIMessage(content="Questa rete collega la distinta base al gestionale. [[nota]]")
