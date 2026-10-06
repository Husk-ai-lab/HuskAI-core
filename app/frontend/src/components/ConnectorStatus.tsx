import { useT } from "../i18n";
import type { Connector } from "../api/types";

/** Ready / needs attention / not set up, with the app's status dot. */
export function ConnectorStatus({ connector }: { connector?: Connector }) {
  const t = useT();
  const state = connector?.state ?? "not_configured";
  const cls = state === "ready" ? "attiva" : state === "error" ? "errore" : "";
  return <span className={`status-dot ${cls}`}>{t(`connectors.status_${state}`)}</span>;
}
