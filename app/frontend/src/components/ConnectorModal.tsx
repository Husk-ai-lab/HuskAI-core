import { X } from "lucide-react";
import { useState } from "react";
import { useT } from "../i18n";
import { useConnectors, type ConnectorKind } from "../lib/connectors";
import { ConnectorForm } from "./ConnectorForm";
import { ConnectorGuide } from "./ConnectorGuide";

/** Guide + setup of one connector in a popup (Settings, the first-login popup and suggestions all open this). */
export function ConnectorModal({ kind, initialTab = "guide", onClose }: { kind: ConnectorKind; initialTab?: "guide" | "setup"; onClose: () => void }) {
  const t = useT();
  const [tab, setTab] = useState<"guide" | "setup">(initialTab);
  const { data } = useConnectors();
  const connector = data?.find((c) => c.kind === kind);
  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal card" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal aria-label={t(`connectors.${kind}.title`)}>
        <div style={{ display: "flex", alignItems: "center" }}>
          <h2 style={{ flex: 1 }}>{t(`connectors.${kind}.title`)}</h2>
          <button className="icon-btn" onClick={onClose} aria-label={t("common.close")}><X size={15} /></button>
        </div>
        <div className="tabs" role="tablist">
          <button role="tab" aria-selected={tab === "guide"} className={`tab ${tab === "guide" ? "active" : ""}`} onClick={() => setTab("guide")}>{t("connectors.tab_guide")}</button>
          <button role="tab" aria-selected={tab === "setup"} className={`tab ${tab === "setup" ? "active" : ""}`} onClick={() => setTab("setup")}>{t("connectors.tab_setup")}</button>
        </div>
        {tab === "guide" ? (
          <>
            <ConnectorGuide kind={kind} />
            <div className="actions-row"><button className="btn primary" onClick={() => setTab("setup")}>{t("connectors.next_setup")}</button></div>
          </>
        ) : (
          <ConnectorForm kind={kind} connector={connector} />
        )}
      </div>
    </div>
  );
}
