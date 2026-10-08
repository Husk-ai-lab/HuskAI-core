import { X } from "lucide-react";
import { useState } from "react";
import { useI18n } from "../i18n";

/** Deleting everything asks for a typed word: a click is not enough for something that cannot be undone. */
export function DeleteDataModal({ busy, error, onConfirm, onClose }: {
  busy: boolean; error?: string | null; onConfirm: () => void; onClose: () => void;
}) {
  const { t } = useI18n();
  const word = t("settings.danger_word");
  const [typed, setTyped] = useState("");
  const ok = typed.trim().toLocaleUpperCase() === word;
  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal card" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal aria-label={t("settings.danger_confirm_title")}>
        <div style={{ display: "flex", alignItems: "center" }}>
          <h2 style={{ flex: 1 }}>{t("settings.danger_confirm_title")}</h2>
          <button className="icon-btn" onClick={onClose} aria-label={t("common.close")}><X size={15} /></button>
        </div>
        <p className="muted" style={{ marginTop: 0 }}>{t("settings.danger_confirm_body", { word })}</p>
        <input className="input" type="text" autoFocus autoComplete="off" spellCheck={false} aria-label={word}
               value={typed} onChange={(e) => setTyped(e.target.value)} />
        {error && <div className="alert" style={{ marginTop: 12 }}>{error}</div>}
        <div className="actions-row">
          <button className="btn ghost" onClick={onClose}>{t("common.cancel")}</button>
          <button className="btn danger" disabled={!ok || busy} onClick={onConfirm}>{t("settings.danger_go")}</button>
        </div>
      </div>
    </div>
  );
}
