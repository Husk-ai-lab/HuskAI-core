import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, ShieldCheck, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { api } from "../api/client";
import type { Personality, PersonalityId } from "../api/types";
import { TopBar } from "../components/TopBar";
import { LanguageSwitch } from "../components/LanguageSwitch";
import { locale, useI18n } from "../i18n";
import { useAiKey, useMe, useSystem } from "../lib/hooks";
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
  const { data: aiKey } = useAiKey();
  const [newKey, setNewKey] = useState<string | null>(null);
  const [keyMsg, setKeyMsg] = useState<{ ok: boolean; text: string } | null>(null);
  if (!me) return null;
  const saveKey = async () => {
    if (!newKey) return;
    setKeyMsg(null);
    try {
      await api.put("/api/v1/user/ai-key", { api_key: newKey.trim() });
      setNewKey(null);
      setKeyMsg({ ok: true, text: t("settings.ai_key_saved") });
      qc.invalidateQueries({ queryKey: ["ai-key"] });
    } catch (e) {
      setKeyMsg({ ok: false, text: (e as Error).message });
    }
  };
  const name = me.user.display_name || me.user.email || t("common.user");

  const setPersonality = async (p: PersonalityId) => {
    await api.patch("/api/v1/user/settings", { personality: p });
    qc.invalidateQueries({ queryKey: ["me"] });
  };
  const logout = async () => {
    const r = await api.post<{ end_session_url?: string | null }>("/api/v1/user/auth/logout");
    qc.clear();
    window.location.href = r.end_session_url || "/accesso";
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
              <div className="txt">
                <b>{t("settings.ai_key")}</b>
                <span>{t(aiKey?.source === "env" ? "settings.ai_key_env" : "settings.ai_key_desc")}</span>
              </div>
              {newKey === null ? (
                <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                  <span className="mono" style={{ fontSize: 12 }}>{aiKey?.hint ?? "—"}{aiKey?.model ? ` · ${aiKey.model}` : ""}</span>
                  <button className="btn sm" onClick={() => setNewKey("")}>{t("settings.change")}</button>
                </div>
              ) : (
                <div style={{ display: "flex", gap: 8 }}>
                  <input className="input mono" type="password" autoComplete="off" placeholder={t("settings.new_key")} style={{ width: 220, height: 38 }}
                         value={newKey} onChange={(e) => setNewKey(e.target.value)} autoFocus />
                  <button className="btn sm primary" disabled={newKey.trim().length < 8} onClick={saveKey}>{t("settings.verify")}</button>
                  <button className="btn sm ghost" onClick={() => { setNewKey(null); setKeyMsg(null); }}>{t("common.cancel")}</button>
                </div>
              )}
            </div>
            {keyMsg && <div className={`alert ${keyMsg.ok ? "info" : ""}`} style={{ marginBottom: 14 }}>{keyMsg.text}</div>}
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
    </>
  );
}
