import type { StepView } from "../api/types";

/** The 7 backend steps shown to the user as 4 stages (steps of one stage never overlap with another's). */
export const STAGES = [
  { id: "prepare", steps: ["load", "knowledge_base"] },
  { id: "read", steps: ["extract"] },
  { id: "connect", steps: ["repetitive", "handoffs"] },
  { id: "build", steps: ["impact", "map"] },
] as const;

export type StageId = (typeof STAGES)[number]["id"];
export type StageStatus = "pending" | "running" | "done" | "failed";
export interface StageView { id: StageId; steps: readonly string[]; status: StageStatus }

export function stageOf(step: string | null | undefined): StageId | null {
  return STAGES.find((s) => (s.steps as readonly string[]).includes(step ?? ""))?.id ?? null;
}

/** failed > running > done (all steps done) > pending; a stage between two of its steps still counts as running. */
export function stageViews(steps: StepView[]): StageView[] {
  return STAGES.map((st) => {
    const mine = steps.filter((s) => (st.steps as readonly string[]).includes(s.step));
    const done = mine.length > 0 && mine.every((s) => s.status === "done");
    const status: StageStatus = mine.some((s) => s.status === "failed") ? "failed"
      : mine.some((s) => s.status === "running") || (!done && mine.some((s) => s.status === "done")) ? "running"
      : done ? "done" : "pending";
    return { id: st.id, steps: st.steps, status };
  });
}
