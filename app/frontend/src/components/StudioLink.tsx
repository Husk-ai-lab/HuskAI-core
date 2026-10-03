import { ExternalLink } from "lucide-react";
import { useT } from "../i18n";

/** Shown only when something went wrong AND a local Husk Studio is running. */
export function StudioLink({ url }: { url?: string | null }) {
  const t = useT();
  if (!url) return null;
  return (
    <a className="studio-link" href={url} target="_blank" rel="noreferrer">
      {t("common.view_in_studio")} <ExternalLink size={11} />
    </a>
  );
}
