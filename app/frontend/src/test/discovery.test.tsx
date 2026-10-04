import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { DiscoveryRunView, StepView } from "../api/types";
import { I18nProvider } from "../i18n";
import { STAGES, stageOf, stageViews } from "../lib/discoveryStages";
import { Running } from "../pages/Discovery";

const ORDER = ["load", "knowledge_base", "extract", "repetitive", "handoffs", "impact", "map"];
const steps = (status: (i: number) => StepView["status"]): StepView[] =>
  ORDER.map((step, i) => ({ step, label: step, status: status(i) }));
const view = (s: StepView[], progress = 0.4): DiscoveryRunView => ({
  run: { id: "r1", status: "running", progress, document_ids: [] }, steps: s,
});
const mount = (v: DiscoveryRunView, event: Parameters<typeof Running>[0]["event"] = null) =>
  render(<I18nProvider><MemoryRouter><Running view={v} event={event} /></MemoryRouter></I18nProvider>);

describe("discovery stages", () => {
  it("groups the 7 backend steps into 4 stages that cover each step once", () => {
    expect(STAGES.map((s) => s.id)).toEqual(["prepare", "read", "connect", "build"]);
    expect(STAGES.flatMap((s) => [...s.steps])).toEqual(ORDER);
    expect(stageOf("handoffs")).toBe("connect");
    expect(stageOf(null)).toBeNull();
  });

  it("a stage is running while one of its steps runs or sits between two of them", () => {
    const before = stageViews(steps((i) => (i < 3 ? "done" : "pending"))).map((s) => s.status);
    expect(before).toEqual(["done", "done", "pending", "pending"]);
    const mid = stageViews(steps((i) => (i < 4 ? "done" : "pending"))).map((s) => s.status);
    expect(mid).toEqual(["done", "done", "running", "pending"]); // repetitive done, handoffs not yet started
    const failed = stageViews(steps((i) => (i < 5 ? "done" : i === 5 ? "failed" : "pending"))).map((s) => s.status);
    expect(failed[3]).toBe("failed");
  });

  it("shows 4 rows and an eyebrow of 4, not the 7 steps", () => {
    mount(view(steps((i) => (i < 2 ? "done" : i === 2 ? "running" : "pending"))));
    expect(screen.getAllByRole("listitem")).toHaveLength(4);
    expect(screen.getByText(/Step 2 of 4/)).toBeTruthy();
    expect(screen.queryByText("Linking the documents")).toBeNull();
  });

  it("renders the counter of the current stage and ignores a late event of another stage", () => {
    const v = view(steps((i) => (i < 2 ? "done" : i === 2 ? "running" : "pending")));
    const { unmount } = mount(v, { key: "discovery.event.reading", step: "extract", params: { done: 12, total: 30 } });
    expect(screen.getByText("Reading the documents · 12 of 30")).toBeTruthy();
    unmount();
    mount(v, { key: "discovery.event.linking", step: "repetitive", params: { count: 30 } });
    expect(screen.queryByText(/Linking/)).toBeNull();
  });
});
