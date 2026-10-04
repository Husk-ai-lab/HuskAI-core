import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Check, RefreshCw, Search, TriangleAlert, X } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { DiscoveryReport, DiscoveryRunView, MemoryView } from "../api/types";
import { Markdown } from "../components/Markdown";
import { RobotArm } from "../components/RobotArm";
import { StudioAction } from "../components/StudioAction";
import { TopBar } from "../components/TopBar";
import { useI18n } from "../i18n";
import { hoursValue, pad2 } from "../lib/format";
import { useDiscoveryState, useDocuments, useSystem } from "../lib/hooks";
import { WORK_WARNING_MIN, minutesUntil, nextBlocking, useNow } from "../lib/maintenance";

/** Working weeks per year used to annualise the weekly estimate (stated in the UI). */
const WEEKS_PER_YEAR = 46;

interface RunEvent { key?: string | null; params?: Record<string, string | number> }

function useRunEvents(runId: string | undefined, onEnd: () => void): RunEvent | null {
  const [last, setLast] = useState<RunEvent | null>(null);
  const qc = useQueryClient();
  useEffect(() => {
    if (!runId) return;
    const es = new EventSource(`/api/v1/discovery/runs/${runId}/events`);
    const refresh = () => qc.invalidateQueries({ queryKey: ["discovery-state"] });
    const handler = (e: MessageEvent) => {
      const d = JSON.parse(e.data) as RunEvent;
      if (d.key) setLast(d);
      refresh();
    };
    ["step_started", "step_done", "progress", "log", "waiting_network"].forEach((n) => es.addEventListener(n, handler as EventListener));
    ["completed", "failed", "paused"].forEach((n) => es.addEventListener(n, () => { es.close(); refresh(); onEnd(); }));
    return () => es.close();
  }, [runId, qc, onEnd]);
  return last;
}

function Start({ ready, onStart, busy, error }: { ready: number; onStart: () => void; busy: boolean; error: string | null }) {
  const nav = useNavigate();
  const { t } = useI18n();
  const { data: sys } = useSystem();
  const now = useNow();
  const soon = nextBlocking(sys?.maintenance, now);
  const soonMin = soon ? minutesUntil(soon.starts_at, now) : null;
  return (
    <div className="hero-split">
      <div>
        <div className="eyebrow">{t("discovery.eyebrow")}</div>
        <h1 className="title">{t("discovery.start_title")}</h1>
        <p className="lead">{t("discovery.start_lead")}</p>
        <ul className="steps4">
          {[1, 2, 3, 4].map((n) => (
            <li key={n}><span className="n">{pad2(n)}</span><span><b>{t(`discovery.four_${n}_title`)}</b> — {t(`discovery.four_${n}_body`)}</span></li>
          ))}
        </ul>
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          {ready > 0 ? (
            <button className="btn primary lg" onClick={onStart} disabled={busy}><Search size={16} /> {busy ? t("discovery.starting") : t("discovery.start")}</button>
          ) : (
            <button className="btn primary lg" onClick={() => nav("/conoscenza")}>{t("discovery.upload_documents")} <ArrowRight size={16} /></button>
          )}
          <span className="muted" style={{ fontSize: 13 }}>{t("discovery.ready_count", { count: ready })}</span>
        </div>
        {soonMin !== null && soonMin <= WORK_WARNING_MIN && (
          <div className="alert" style={{ marginTop: 16 }}><TriangleAlert size={15} /> {t("discovery.maintenance_soon", { count: soonMin })}</div>
        )}
        {error && <div className="alert" style={{ marginTop: 16 }}><TriangleAlert size={15} /> {error}</div>}
      </div>
      <RobotArm />
    </div>
  );
}

function Running({ view, event }: { view: DiscoveryRunView; event: RunEvent | null }) {
  const { t } = useI18n();
  const r = view.run;
  const idx = Math.max(0, view.steps.findIndex((s) => s.status === "running" || s.status === "pending"));
  const current = view.steps.find((s) => s.status === "running") ?? view.steps[idx];
  const label = (step: string) => t(`discovery.steps.${step}`);
  return (
    <div className="hero-split">
      <div>
        <div className="eyebrow">{t("discovery.running_eyebrow", { step: Math.min(idx + 1, 7), total: 7 })}</div>
        <h1 className="title">{t("discovery.running_title")}</h1>
        <div className="progress-head"><h3>{current ? label(current.step) : ""}</h3><span>{Math.round(r.progress * 100)}%</span></div>
        <div className="bar"><i style={{ width: `${Math.max(4, r.progress * 100)}%` }} /></div>
        <ul className="steps7">
          {view.steps.map((s) => (
            <li key={s.step} className={s.status}>
              <span className="ico">{s.status === "done" ? <Check size={13} color="var(--success)" /> : s.status === "failed" ? <X size={13} /> : <span className="ring" />}</span>
              {label(s.step)}
            </li>
          ))}
        </ul>
        <div className="log-line">
          {r.status === "waiting_network" ? t("discovery.event.waiting_network") : event?.key ? t(event.key, event.params) : ""}
        </div>
      </div>
      <RobotArm working />
    </div>
  );
}

function Report({ runId, onAgain, busy }: { runId: string; onAgain: () => void; busy: boolean }) {
  const nav = useNavigate();
  const { t, lang } = useI18n();
  const { data: report } = useQuery({ queryKey: ["report", runId], queryFn: () => api.get<DiscoveryReport>(`/api/v1/discovery/runs/${runId}/report`) });
  const { data: memory } = useQuery({ queryKey: ["memory", lang], queryFn: () => api.get<MemoryView>("/api/v1/discovery/memory") });
  const { data: docs } = useDocuments();
  const [showMem, setShowMem] = useState(false);
  const name = (id: string) => docs?.find((d) => d.id === id)?.filename ?? id;
  if (!report) return null;
  return (
    <div className="page wide">
      <div className="eyebrow">{t("discovery.report_eyebrow")}</div>
      <div style={{ display: "flex", alignItems: "flex-end", gap: 20 }}>
        <div style={{ flex: 1 }}>
          <h1 className="title">{t("discovery.report_title")}</h1>
          <p className="lead">{t("discovery.report_lead")}</p>
          {report.usage?.total && report.usage.total.calls > 0 && (
            <div className="mono muted tokens-used" style={{ fontSize: 11.5, marginTop: -18, marginBottom: 22 }}
                 title={Object.entries(report.usage.by_model ?? {}).map(([m, u]) => `${m}: ${u.calls} · ${(u.tokens_in + u.tokens_out).toLocaleString()}`).join("\n")}>
              {t("discovery.tokens_used", { tokens: (report.usage.total.tokens_in + report.usage.total.tokens_out).toLocaleString(), calls: report.usage.total.calls })}
            </div>
          )}
        </div>
        <button className="btn" style={{ marginBottom: 28 }} onClick={onAgain} disabled={busy}><RefreshCw size={14} /> {t("discovery.analyze_again")}</button>
      </div>
      <div className="stats">
        <div className="card stat"><b>{report.documents_analyzed}</b><span>{t("discovery.stat_documents", { count: report.documents_analyzed })}</span></div>
        <div className="card stat"><b>{report.opportunities}</b><span>{t("discovery.stat_opportunities", { count: report.opportunities })}</span></div>
        <div className="card stat"><b>{report.repetitive_activities}</b><span>{t("discovery.stat_activities", { count: report.repetitive_activities })}</span></div>
        {report.total_hours_per_week > 0 && (
          <div className="card stat hero">
            <b>{t("discovery.hours_year", { hours: Math.round(report.total_hours_per_week * WEEKS_PER_YEAR) })}</b>
            <span>{t("discovery.hours_year_note", { weekly: hoursValue(report.total_hours_per_week), weeks: WEEKS_PER_YEAR })}</span>
          </div>
        )}
      </div>
      <div className="list">
        {report.items.map((o, i) => (
          <div key={o.id} className="card opp">
            <span className="row-num">{pad2(i + 1)}</span>
            <div>
              <div className="row-title"><h3>{o.title}</h3><span className="chip">{t("common.automation_chip")}</span></div>
              <p className="row-desc">{o.explanation}</p>
              <div className="row-meta">
                <span>{t("common.source", { source: o.source_document_ids.slice(0, 2).map(name).join(", ") })}</span>
                <span>{t("common.estimated_impact", { hours: hoursValue(o.hours_per_week) })}</span>
                <span>{t("discovery.priority", { priority: t(`enums.priority.${o.priority}`) })}</span>
              </div>
            </div>
            {o.automation_id && <button className="btn sm" onClick={() => nav(`/automazioni/${o.automation_id}`)}>{t("common.open")} <ArrowRight size={13} /></button>}
          </div>
        ))}
      </div>
      {memory && (
        <div className="card" style={{ marginTop: 28, padding: "18px 24px" }}>
          <div style={{ display: "flex", alignItems: "center" }}>
            <div style={{ flex: 1 }}>
              <b>{t("discovery.memory_title")}</b>
              <div className="muted" style={{ fontSize: 13 }}>
                {t("discovery.memory_summary", { facts: memory.facts.length, max: memory.max_facts, networks: memory.networks.length })}
              </div>
            </div>
            <button className="btn sm" onClick={() => setShowMem(!showMem)}>{t(showMem ? "common.hide" : "common.show")}</button>
          </div>
          {showMem && <div style={{ marginTop: 14 }}><Markdown text={memory.company_profile_markdown} /></div>}
        </div>
      )}
    </div>
  );
}

export function Discovery() {
  const { t } = useI18n();
  const { data: st, refetch } = useDiscoveryState();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const qc = useQueryClient();
  const run = st?.run;
  const active = run && ["running", "pending", "waiting_network"].includes(run.run.status);
  const event = useRunEvents(active ? run?.run.id : undefined, () => {
    refetch();
    qc.invalidateQueries({ queryKey: ["automations"] });
    qc.invalidateQueries({ queryKey: ["documents"] });
  });

  const start = async () => {
    setBusy(true);
    setError(null);
    try { await api.post("/api/v1/discovery/runs"); await refetch(); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };
  const resume = async () => {
    if (!run) return;
    setBusy(true);
    try { await api.post(`/api/v1/discovery/runs/${run.run.id}/resume`); await refetch(); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };

  let body: React.ReactNode = null;
  if (!st) body = null;
  else if (active && run) body = <Running view={run} event={event} />;
  else if (run && (run.run.status === "failed" || run.run.status === "paused")) {
    body = (
      <div className="hero-split">
        <div>
          <div className="eyebrow">{t(run.run.status === "paused" ? "discovery.paused_eyebrow" : "discovery.failed_eyebrow")}</div>
          <h1 className="title">{t("discovery.resume_title")}</h1>
          <p className="lead">{t("discovery.resume_lead")}</p>
          {run.run.error && <div className="alert" style={{ marginBottom: 16 }}><TriangleAlert size={15} /> {run.run.error}</div>}
          <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
            <button className="btn primary lg" onClick={resume} disabled={busy}><RefreshCw size={15} /> {t("discovery.resume")}</button>
            <button className="btn lg" onClick={start} disabled={busy}>{t("discovery.restart")}</button>
            <StudioAction traceId={run.run.trace_id} traced={run.run.traced} />
          </div>
          {error && <div className="alert" style={{ marginTop: 16 }}>{error}</div>}
        </div>
        <RobotArm />
      </div>
    );
  } else if (st.last_completed_run_id) body = <Report runId={st.last_completed_run_id} onAgain={start} busy={busy} />;
  else body = <Start ready={st.documents_ready} onStart={start} busy={busy} error={error} />;

  return (
    <>
      <TopBar crumb={t("discovery.crumb")} />
      <div className="content">{body}</div>
    </>
  );
}
