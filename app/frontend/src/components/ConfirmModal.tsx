import { X } from "lucide-react";
import { useI18n } from "../i18n";

/** In-page confirmation. The native `window.confirm` is not available in the desktop shell's webview (it answers
 *  "no" without showing anything), so every confirmation goes through this. */
export function ConfirmModal({ title, body, confirmLabel, danger, busy, onConfirm, onClose }: {
  title: string; body: string; confirmLabel?: string; danger?: boolean; busy?: boolean;
  onConfirm: () => void; onClose: () => void;
}) {
  const { t } = useI18n();
  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal card" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal aria-label={title}>
        <div style={{ display: "flex", alignItems: "center" }}>
          <h2 style={{ flex: 1 }}>{title}</h2>
          <button className="icon-btn" onClick={onClose} aria-label={t("common.close")}><X size={15} /></button>
        </div>
        <p className="muted" style={{ marginTop: 0 }}>{body}</p>
        <div className="actions-row">
          <button className="btn ghost" onClick={onClose}>{t("common.cancel")}</button>
          <button className={`btn ${danger ? "danger" : "primary"}`} disabled={busy} autoFocus onClick={onConfirm}>
            {confirmLabel ?? t("common.confirm")}
          </button>
        </div>
      </div>
    </div>
  );
}
