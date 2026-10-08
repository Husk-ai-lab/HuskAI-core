import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Automation } from "../api/types";
import { RunConfirmModal, validRecipients } from "../components/RunConfirmModal";
import { I18nProvider, type Lang } from "../i18n";
import { AutomationEditor } from "../pages/AutomationEditor";

vi.mock("../components/ChatPanel", () => ({ ChatPanel: () => null })); // the side chat has its own tests

const auto = (recipients: string | null = "boss@cliente.it; hr@cliente.it, nope"): Automation => ({
  id: "auto_1", title: "Controllo", description: "", status: "bozza", version: 1, source_document_ids: [], hours_per_week: 1, priority: "media", updated_at: "",
  graph: {
    nodes: [
      { id: "trigger", kind: "trigger", label: "Manuale", position: { x: 60, y: 100 }, trigger: "manual" },
      { id: "mail", kind: "notification", label: "Avvisa", position: { x: 300, y: 100 }, channel: "gmail", recipients },
      { id: "tg", kind: "notification", label: "Telegram", position: { x: 500, y: 100 }, channel: "telegram" },
    ],
    edges: [{ id: "e1", source: "trigger", target: "mail" }, { id: "e2", source: "mail", target: "tg" }],
  },
} as unknown as Automation);

const wrap = (ui: React.ReactNode, lang: Lang = "en", path = "/") =>
  render(<I18nProvider initial={lang}><QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter></QueryClientProvider></I18nProvider>);

describe("validRecipients", () => {
  it("keeps only addresses, once each", () => {
    expect(validRecipients("a@x.it; b@y.it  a@x.it, Acquisti")).toEqual(["a@x.it", "b@y.it"]);
    expect(validRecipients(null)).toEqual([]);
  });
});

describe("RunConfirmModal", () => {
  it("lists what will reach people, with the real addresses, and asks before anything is sent", () => {
    const onConfirm = vi.fn();
    wrap(<RunConfirmModal automation={auto()} busy={false} onConfirm={onConfirm} onClose={() => {}} />);
    expect(screen.getByText("Gmail: an email to 2 recipients (boss@cliente.it, hr@cliente.it)")).toBeInTheDocument();
    expect(screen.getByText("Telegram: a message to your configured chat")).toBeInTheDocument();
    expect(onConfirm).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Run for real" }));
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it("warns when Gmail has no valid address (the step would be skipped)", () => {
    wrap(<RunConfirmModal automation={auto("Acquisti")} busy={false} onConfirm={() => {}} onClose={() => {}} />);
    expect(screen.getByText("Gmail: «Avvisa» has no valid address, it will be skipped")).toBeInTheDocument();
  });
});

describe("Run now in the editor", () => {
  let calls: { method: string; path: string }[] = [];
  beforeEach(() => {
    calls = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      const path = url.split("?")[0];
      const method = init?.method ?? "GET";
      calls.push({ method, path });
      if (path === "/api/v1/automations/auto_1") return new Response(JSON.stringify({ automation: auto(), next_run: null }), { status: 200 });
      if (path === "/api/v1/automations/auto_1/run") return new Response(JSON.stringify({ run: { id: "r", automation_id: "auto_1", trigger: "manual", status: "success", log: [] } }), { status: 200 });
      return new Response("[]", { status: 200 });
    }));
  });
  afterEach(() => vi.unstubAllGlobals());

  it("nothing is sent until the confirmation, and the result is titled as a real run", async () => {
    wrap(<Routes><Route path="/automazioni/:id" element={<AutomationEditor />} /></Routes>, "en", "/automazioni/auto_1");
    fireEvent.click(await screen.findByRole("button", { name: "Run now" }));
    expect(await screen.findByRole("dialog", { name: "Run for real?" })).toBeInTheDocument();
    expect(calls.some((c) => c.path.endsWith("/run"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Run for real" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/api/v1/automations/auto_1/run")).toBe(true));
    expect(await screen.findByText("Run completed")).toBeInTheDocument();
  });
});
