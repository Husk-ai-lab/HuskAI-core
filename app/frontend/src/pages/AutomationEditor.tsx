import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ChevronDown, Power, RotateCcw, Upload, X } from "lucide-react";
import { useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { Automation, AutomationRun, AutomationView, Suggestion } from "../api/types";
import { ChatPanel } from "../components/ChatPanel";
import { FlowCanvas } from "../components/FlowCanvas";
import { DiagnoseButton, StudioAction } from "../components/StudioAction";
import { TopActions } from "../components/TopBar";
import { useI18n } from "../i18n";
import { fmtDateTime } from "../lib/format";
import { useNotify } from "../lib/notifications";

export function AutomationEditor() {
  const { id = "" } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const { data, error } = useQuery({ queryKey: ["automation", id], queryFn: () => api.get<AutomationView>(`/api/v1/automations/${id}`) });
  const [selected, setSelected] = useState<string | null>(null);
  const [menu, setMenu] = useState(false);
  const [run, setRun] = useState<{ run: AutomationRun; studio_url?: string | null } | null>(null);
  const [testing, setTesting] = useState(false);
  const [selfHeal, setSelfHeal] = useState<{ traceId: string; prompt: string; nonce: number } | null>(null);
  const { t, lang } = useI18n();
  const notify = useNotify();
  const [params, setParams] = useSearchParams();
  const suggestionId = params.get("suggestion");
  const { data: suggestion } = useQuery({
    queryKey: ["suggestion", suggestionId], enabled: !!suggestionId,
    queryFn: () => api.get<Suggestion>(`/api/v1/suggestions/${suggestionId}`),
  });

  if (error) return <div className="page"><div className="alert">{(error as Error).message}</div></div>;
  if (!data) return <div className="fullscreen"><div className="spinner" /></div>;
  const a = data.automation;

  const flash = (text: string) => notify(text, { duration: 2600 });
  const fail = (e: unknown) => notify((e as Error).message, { studio: {} });
  const usableSuggestion = suggestion && suggestion.automation_id === a.id && (suggestion.status === "new" || suggestion.status === "seen")
    ? suggestion : null;
  const clearSuggestion = () => { params.delete("suggestion"); setParams(params, { replace: true }); };
  const refresh = (updated: Automation) => {
    qc.setQueryData(["automation", id], { ...data, automation: updated });
    qc.invalidateQueries({ queryKey: ["automation", id] });
    qc.invalidateQueries({ queryKey: ["automations"] });
  };
  const setStatus = async (status: Automation["status"]) => {
    setMenu(false);
    try {
      refresh(await api.post<Automation>(`/api/v1/automations/${id}/status`, { status }));
      flash(t(status === "attiva" ? "editor.toast_active" : status === "caricata" ? "editor.toast_loaded" : "editor.toast_deactivated"));
    } catch (e) { fail(e); }
  };
  const test = async () => {
    setTesting(true);
    try { setRun(await api.post(`/api/v1/automations/${id}/test`)); }
    catch (e) { fail(e); }
    finally { setTesting(false); }
  };
  const undo = async () => {
    refresh(await api.post<Automation>(`/api/v1/automations/${id}/undo`));
    flash(t("editor.toast_restored"));
  };

  return (
    <>
      <header className="topbar">
        <button className="icon-btn" onClick={() => nav("/automazioni")} aria-label={t("common.back")}><ArrowLeft size={16} /></button>
        <span className="crumb">{t("automations.crumb")}{a.title}</span>
        <span className="spacer" />
        <span className="chip">{t("common.automation_chip")}</span>
        <span className={`status-dot ${a.status}`}>{t(`enums.automation_status.${a.status}`)}</span>
        {a.version > 1 && <button className="icon-btn" onClick={undo} title={t("editor.undo")}><RotateCcw size={15} /></button>}
        <button className="btn sm" onClick={test} disabled={testing} style={{ height: 36 }}>{testing ? t("editor.testing") : t("editor.test")}</button>
        <div style={{ position: "relative" }}>
          <button className="btn sm primary" style={{ height: 36 }} onClick={() => setMenu(!menu)}>{t("editor.activate")} <ChevronDown size={14} /></button>
          {menu && (
            <div className="card menu">
              <button onClick={() => setStatus("caricata")}>
                <span className="icon-tile"><Upload size={17} /></span>
                <div><b>{t("editor.load")}</b><span>{t("editor.load_desc")}</span></div>
              </button>
              <button onClick={() => setStatus(a.status === "attiva" ? "caricata" : "attiva")}>
                <span className="icon-tile"><Power size={17} /></span>
                <div><b>{t(a.status === "attiva" ? "editor.deactivate" : "editor.activate")}</b><span>{t(a.status === "attiva" ? "editor.deactivate_desc" : "editor.activate_desc")}</span></div>
              </button>
            </div>
          )}
        </div>
        <TopActions />
      </header>
      <div className="editor">
        <div style={{ position: "relative", display: "flex", minHeight: 0, minWidth: 0 }}>
          <div style={{ flex: 1, display: "flex", minWidth: 0 }}>
            <FlowCanvas automation={a} selected={selected} onSelect={setSelected} />
          </div>
          {data.next_run && !run && (
            <div className="canvas-chip" style={{ left: "auto", right: 18 }}>{t("editor.next_run", { time: fmtDateTime(data.next_run, lang) })}</div>
          )}
          {run && (
            <div className="card drawer">
              <h3>
                {t(run.run.status === "success" ? "editor.test_ok" : "editor.test_failed")}
                <button className="icon-btn" style={{ width: 26, height: 26 }} onClick={() => setRun(null)} aria-label={t("common.close")}><X size={13} /></button>
              </h3>
              {run.run.error && <div className="alert" style={{ marginBottom: 10 }}>{run.run.error}</div>}
              <ul className="run-log">
                {run.run.log.map((l) => (
                  <li key={l.node_id}>
                    <span style={{ color: l.status === "error" ? "var(--danger)" : l.status === "simulated" ? "var(--warning)" : "var(--success)" }}>●</span>
                    <div><b>{l.label}</b>{l.message && <div className="m">{l.message}</div>}
                      {l.node_id !== "trigger" && l.output != null && typeof l.output === "object" && "text" in (l.output as any) && <div className="m">{(l.output as any).text}</div>}
                    </div>
                  </li>
                ))}
              </ul>
              {run.run.status === "failed" && (
                <div className="err-actions" style={{ marginTop: 12 }}>
                  <StudioAction traceId={run.run.trace_id} traced={run.run.traced} />
                  {run.run.trace_id && (
                    <DiagnoseButton onClick={() => setSelfHeal({
                      traceId: run.run.trace_id!, nonce: Date.now(),
                      prompt: t("chat.self_heal_prompt", { step: run.run.log.find((l) => l.status === "error")?.label ?? "-" }),
                    })} />
                  )}
                </div>
              )}
            </div>
          )}
        </div>
        <ChatPanel automation={a} selectedNodeId={selected} suggestion={usableSuggestion} onSuggestionDone={clearSuggestion} selfHeal={selfHeal} />
      </div>
    </>
  );
}
