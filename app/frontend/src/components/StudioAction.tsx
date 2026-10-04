import { Activity, ExternalLink } from "lucide-react";
import { useT } from "../i18n";
import { studioMcpReady, studioTarget, useStudio } from "../lib/studio";

/**
 * Next to every blocking error: "Show in Husk Studio" when Studio is running, otherwise a small
 * "Install Husk Studio" link. Never hidden, so users always know where to look.
 */
export function StudioAction({ traceId, traced }: { traceId?: string | null; traced?: boolean }) {
  const t = useT();
  const target = studioTarget(useStudio(), traceId, traced);
  const stop = (e: React.MouseEvent) => e.stopPropagation(); // safe inside clickable rows
  if (target.kind === "install") {
    return (
      <a className="studio-link install" href={target.url} target="_blank" rel="noreferrer" onClick={stop} title={t("studio.install_title")}>
        {t("studio.install")} <ExternalLink size={10} />
      </a>
    );
  }
  return (
    <a className="studio-link" href={target.url} target="_blank" rel="noreferrer" onClick={stop}
       title={target.kind === "home" ? t("studio.home_hint") : undefined}>
      {t("common.view_in_studio")} <ExternalLink size={11} />
    </a>
  );
}

/** "Diagnose with Husk Studio": only when Studio's MCP is up (the Builder can then read the trace). */
export function DiagnoseButton({ onClick }: { onClick: () => void }) {
  const t = useT();
  if (!studioMcpReady(useStudio())) return null;
  return <button className="btn sm diagnose" onClick={onClick}><Activity size={13} /> {t("studio.diagnose")}</button>;
}
