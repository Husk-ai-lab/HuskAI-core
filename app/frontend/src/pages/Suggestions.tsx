import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, BookOpen, FileX, Lightbulb, Plus, Search, Wrench, X } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { MemoryView, Suggestion, SuggestionKind } from "../api/types";
import { Markdown } from "../components/Markdown";
import { TopBar } from "../components/TopBar";
import { useI18n } from "../i18n";
import { fmtDateTime, hoursValue } from "../lib/format";
import { useAutomations, useDocuments, useSuggestions, useSuggestionSummary } from "../lib/hooks";
import { useNotify } from "../lib/notifications";

const FILTERS = ["open", "applied", "dismissed", "all"] as const;
type Filter = (typeof FILTERS)[number];

const KIND_ICON: Record<SuggestionKind, ReactNode> = {
  modify_automation: <Wrench size={18} />,
  new_automation: <Plus size={18} />,
  memory_update: <BookOpen size={18} />,
  source_deleted: <FileX size={18} />,
  run_discovery: <Search size={18} />,
};

export const suggestionEditorPath = (s: Suggestion) => `/automazioni/${s.automation_id}?suggestion=${s.id}`;

function ProfileModal({ onClose }: { onClose: () => void }) {
  const { t, lang } = useI18n();
  const { data } = useQuery({ queryKey: ["memory", lang], queryFn: () => api.get<MemoryView>("/api/v1/discovery/memory") });
  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal card" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal>
        <div style={{ display: "flex", alignItems: "center" }}>
          <h2 style={{ flex: 1 }}>{t("suggestions.memory_profile_title")}</h2>
          <button className="icon-btn" onClick={onClose} aria-label={t("common.close")}><X size={15} /></button>
        </div>
        {data ? <Markdown text={data.company_profile_markdown} /> : <div className="spinner" />}
      </div>
    </div>
  );
}

function SuggestionCard({ s, highlight }: { s: Suggestion; highlight: boolean }) {
  const { t, lang } = useI18n();
  const nav = useNavigate();
  const qc = useQueryClient();
  const notify = useNotify();
  const { data: docs } = useDocuments();
  const { data: autos } = useAutomations();
  const [busy, setBusy] = useState(false);
  const [profile, setProfile] = useState(false);
  const docName = (id: string) => docs?.find((d) => d.id === id)?.filename ?? id;
  const automation = autos?.find((a) => a.id === s.automation_id);
  const open = s.status === "new" || s.status === "seen";

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["suggestions"] });
    qc.invalidateQueries({ queryKey: ["suggestions-summary"] });
  };
  const call = async (path: string) => {
    setBusy(true);
    try {
      const r = await api.post<{ automation_id?: string | null }>(path);
      refresh();
      return r;
    } catch (e) {
      notify((e as Error).message);
      return null;
    } finally {
      setBusy(false);
    }
  };
  const createDraft = async () => {
    const r = await call(`/api/v1/suggestions/${s.id}/apply`);
    if (r?.automation_id) {
      qc.invalidateQueries({ queryKey: ["automations"] });
      nav(`/automazioni/${r.automation_id}`);
    }
  };

  return (
    <div className={`card sugg-card ${highlight ? "fresh" : ""} ${open ? "" : "closed"}`}>
      <span className="icon-tile">{KIND_ICON[s.kind]}</span>
      <div style={{ minWidth: 0 }}>
        <div className="row-title">
          <span className="chip accent">{t(`suggestions.kind.${s.kind}`)}</span>
          {!open && <span className="chip">{t(`suggestions.status.${s.status}`)}</span>}
          {highlight && <span className="new-dot" aria-label={t("suggestions.status.new")} />}
        </div>
        <h3>{s.title}</h3>
        {s.body && <p className="row-desc pre">{s.body}</p>}
        {s.evidence.slice(0, 2).map((e, i) => (
          <blockquote key={i} className="quote">«{e.quote}» <span>{docName(e.document_id)}</span></blockquote>
        ))}
        <div className="row-meta">
          {automation && <Link to={`/automazioni/${automation.id}`}>{t("suggestions.automation", { title: automation.title })}</Link>}
          {s.document_ids.length > 0 && s.kind !== "run_discovery" && s.kind !== "source_deleted" && (
            <span>{t("suggestions.sources", { documents: s.document_ids.slice(0, 3).map(docName).join(", ") })}</span>
          )}
          {s.hours_per_week ? <span>{t("suggestions.hours", { hours: hoursValue(s.hours_per_week) })}</span> : null}
          {s.confidence < 1 && <span>{t("suggestions.confidence", { pct: Math.round(s.confidence * 100) })}</span>}
          <span>{fmtDateTime(s.created_at, lang)}</span>
        </div>
      </div>
      {open && (
        <div className="sugg-actions">
          {(s.kind === "modify_automation" || s.kind === "source_deleted") && s.automation_id && (
            <button className="btn sm primary" onClick={() => nav(suggestionEditorPath(s))}>
              {t(s.kind === "modify_automation" ? "suggestions.action_modify" : "suggestions.action_review")} <ArrowRight size={13} />
            </button>
          )}
          {s.kind === "new_automation" && (
            <button className="btn sm primary" onClick={createDraft} disabled={busy}>
              {busy ? t("suggestions.creating") : t("suggestions.action_create_draft")}
            </button>
          )}
          {s.kind === "memory_update" && (
            <>
              <button className="btn sm" onClick={() => setProfile(true)}>{t("suggestions.action_view_memory")}</button>
              <button className="btn sm primary" onClick={() => call(`/api/v1/suggestions/${s.id}/apply`)} disabled={busy}>{t("suggestions.action_ok")}</button>
            </>
          )}
          {s.kind === "run_discovery" && (
            <button className="btn sm primary" onClick={async () => { await call(`/api/v1/suggestions/${s.id}/apply`); nav("/discovery"); }}>
              {t("suggestions.action_start_discovery")} <ArrowRight size={13} />
            </button>
          )}
          <button className="btn sm ghost" onClick={() => call(`/api/v1/suggestions/${s.id}/dismiss`)} disabled={busy}>{t("suggestions.action_dismiss")}</button>
        </div>
      )}
      {profile && <ProfileModal onClose={() => setProfile(false)} />}
    </div>
  );
}

export function Suggestions() {
  const { t } = useI18n();
  const qc = useQueryClient();
  const [filter, setFilter] = useState<Filter>("open");
  const { data: items } = useSuggestions(filter);
  const { data: summary } = useSuggestionSummary();
  // Opening the page marks new suggestions as seen (clears the dot) but keeps them highlighted for this visit.
  const fresh = useRef<Set<string>>(new Set());
  useEffect(() => {
    const unseen = (items ?? []).filter((s) => s.status === "new").map((s) => s.id);
    if (!unseen.length) return;
    unseen.forEach((id) => fresh.current.add(id));
    api.post("/api/v1/suggestions/seen", { ids: unseen }).then(() => {
      qc.invalidateQueries({ queryKey: ["suggestions-summary"] });
      qc.invalidateQueries({ queryKey: ["suggestions"] });
    }).catch(() => undefined);
  }, [items, qc]);

  return (
    <>
      <TopBar crumb={t("suggestions.crumb")} />
      <div className="content">
        <div className="page wide">
          <div className="eyebrow">{t("suggestions.eyebrow")}</div>
          <h1 className="title">{t("suggestions.title")}</h1>
          <p className="lead">{t("suggestions.lead")}</p>
          <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 18, flexWrap: "wrap" }}>
            <div className="segmented">
              {FILTERS.map((f) => (
                <button key={f} className={f === filter ? "on" : ""} onClick={() => setFilter(f)}>{t(`suggestions.filter_${f}`)}</button>
              ))}
            </div>
            {summary?.processing && <span className="processing"><Lightbulb size={14} /> {t("suggestions.processing")}</span>}
          </div>
          {items && items.length === 0 ? (
            <div className="card empty-card">
              <span className="icon-tile lg"><Lightbulb size={22} /></span>
              <p>{t("suggestions.empty")}</p>
            </div>
          ) : (
            <div className="list">
              {items?.map((s) => <SuggestionCard key={s.id} s={s} highlight={fresh.current.has(s.id) && (s.status === "new" || s.status === "seen")} />)}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
