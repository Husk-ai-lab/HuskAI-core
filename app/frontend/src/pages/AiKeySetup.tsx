import { useQueryClient } from "@tanstack/react-query";
import { Eye, EyeOff, KeyRound } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import { LanguageSwitch } from "../components/LanguageSwitch";
import { Logo } from "../components/Logo";
import { useI18n } from "../i18n";

/**
 * First access after login: pick the language and enter the AI key provided by Husk AI.
 * The key is verified with Regolo.ai before it is stored; the screen stays until a valid key is saved.
 */
export function AiKeySetup({ onLogout }: { onLogout: () => void }) {
  const { t, lang } = useI18n();
  const [key, setKey] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const qc = useQueryClient();

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.put("/api/v1/user/ai-key", { api_key: key.trim() });
      await api.patch("/api/v1/user/settings", { language: lang });
      setKey("");
      await qc.invalidateQueries({ queryKey: ["me"] });
      await qc.invalidateQueries({ queryKey: ["ai-key"] });
      await qc.invalidateQueries({ queryKey: ["system"] });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fullscreen">
      <form className="card center-card" onSubmit={save} style={{ textAlign: "left" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <Logo size={28} /><b style={{ fontFamily: "var(--font-display)", fontSize: 22, flex: 1 }}>Husk AI</b>
        </div>
        <h1 style={{ fontSize: 36, margin: "22px 0 6px" }}>{t("aiKey.title")}</h1>
        <p className="muted" style={{ margin: "0 0 20px" }}>{t("aiKey.subtitle")}</p>
        <div className="field" style={{ marginBottom: 18 }}>
          <label>{t("aiKey.language_label")}</label>
          <LanguageSwitch />
        </div>
        <div className="field">
          <label htmlFor="ai-key">{t("aiKey.key_label")}</label>
          <div style={{ position: "relative" }}>
            <input id="ai-key" className="input mono" type={show ? "text" : "password"} autoComplete="off" spellCheck={false}
                   placeholder={t("aiKey.key_placeholder")} value={key} onChange={(e) => setKey(e.target.value)} autoFocus
                   style={{ paddingRight: 44 }} />
            <button type="button" className="icon-btn" onClick={() => setShow(!show)} aria-label={t(show ? "aiKey.hide" : "aiKey.show")}
                    style={{ position: "absolute", right: 5, top: 5, width: 34, height: 34, border: 0 }}>
              {show ? <EyeOff size={15} /> : <Eye size={15} />}
            </button>
          </div>
        </div>
        {error && <div className="alert" style={{ marginTop: 14 }}>{error}</div>}
        <div style={{ display: "flex", gap: 10, marginTop: 20, alignItems: "center" }}>
          <button className="btn primary lg" type="submit" disabled={busy || key.trim().length < 8}>
            <KeyRound size={16} /> {busy ? t("aiKey.verifying") : t("aiKey.submit")}
          </button>
          <span style={{ flex: 1 }} />
          <button type="button" className="btn ghost" onClick={onLogout}>{t("common.logout")}</button>
        </div>
        <p className="login-note" style={{ textAlign: "left", marginTop: 18 }}>{t("aiKey.no_key")}</p>
      </form>
    </div>
  );
}
