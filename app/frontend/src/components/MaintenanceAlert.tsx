import { CalendarClock, TriangleAlert } from "lucide-react";
import { useState } from "react";
import type { MaintenanceWindow } from "../api/types";
import { fmtTime, useI18n } from "../i18n";
import { useSystem } from "../lib/hooks";
import { ALERT_AHEAD_H, COUNTDOWN_MIN, acknowledge, acknowledged, fmtWindow, message, minutesUntil, upcoming, useNow } from "../lib/maintenance";

/** Startup modal (before and after login): shown once per scheduled window, within 24 h. */
export function MaintenanceAlert() {
  const { data } = useSystem();
  const { t, lang } = useI18n();
  const now = useNow();
  const [dismissed, setDismissed] = useState<string[]>(acknowledged);
  const w = upcoming(data?.maintenance, now).find(
    (x) => !dismissed.includes(x.id) && new Date(x.starts_at).getTime() - now <= ALERT_AHEAD_H * 3_600_000,
  );
  if (!w) return null;
  const ok = () => {
    acknowledge(w.id);
    setDismissed([...dismissed, w.id]);
  };
  const mins = minutesUntil(w.starts_at, now);
  return (
    <div className="overlay" role="alertdialog" aria-modal aria-labelledby="maint-title">
      <div className="modal card">
        <span className="icon-tile lg" style={{ marginBottom: 14 }}><CalendarClock size={22} /></span>
        <h2 id="maint-title">{t("maintenance.scheduled_title")}</h2>
        <p style={{ fontWeight: 600, margin: "6px 0 4px" }}>{fmtWindow(w, lang)}</p>
        {mins <= COUNTDOWN_MIN && <p className="mono" style={{ color: "var(--accent)", fontSize: 12.5, margin: "0 0 8px" }}>{t("maintenance.starts_in", { count: mins })}</p>}
        <p className="muted" style={{ margin: "0 0 14px" }}>{message(w, lang)}</p>
        <div className={`alert ${w.blocking ? "" : "info"}`}>
          <TriangleAlert size={15} />
          <span>{t(w.blocking ? "maintenance.blocking_effect" : "maintenance.non_blocking_effect")}</span>
        </div>
        <div className="actions-row"><button className="btn primary" onClick={ok} autoFocus>{t("maintenance.got_it")}</button></div>
      </div>
    </div>
  );
}

/** One-line notice for the next window: countdown in the last hour. */
export function MaintenanceBanner({ compact = false }: { compact?: boolean }) {
  const { data } = useSystem();
  const { t, lang } = useI18n();
  const now = useNow();
  const m = data?.maintenance;
  if (!m) return null;
  if (m.active && !m.blocking && m.message_it) {
    const msg = message({ message_en: m.message_en ?? "", message_it: m.message_it ?? "" }, lang);
    return (
      <div className={compact ? "alert info" : "banner warn"}>
        <TriangleAlert size={15} /> {m.ends_at ? t("maintenance.in_progress_until", { time: fmtTime(m.ends_at, lang), message: msg }) : t("maintenance.in_progress", { message: msg })}
      </div>
    );
  }
  const w: MaintenanceWindow | undefined = upcoming(m, now).find((x) => new Date(x.starts_at).getTime() - now <= ALERT_AHEAD_H * 3_600_000);
  if (!w) return null;
  const mins = minutesUntil(w.starts_at, now);
  const when = mins <= COUNTDOWN_MIN ? t("maintenance.in_minutes", { count: mins }) : fmtWindow(w, lang);
  return (
    <div className={compact ? "alert info" : `banner ${w.blocking ? "warn" : ""}`}>
      <CalendarClock size={15} /> {t(w.blocking ? "maintenance.banner_blocking" : "maintenance.banner_non_blocking", { when, message: message(w, lang) })}
    </div>
  );
}
