import { CircleAlert, CircleCheck } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { Logo } from "../components/Logo";
import { useT } from "../i18n";

const REASONS = ["cancelled", "failed", "no_org", "unreachable"];

/** Where the system browser lands after Clerk: nothing to do here, the Husk window continues by itself.
 *  Rendered outside the login gate, so it works in a browser that has no Husk session. */
export function AuthDone() {
  const t = useT();
  const [params] = useSearchParams();
  const ok = params.get("status") === "ok";
  const reason = params.get("reason") ?? "";
  return (
    <div className="fullscreen">
      <div className="card center-card" role="status">
        <Logo size={34} />
        <div style={{ display: "flex", justifyContent: "center", marginTop: 18 }}>
          <div className="icon-tile lg" style={ok ? undefined : { background: "var(--bg)", color: "var(--danger)" }}>
            {ok ? <CircleCheck size={26} /> : <CircleAlert size={26} />}
          </div>
        </div>
        <h1>{t(ok ? "auth_done.ok_title" : "auth_done.error_title")}</h1>
        <p className="muted">
          {ok ? t("auth_done.ok_body") : t(REASONS.includes(reason) ? `auth_done.reason_${reason}` : "auth_done.reason_failed")}
        </p>
        {!ok && <p className="muted" style={{ fontSize: 13 }}>{t("auth_done.error_body")}</p>}
      </div>
    </div>
  );
}
