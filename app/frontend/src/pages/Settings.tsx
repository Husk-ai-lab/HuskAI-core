import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Mail, Send, ShieldCheck, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { Personality, PersonalityId } from "../api/types";
import { ConnectorModal } from "../components/ConnectorModal";
import { ConnectorStatus } from "../components/ConnectorStatus";
import { DeleteDataModal } from "../components/DeleteDataModal";
import { TopBar } from "../components/TopBar";
import { LanguageSwitch } from "../components/LanguageSwitch";
import { locale, useI18n } from "../i18n";
import { CONNECTOR_KINDS, useConnectors, type ConnectorKind } from "../lib/connectors";
import { useMe, useSystem } from "../lib/hooks";
import { useTheme } from "../lib/theme";

export function Settings() {
  const { data: me } = useMe();
  const { data: sys } = useSystem();
  const { theme, setTheme } = useTheme();
  const qc = useQueryClient();
  const { t, lang } = useI18n();
  const { data: personalities } = useQuery({ queryKey: ["personalities", lang], queryFn: () => api.get<Personality[]>("/api/v1/agent/personalities") });
  const { data: usage } = useQuery({ queryKey: ["usage"], queryFn: () => api.get<{ tokens_in: number; tokens_out: number; calls: number }>("/api/v1/user/usage") });
  const fileInput = useRef<HTMLInputElement>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const { data: connectors } = useConnectors();
  const [wipe, setWipe] = useState<{ error: string | null } | null>(null);
  const [params, setParams] = useSearchParams();
  const fromLink = params.get("connector");  // a suggestion sends the user here: open that connector's setup
  const [modal, setModal] = useState<{ kind: ConnectorKind; tab: "guide" | "setup" } | null>(
    CONNECTOR_KINDS.includes(fromLink as ConnectorKind) ? { kind: fromLink as ConnectorKind, tab: "setup" } : null,
  );
  const closeModal = () => { setModal(null); if (params.has("connector")) { params.delete("connector"); setParams(params, { replace: true }); } };
  if (!me) return null;
  const name = me.user.display_name || me.user.email || t("common.user");

  const setPersonality = async (p: PersonalityId) => {
    await api.patch("/api/v1/user/settings", { personality: p });
    qc.invalidateQueries({ queryKey: ["me"] });
  };
  const logout = async () => {
    await api.post("/api/v1/user/auth/logout");
    qc.clear();
    window.location.href = "/accesso";
  };
  const doImport = async (f: File) => {
    if (!confirm(t("settings.import_confirm"))) return;
    setBusy(true);
    setMsg(null);
    const fd = new FormData();
    fd.append("file", f);
    fd.append("confirm", "true");
    try {
      const r = await api.form<{ documents: number; safety_copy: string }>("/api/v1/user/data/import", fd);
      qc.invalidateQueries();
      setMsg({ ok: true, text: t("settings.import_done", { count: r.documents, path: r.safety_copy }) });
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message });
    } finally {
      setBusy(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  };

  const doWipe = async () => {
    setBusy(true);
    try {
      const r = await api.del<{ documents: number; automations: number }>("/api/v1/user/data", { confirm: true });
      setWipe(null);
      setMsg({ ok: true, text: t("settings.danger_done", { documents: r.documents, automations: r.automations }) });
      qc.invalidateQueries();
    } catch (e) {
      setWipe({ error: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <TopBar crumb={t("settings.crumb")} />
      <div className="content">
        <div className="page">
          <div className="eyebrow">{t("settings.eyebrow")}</div>
          <h1 className="title">{t("settings.title")}</h1>

          <div className="card profile-card">
            <div className="avatar lg">{name.slice(0, 1).toUpperCase()}</div>
            <div style={{ flex: 1 }}>
              <h2>{name}</h2>
              <div className="muted">{me.user.email}{me.user.org_name ? ` · ${me.user.org_name}` : ""}</div>
            </div>
            <button className="btn sm" onClick={logout}>{t("common.logout")}</button>
          </div>

          <div className="card settings-card">
            <div className="setting">
              <div className="txt"><b>{t("settings.personality")}</b><span>{personalities?.find((p) => p.id === me.settings.personality)?.description}</span></div>
              <select className="input" style={{ width: 190, height: 38 }} value={me.settings.personality} onChange={(e) => setPersonality(e.target.value as PersonalityId)}>
                {personalities?.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
              </select>
            </div>
            <div className="setting">
              <div className="txt"><b>{t("settings.appearance")}</b><span>{t("settings.appearance_desc")}</span></div>
              <div className="segmented">
                <button className={theme === "light" ? "on" : ""} onClick={() => setTheme("light")}>{t("settings.light")}</button>
                <button className={theme === "dark" ? "on" : ""} onClick={() => setTheme("dark")}>{t("settings.dark")}</button>
              </div>
            </div>
            <div className="setting">
              <div className="txt"><b>{t("settings.language")}</b><span>{t("settings.language_desc")}</span></div>
              <LanguageSwitch onChange={(l) => api.patch("/api/v1/user/settings", { language: l }).then(() => qc.invalidateQueries())} />
            </div>
            <div className="setting">
              <div className="txt">
                <b>{t("settings.privacy")}</b>
                <span>{t("settings.privacy_desc")}</span>
              </div>
              <span className="ok-badge"><ShieldCheck size={13} /> {t("settings.privacy_badge")}</span>
            </div>
          </div>

          <div className="card settings-card">
            <div className="setting">
              <div className="txt"><b>{t("connectors.section_title")}</b><span>{t("connectors.section_desc")}</span></div>
            </div>
            {CONNECTOR_KINDS.map((k) => {
              const c = connectors?.find((x) => x.kind === k);
              return (
                <div className="setting" key={k}>
                  <span className="icon-tile">{k === "telegram" ? <Send size={18} /> : <Mail size={18} />}</span>
                  <div className="txt">
                    <b>{t(`connectors.${k}.title`)}</b>
                    <span>{c?.hint ?? t(`connectors.desc_${k}`)}</span>
                    {c?.state === "error" && c.last_error && <span style={{ color: "var(--danger)" }}> · {t("connectors.last_error", { reason: t(`connectors.error_${c.last_error}`) })}</span>}
                  </div>
                  <ConnectorStatus connector={c} />
                  <div style={{ display: "flex", gap: 8 }}>
                    <button className="btn sm" onClick={() => setModal({ kind: k, tab: "guide" })}>{t("connectors.guide")}</button>
                    <button className="btn sm primary" onClick={() => setModal({ kind: k, tab: "setup" })}>
                      {t(c && c.state !== "not_configured" ? "connectors.change" : "connectors.setup")}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>

          <div className="card settings-card">
            <div className="setting">
              <div className="txt"><b>{t("settings.backup")}</b><span>{t("settings.backup_desc")}</span></div>
              <div style={{ display: "flex", gap: 8 }}>
                <a className="btn sm" href="/api/v1/user/data/export" download><Download size={14} /> {t("settings.export")}</a>
                <button className="btn sm" disabled={busy} onClick={() => fileInput.current?.click()}><Upload size={14} /> {busy ? t("settings.importing") : t("settings.import")}</button>
                <input ref={fileInput} type="file" accept=".db,.sqlite,.sqlite3" hidden onChange={(e) => e.target.files?.[0] && doImport(e.target.files[0])} />
              </div>
            </div>
            {msg && <div className={`alert ${msg.ok ? "info" : ""}`} style={{ marginBottom: 16 }}>{msg.text}</div>}
            <div className="setting">
              <div className="txt"><b>{t("settings.data_folder")}</b><span className="mono" style={{ fontSize: 12 }}>{sys?.data_dir}</span></div>
            </div>
            <div className="setting">
              <div className="txt"><b>{t("settings.danger_title")}</b><span>{t("settings.danger_desc")}</span></div>
              <div style={{ display: "flex", gap: 8 }}>
                <a className="btn sm" href="/api/v1/user/data/export" download><Download size={14} /> {t("settings.danger_export_first")}</a>
                <button className="btn sm danger" disabled={busy} onClick={() => setWipe({ error: null })}>{t("settings.danger_button")}</button>
              </div>
            </div>
            <div className="setting">
              <div className="txt"><b>{t("settings.usage")}</b><span>{t("settings.usage_desc")}</span></div>
              <span className="mono" style={{ fontSize: 12 }}>{usage ? t("settings.usage_value", { tokens: (usage.tokens_in + usage.tokens_out).toLocaleString(locale(lang)), count: usage.calls }) : "—"}</span>
            </div>
            <div className="setting">
              <div className="txt"><b>Husk Studio</b><span>{t("settings.studio_desc")}</span></div>
              <span className="mono" style={{ fontSize: 12 }}>{sys?.studio.alive ? t("settings.studio_connected") + (sys.studio.mcp ? " · MCP" : "") : t("settings.studio_off")}</span>
            </div>
            <div className="setting">
              <div className="txt"><b>{t("settings.version")}</b><span>{sys?.llm_model ? t("settings.model", { model: sys.llm_model }) : t("settings.model_none")}</span></div>
              <span className="mono" style={{ fontSize: 12 }}>Husk {sys?.version}</span>
            </div>
          </div>
        </div>
      </div>
      {wipe && <DeleteDataModal busy={busy} error={wipe.error} onConfirm={doWipe} onClose={() => setWipe(null)} />}
      {modal && <ConnectorModal kind={modal.kind} initialTab={modal.tab} onClose={closeModal} />}
    </>
  );
}
