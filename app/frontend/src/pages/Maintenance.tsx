import { Wrench } from "lucide-react";
import type { Maintenance as M } from "../api/types";
import { Logo } from "../components/Logo";
import { fmtDateTime, useI18n } from "../i18n";
import { message } from "../lib/maintenance";

export function MaintenanceScreen({ m }: { m: M }) {
  const { t, lang } = useI18n();
  const msg = m.message_it || m.message_en ? message({ message_en: m.message_en ?? "", message_it: m.message_it ?? "" }, lang) : "";
  return (
    <div className="fullscreen">
      <div className="card center-card">
        <Logo size={34} />
        <h1>{t("maintenance.screen_title")}</h1>
        <p className="muted">{msg || t("maintenance.screen_default")}</p>
        {m.ends_at && <p className="mono" style={{ fontSize: 12 }}>{t("maintenance.expected_end", { time: fmtDateTime(m.ends_at, lang) })}</p>}
        <p style={{ display: "flex", gap: 8, justifyContent: "center", color: "var(--text-2)", fontSize: 13 }}>
          <Wrench size={15} /> {t("maintenance.data_safe")}
        </p>
      </div>
    </div>
  );
}
