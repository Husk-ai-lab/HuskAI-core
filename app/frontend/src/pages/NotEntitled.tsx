import { useQueryClient } from "@tanstack/react-query";
import { LockKeyhole } from "lucide-react";
import { Logo } from "../components/Logo";
import { useT } from "../i18n";

const STATUSES = ["none", "past_due", "suspended"];

/** The organisation can sign in, but husk-gateway says its installation is not active (billing state).
 *  Local data is untouched; AI features come back as soon as billing marks it active. */
export function NotEntitled({ status, onLogout }: { status: string; onLogout: () => void }) {
  const t = useT();
  const qc = useQueryClient();
  const key = STATUSES.includes(status) ? status : "none";
  return (
    <div className="fullscreen">
      <div className="card center-card" role="alert">
        <Logo size={34} />
        <div style={{ display: "flex", justifyContent: "center", marginTop: 18 }}>
          <div className="icon-tile lg"><LockKeyhole size={26} /></div>
        </div>
        <h1>{t("entitlement.title")}</h1>
        <p className="muted">{t(`entitlement.body_${key}`)}</p>
        <p className="muted" style={{ fontSize: 13 }}>{t("entitlement.data_safe")}</p>
        <div className="actions-row" style={{ justifyContent: "center", marginTop: 18 }}>
          <button className="btn primary" onClick={() => qc.invalidateQueries({ queryKey: ["entitlement"] })}>{t("entitlement.retry")}</button>
          <button className="btn ghost" onClick={onLogout}>{t("common.logout")}</button>
        </div>
      </div>
    </div>
  );
}
