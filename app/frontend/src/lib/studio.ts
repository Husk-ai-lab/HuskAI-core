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
