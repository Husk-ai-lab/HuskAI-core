import { useQueryClient } from "@tanstack/react-query";
import { ShieldCheck } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { LanguageSwitch } from "../components/LanguageSwitch";
import { Brand } from "../components/Logo";
import { MaintenanceBanner } from "../components/MaintenanceAlert";
import { ThemeToggle } from "../components/TopBar";
import { useT } from "../i18n";
import { useSystem } from "../lib/hooks";

const POLL_MS = 1500;
const GIVE_UP_MS = 10 * 60 * 1000;
const LOGIN_ERRORS = ["cancelled", "failed", "no_org", "unreachable", "expired"];

interface Started { url: string; poll_id: string }
interface Polled { status: "pending" | "ok" | "error"; reason?: string | null }

/** Login only (no guest, no sign-up). The backend opens the system browser on Clerk's hosted page (OAuth + PKCE);
 *  this window waits and redeems the one-time poll id, so it works the same in a native webview and in a browser. */
export function Login() {
  const t = useT();
  const qc = useQueryClient();
  const { data: sys } = useSystem();
  const [busy, setBusy] = useState(false);
  const [started, setStarted] = useState<Started | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rawError, setRawError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const since = useRef(0);

  const fail = (reason?: string | null) => {
    setStarted(null);
    setError(`login.error_${LOGIN_ERRORS.includes(reason ?? "") ? reason : "failed"}`);
  };

  useEffect(() => {
    if (!started) return;
    const id = window.setInterval(async () => {
      if (Date.now() - since.current > GIVE_UP_MS) return fail("expired");
      try {
        const r = await api.post<Polled>("/api/v1/user/auth/poll", { poll_id: started.poll_id });
        if (r.status === "ok") {
          setStarted(null);
          qc.invalidateQueries({ queryKey: ["me"] });
        } else if (r.status === "error") fail(r.reason);
      } catch {
        /* the backend is local: a missed poll is retried on the next tick */
      }
    }, POLL_MS);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [started]);

  const go = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setRawError(null);
    try {
      const s = await api.post<Started>("/api/v1/user/auth/login");
      since.current = Date.now();
      setStarted(s);
    } catch (err) {
      setRawError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(started!.url);
      setCopied(true);
    } catch {
      /* clipboard blocked: the link is still shown */
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
          {started ? (
            <>
              <h2>{t("login.waiting_title")}</h2>
              <p className="sub">{t("login.waiting_body")}</p>
              <div className="spinner" style={{ margin: "6px 0 14px" }} />
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <a className="btn" href={started.url} target="_blank" rel="noreferrer">{t("login.open_again")}</a>
                <button type="button" className="btn ghost" onClick={copy}>{copied ? t("login.link_copied") : t("login.copy_link")}</button>
                <button type="button" className="btn ghost" onClick={() => setStarted(null)}>{t("login.cancel")}</button>
              </div>
            </>
          ) : (
            <>
              <h2>{t("login.title")}</h2>
              <p className="sub">{t("login.subtitle")}</p>
              <button className="btn primary lg" type="submit" disabled={busy || sys?.login_configured === false}>
                {t("login.submit")}
              </button>
            </>
          )}
          {(error || rawError) && <div className="alert">{rawError ?? t(error!)}</div>}
          <MaintenanceBanner compact />
          {sys?.login_configured === false && <div className="alert info">{t("login.not_configured")}</div>}
          <p className="login-note">{t("login.note")}</p>
        </form>
      </section>
    </div>
  );
}
