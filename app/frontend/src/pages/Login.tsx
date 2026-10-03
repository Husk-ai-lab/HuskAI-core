import { ShieldCheck } from "lucide-react";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { LanguageSwitch } from "../components/LanguageSwitch";
import { Brand } from "../components/Logo";
import { MaintenanceBanner } from "../components/MaintenanceAlert";
import { ThemeToggle } from "../components/TopBar";
import { useT } from "../i18n";
import { useSystem } from "../lib/hooks";

const LOGIN_ERRORS = ["cancelled", "failed"];

/** Login only (no guest, no sign-up). Credentials are typed on ZITADEL's hosted page (OIDC + PKCE). */
export function Login() {
  const t = useT();
  const [params] = useSearchParams();
  const code = params.get("error");
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(code && LOGIN_ERRORS.includes(code) ? `login.error_${code}` : null);
  const [rawError, setRawError] = useState<string | null>(null);
  const { data: sys } = useSystem();

  const go = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setRawError(null);
    try {
      const q = email ? `?login_hint=${encodeURIComponent(email)}` : "";
      const { url } = await api.get<{ url: string }>(`/api/v1/user/auth/login${q}`);
      window.location.href = url;
    } catch (err) {
      setRawError((err as Error).message);
      setBusy(false);
    }
  };

  return (
    <div className="login">
      <section className="login-left">
        <Brand />
        <div className="mission">
          <div className="eyebrow">{t("login.mission_eyebrow")}</div>
          <h1>
            {t("login.mission_line1")}
            <strong>{t("login.mission_line2")}</strong>
          </h1>
          <p>{t("login.mission_body")}</p>
        </div>
        <div className="login-foot"><ShieldCheck size={13} /> {t("login.privacy_footer")}</div>
      </section>
      <section className="login-right">
        <div className="theme" style={{ display: "flex", gap: 8 }}><LanguageSwitch compact /><ThemeToggle /></div>
        <form className="login-form" onSubmit={go}>
          <h2>{t("login.title")}</h2>
          <p className="sub">{t("login.subtitle")}</p>
          <div className="field">
            <label htmlFor="email">{t("login.email_label")}</label>
            <input id="email" className="input" type="email" autoComplete="username" placeholder={t("login.email_placeholder")}
                   value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
          <button className="btn primary lg" type="submit" disabled={busy || sys?.zitadel_configured === false}>
            {busy ? t("login.opening") : t("login.submit")}
          </button>
          {(error || rawError) && <div className="alert">{rawError ?? t(error!)}</div>}
          <MaintenanceBanner compact />
          {sys?.zitadel_configured === false && <div className="alert info">{t("login.not_configured")}</div>}
          <p className="login-note">{t("login.note")}</p>
        </form>
      </section>
    </div>
  );
}
