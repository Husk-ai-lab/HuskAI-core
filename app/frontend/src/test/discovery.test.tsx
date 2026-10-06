import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import type { DiscoveryRunView, StepView } from "../api/types";
import { I18nProvider } from "../i18n";
import { STAGES, keepNewer, stageOf, stageViews } from "../lib/discoveryStages";
import { Discovery, Running } from "../pages/Discovery";

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


describe("the reading counter only goes up", () => {
  const ev = (done: number, total = 30, step = "extract") => ({ key: "discovery.event.reading", step, params: { done, total } });

  it("keepNewer ignores a lower count of the same step, but not a new total or another step", () => {
    expect(keepNewer(ev(17), ev(5))).toEqual(ev(17));
    expect(keepNewer(ev(17), ev(18))).toEqual(ev(18));
    expect(keepNewer(ev(17), ev(5, 12))).toEqual(ev(5, 12));  // a different total is a different attempt
    expect(keepNewer(ev(17), ev(2, 30, "repetitive"))).toEqual(ev(2, 30, "repetitive"));
    expect(keepNewer(null, ev(1))).toEqual(ev(1));
  });
});

describe("Discovery event stream", () => {
  class FakeES {
    static all: FakeES[] = [];
    listeners: Record<string, ((e: MessageEvent) => void)[]> = {};
    closed = false;
    constructor(public url: string) { FakeES.all.push(this); }
    addEventListener(n: string, fn: (e: MessageEvent) => void) { (this.listeners[n] ??= []).push(fn); }
    close() { this.closed = true; }
    emit(name: string, data: object) { this.listeners[name]?.forEach((f) => f({ data: JSON.stringify(data) } as MessageEvent)); }
  }

  beforeEach(() => {
    FakeES.all = [];
    vi.stubGlobal("EventSource", FakeES);
    const state = {
      state: "in_corso", documents_ready: 30,
      run: { run: { id: "r1", status: "running", progress: 0.3, document_ids: [] }, steps: steps((i) => (i < 2 ? "done" : i === 2 ? "running" : "pending")) },
    };
    vi.stubGlobal("fetch", vi.fn(async (url: string) => new Response(JSON.stringify(String(url).includes("discovery/state") ? state : {}), { status: 200 })));
  });
  afterEach(() => vi.unstubAllGlobals());

  it("opens ONE stream per run however many events arrive, and a replayed lower count never shows", async () => {
    render(<I18nProvider><QueryClientProvider client={new QueryClient()}><MemoryRouter><Discovery /></MemoryRouter></QueryClientProvider></I18nProvider>);
    await screen.findByText(/Step 2 of 4/);
    const es = FakeES.all[0];
    await act(async () => { for (const d of [0, 1, 17, 30]) es.emit("progress", { ...ev(d), type: "progress" }); });
    expect(await screen.findByText("Reading the documents · 30 of 30")).toBeTruthy();
    await act(async () => { es.emit("progress", { ...ev(17), type: "progress" }); });  // a reconnect replays old events
    expect(screen.getByText("Reading the documents · 30 of 30")).toBeTruthy();
    expect(FakeES.all).toHaveLength(1);  // renders did not tear the stream down and re-open it
    expect(es.closed).toBe(false);
  });

  function ev(done: number) { return { key: "discovery.event.reading", step: "extract", params: { done, total: 30 } }; }
});
