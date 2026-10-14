import { api } from "../api/client";
import type { StudioStatus } from "../api/types";
import { useSystem } from "./hooks";

/** Husk Studio (local debugger): one place that decides what an error links to. */
export const STUDIO_INSTALL_URL = "https://github.com/Husk-ai-lab/husk-ai";

export type StudioTarget =
  | { kind: "run"; url: string }      // Studio running and this trace was exported to it
  | { kind: "home"; url: string }     // Studio running, but this attempt was not traced
  | { kind: "install"; url: string }; // Studio not running

export const useStudio = (): StudioStatus | undefined => useSystem().data?.studio;
export const studioMcpReady = (st?: StudioStatus) => !!st?.alive && !!st?.mcp;

export function studioTarget(st: StudioStatus | undefined, traceId?: string | null, traced?: boolean): StudioTarget {
  if (!st?.alive || !st.ui_url) return { kind: "install", url: st?.install_url || STUDIO_INSTALL_URL };
  const base = st.ui_url.replace(/\/$/, "");
  if (traceId && traced) {
    const tpl = st.run_url_template || "{base}/runs/{trace_id}";
    return { kind: "run", url: tpl.replace("{base}", base).replace("{trace_id}", traceId) };
  }
  return { kind: "home", url: base };
}

/**
 * "Install Husk Studio": inside the desktop app this opens the shell's installation manager (the shell knows whether
 * Studio is missing, failed to start or is stopped); in a plain browser, or with an older shell, it opens the GitHub
 * page exactly as before. The links keep their `href` (middle-click, accessibility): this only intercepts the click.
 */
export async function requestStudioInstall(fallbackUrl: string): Promise<void> {
  try {
    const r = await api.post<{ queued: boolean }>("/api/v1/system/shell/open-manager", { tab: "components", component: "studio" });
    if (r.queued) return;
  } catch {
    /* the backend could not be asked: the link still works */
  }
  window.open(fallbackUrl, "_blank", "noopener,noreferrer");
}

export const onStudioInstallClick = (url: string) => (e: { preventDefault: () => void }) => {
  e.preventDefault();
  void requestStudioInstall(url);
};
