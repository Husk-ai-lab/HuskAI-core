import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, ArrowUp, Check, FileText, Lightbulb, Paperclip, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, streamSSE } from "../api/client";
import type { Automation, ChatMessage, ChatView, Personality, PersonalityId, Proposal, Suggestion } from "../api/types";
import { useT } from "../i18n";
import { useDocuments } from "../lib/hooks";
import { Logo } from "./Logo";
import { Markdown } from "./Markdown";
import { DiagnoseButton, StudioAction } from "./StudioAction";

function describeOp(op: Record<string, any>, t: (k: string) => string): string {
  const notif = op.node?.kind === "notification" ? ` · ${t(`notification.channel_${op.node.channel ?? "desktop"}`)}${op.node.recipients ? ` · ${op.node.recipients}` : ""}` : "";
  const what = `${op.node?.label ?? op.node_id ?? (op.source ? `${op.source} → ${op.target}` : op.label ?? op.cron ?? "")}${notif}`;
  const extra = op.changes ? ` ${JSON.stringify(op.changes)}` : "";
  const key = `chat.op.${op.op}`;
  const label = t(key);
  return `${label === key ? op.op : label}: ${what}${extra}`;
}

function DocChips({ ids, name }: { ids: string[]; name: (id: string) => string }) {
  if (!ids.length) return null;
  return <div className="doc-chips">{ids.slice(0, 3).map((id) => <span key={id} className="doc-chip"><FileText size={11} /> {name(id)}</span>)}</div>;
}

/** A request Husk prepared (not typed by the user): sent marked as such only if left unchanged. */
export type Prefill =
  | { kind: "suggestion"; prompt: string; suggestion: Suggestion }
  | { kind: "self_heal"; prompt: string; traceId: string };

interface ChatPanelProps {
  automation: Automation;
  selectedNodeId: string | null;
  /** Opened from the Suggestions page: the composer is pre-filled with Husk's request, marked as such. */
  suggestion?: Suggestion | null;
  onSuggestionDone?: () => void;
  /** "Diagnose with Husk Studio" from outside the chat (e.g. the failed test drawer). */
  selfHeal?: { traceId: string; prompt: string; nonce: number } | null;
}

export function ChatPanel({ automation, selectedNodeId, suggestion = null, onSuggestionDone, selfHeal = null }: ChatPanelProps) {
  const qc = useQueryClient();
  const t = useT();
  const key = ["chat", automation.id];
  const { data } = useQuery({ queryKey: key, queryFn: () => api.get<ChatView>(`/api/v1/agent/chats/automation/${automation.id}`) });
  const { data: personalities } = useQuery({ queryKey: ["personalities"], queryFn: () => api.get<Personality[]>("/api/v1/agent/personalities") });
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [trace, setTrace] = useState<string | null>(null);
  const body = useRef<HTMLDivElement>(null);
  const [pending, setPending] = useState<Prefill | null>(null);
  const { data: docs } = useDocuments();
  const docName = (id: string) => docs?.find((d) => d.id === id)?.filename ?? id;

  const prefill = (p: Prefill) => { setPending(p); setText(p.prompt); };
  useEffect(() => {
    if (suggestion?.prompt) prefill({ kind: "suggestion", prompt: suggestion.prompt, suggestion });
  }, [suggestion]);
  useEffect(() => {
    if (selfHeal) prefill({ kind: "self_heal", prompt: selfHeal.prompt, traceId: selfHeal.traceId });
  }, [selfHeal]);
  const edited = !!pending && text.trim() !== pending.prompt.trim();
  const dropPending = () => {
    if (pending?.kind === "suggestion") onSuggestionDone?.();
    setPending(null);
    setText("");
  };

  useEffect(() => {
    if (data) {
      setMessages(data.messages);
      setProposals(data.proposals);
    }
  }, [data]);
  useEffect(() => {
    body.current?.scrollTo({ top: body.current.scrollHeight, behavior: "smooth" });
  }, [messages, proposals, trace]);

  const selected = automation.graph.nodes.find((n) => n.id === selectedNodeId);
  const personality = data?.chat.personality ?? "bilanciato";

  const setPersonality = async (p: PersonalityId) => {
    if (!data) return;
    await api.patch(`/api/v1/agent/chats/${data.chat.id}`, { personality: p });
    qc.invalidateQueries({ queryKey: key });
  };

  const send = async (content: string) => {
    if (!data || !content.trim() || busy) return;
    const from = pending;
    const unchanged = !!from && content.trim() === from.prompt.trim();
    setBusy(true);
    setText("");
    if (from) {
      setPending(null);
      if (from.kind === "suggestion") onSuggestionDone?.();
    }
    setTrace(t("chat.status_working"));
    try {
      const payload = {
        content, selected_node_id: selectedNodeId,
        suggestion_id: from?.kind === "suggestion" ? from.suggestion.id : null,
        // a self-heal request is marked only when sent as prepared; edited text is the user's own request
        self_heal: from?.kind === "self_heal" && unchanged ? { trace_id: from.traceId } : null,
      };
      await streamSSE(`/api/v1/agent/chats/${data.chat.id}/messages`, payload, (ev, d) => {
        if (ev === "user" || ev === "message" || ev === "error") setMessages((m) => [...m, d as ChatMessage]);
        else if (ev === "status") setTrace(d.key ? t(d.key) : d.text);
        else if (ev === "tool") setTrace(`→ ${d.name}`);
        else if (ev === "proposal") setProposals((p) => [...p, d as Proposal]);
        else if (ev === "patch") {
          qc.setQueryData(["automation", automation.id], (old: any) => (old ? { ...old, automation: d.automation } : old));
          qc.invalidateQueries({ queryKey: ["automations"] });
        }
      });
    } catch (e) {
      setMessages((m) => [...m, { id: crypto.randomUUID(), chat_id: data.chat.id, role: "assistant", content: (e as Error).message, meta: { error: true }, created_at: "" }]);
    } finally {
      setBusy(false);
      setTrace(null);
    }
  };

  const decide = async (p: Proposal, accept: boolean) => {
    const r = await api.post<{ automation: Automation | null }>(`/api/v1/agent/proposals/${p.id}`, { accept });
    setProposals((all) => all.filter((x) => x.id !== p.id));
    if (r.automation) {
      qc.setQueryData(["automation", automation.id], (old: any) => (old ? { ...old, automation: r.automation } : old));
      qc.invalidateQueries({ queryKey: ["automations"] });
    }
  };

  return (
    <aside className="chat">
      <div className="chat-head">
        <Logo size={20} />
        <h3>{t("chat.title")}</h3>
        <select value={personality} onChange={(e) => setPersonality(e.target.value as PersonalityId)} title={t("chat.personality_title")} aria-label={t("chat.personality")}>
          {personalities?.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
        </select>
      </div>
      <div className="chat-body" ref={body}>
        {messages.map((m) => m.meta?.origin === "suggestion" ? (
          <div key={m.id} className="msg suggestion-msg">
            <div className="who"><Lightbulb size={12} /> {t("chat.suggestion_label")}</div>
            <p>{m.content}</p>
            <DocChips ids={(m.meta.document_ids as string[] | undefined) ?? []} name={docName} />
          </div>
        ) : m.meta?.origin === "self_heal" ? (
          <div key={m.id} className="msg suggestion-msg studio-msg">
            <div className="who"><Activity size={12} /> {t("chat.self_heal_label")}</div>
            <p>{m.content}</p>
          </div>
        ) : (
          <div key={m.id} className={`msg ${m.role === "user" ? "user" : ""} ${m.meta?.error ? "err" : ""}`}>
            <div className="who">Husk</div>
            {m.role === "assistant" && !m.meta?.error ? <Markdown text={m.content} variant="chat" /> : <p>{m.content}</p>}
            {Boolean(m.meta?.error) && (
              <div className="err-actions">
                <StudioAction traceId={m.meta.trace_id as string | undefined} traced={m.meta.traced as boolean | undefined} />
                {typeof m.meta.trace_id === "string" && (
                  <DiagnoseButton onClick={() => prefill({ kind: "self_heal", traceId: m.meta.trace_id as string, prompt: t("chat.self_heal_prompt_chat") })} />
                )}
              </div>
            )}
          </div>
        ))}
        {proposals.map((p) => (
          <div key={p.id} className="proposal">
            {p.suggestion_id && <div className="from-suggestion"><Lightbulb size={11} /> {t("chat.proposal_from_suggestion")}</div>}
            {p.origin === "self_heal" && <div className="from-suggestion"><Activity size={11} /> {t("chat.proposal_from_studio")}</div>}
            <b>{t("chat.proposal_title")}</b>
            <ul>{p.operations.map((o, i) => <li key={i}>{describeOp(o, t)}</li>)}</ul>
            <div style={{ display: "flex", gap: 8 }}>
              <button className="btn sm primary" onClick={() => decide(p, true)}><Check size={13} /> {t("chat.apply")}</button>
              <button className="btn sm" onClick={() => decide(p, false)}><X size={13} /> {t("chat.discard")}</button>
            </div>
          </div>
        ))}
        {trace && <div className="tool-trace">{trace}</div>}
      </div>
      <div className="chat-foot">
        {!busy && !pending && data?.suggestions && (
          <div className="chips-col">
            {data.suggestions.map((s) => <button key={s} className="suggestion" onClick={() => send(s)}>{s}</button>)}
          </div>
        )}
        <div className={`composer ${pending && !edited ? "suggesting" : ""}`}>
          {pending && (
            <div className="suggestion-frame">
              <div className="head">
                {pending.kind === "suggestion" ? <Lightbulb size={13} /> : <Activity size={13} />}
                <b>{t(pending.kind === "suggestion" ? "chat.suggestion_label" : "chat.self_heal_label")}</b>
                <span className="spacer" />
                <button className="icon-btn" style={{ width: 22, height: 22 }} onClick={dropPending} aria-label={t("common.cancel")}><X size={11} /></button>
              </div>
              {pending.kind === "suggestion" && <DocChips ids={pending.suggestion.document_ids} name={docName} />}
              <div className="note">{t(edited ? "chat.suggestion_edited" : pending.kind === "suggestion" ? "chat.suggestion_note" : "chat.self_heal_note")}</div>
            </div>
          )}
          <textarea
            placeholder={selected ? t("chat.placeholder_node", { label: selected.label }) : t("chat.placeholder")}
            value={text} rows={pending ? 4 : 2} disabled={busy}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(text); } }}
          />
          <div className="bar2">
            <Paperclip size={14} className="muted" />
            <span className="sel">{selected ? t("chat.selected", { label: selected.label }) : ""}</span>
            <button className="send" onClick={() => send(text)} disabled={busy || !text.trim()} aria-label={t(pending && !edited ? (pending.kind === "suggestion" ? "chat.suggestion_send" : "chat.self_heal_send") : "chat.send")}><ArrowUp size={15} /></button>
          </div>
        </div>
      </div>
    </aside>
  );
}
