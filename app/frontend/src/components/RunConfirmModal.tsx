import { X } from "lucide-react";
import { useI18n } from "../i18n";
import type { Automation } from "../api/types";

const ADDRESS = /^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$/;

/** The addresses that will receive the email (the server decides again; this is only what the user is told). */
export function validRecipients(text: string | null | undefined): string[] {
  return [...new Set((text ?? "").split(/[,;\s]+/).filter((p) => ADDRESS.test(p)))];
}

/** "Run now" is not a test: say which steps reach people before anything is sent. */
export function RunConfirmModal({ automation, busy, onConfirm, onClose }: {
  automation: Automation; busy: boolean; onConfirm: () => void; onClose: () => void;
}) {
  const { t } = useI18n();
  const nodes = automation.graph.nodes.filter((n) => n.kind === "notification");
  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal card" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal aria-label={t("editor.run_confirm_title")}>
        <div style={{ display: "flex", alignItems: "center" }}>
          <h2 style={{ flex: 1 }}>{t("editor.run_confirm_title")}</h2>
          <button className="icon-btn" onClick={onClose} aria-label={t("common.close")}><X size={15} /></button>
        </div>
        <p className="muted" style={{ marginTop: 0 }}>{t(nodes.length ? "editor.run_confirm_body" : "editor.run_confirm_none")}</p>
        {nodes.length > 0 && (
          <ul style={{ paddingLeft: 18, margin: "0 0 8px" }}>
            {nodes.map((n) => {
              if (n.channel === "gmail") {
                const to = validRecipients(n.recipients);
                return <li key={n.id}>{to.length
                  ? t("editor.run_confirm_gmail", { count: to.length, to: to.join(", ") })
                  : t("editor.run_confirm_gmail_none", { node: n.label })}</li>;
              }
              return <li key={n.id}>{t(n.channel === "telegram" ? "editor.run_confirm_telegram" : "editor.run_confirm_desktop")}</li>;
            })}
          </ul>
        )}
        <div className="actions-row">
          <button className="btn ghost" onClick={onClose}>{t("common.cancel")}</button>
          <button className="btn primary" disabled={busy} onClick={onConfirm}>{t("editor.run_confirm_go")}</button>
        </div>
      </div>
    </div>
  );
}
