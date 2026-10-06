import { useQueryClient } from "@tanstack/react-query";
import { Mail, Send } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import { useT } from "../i18n";
import { CONNECTOR_KINDS, allReady, useConnectors, type ConnectorKind } from "../lib/connectors";
import { useMe } from "../lib/hooks";
import { ConnectorModal } from "./ConnectorModal";
import { ConnectorStatus } from "./ConnectorStatus";

const ICON: Record<ConnectorKind, React.ReactNode> = { telegram: <Send size={20} />, gmail: <Mail size={20} /> };

/** First login: "Configure your connectors!". Shown until the user closes it once (a per-user setting, so it
 *  survives restarts), and never when every connector already works. */
export function ConnectorsWelcome() {
  const t = useT();
  const qc = useQueryClient();
  const { data: me } = useMe();
  const { data: connectors } = useConnectors(!!me);
  const [open, setOpen] = useState<ConnectorKind | null>(null);
  if (!me || !connectors || me.settings.connectors_onboarded || allReady(connectors)) return null;

  const close = async () => {
    await api.patch("/api/v1/user/settings", { connectors_onboarded: true });
    qc.invalidateQueries({ queryKey: ["me"] });
  };
  return (
    <>
      <div className="overlay" role="dialog" aria-modal aria-label={t("connectors.welcome_title")}>
        <div className="modal card">
          <h2>{t("connectors.welcome_title")}</h2>
          <p className="muted">{t("connectors.welcome_body")}</p>
          <div style={{ display: "flex", flexDirection: "column", gap: 10, margin: "18px 0 4px" }}>
            {CONNECTOR_KINDS.map((k) => {
              const c = connectors.find((x) => x.kind === k);
              return (
                <div key={k} className="card" style={{ display: "flex", alignItems: "center", gap: 14, padding: "14px 16px" }}>
                  <span className="icon-tile">{ICON[k]}</span>
                  <div style={{ flex: 1 }}>
                    <b>{t(`connectors.${k}.title`)}</b>
                    <div className="muted" style={{ fontSize: 13 }}>{t(`connectors.desc_${k}`)}</div>
                  </div>
                  <ConnectorStatus connector={c} />
                  <button className="btn sm primary" onClick={() => setOpen(k)}>{t("connectors.setup")}</button>
                </div>
              );
            })}
          </div>
          <div className="actions-row">
            <button className="btn ghost" onClick={close}>{t("connectors.welcome_later")}</button>
            <button className="btn primary" onClick={close}>{t("connectors.welcome_done")}</button>
          </div>
        </div>
      </div>
      {open && <ConnectorModal kind={open} onClose={() => setOpen(null)} />}
    </>
  );
}
