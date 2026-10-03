import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, vi } from "vitest";
import type { Automation, Suggestion } from "../api/types";
import { ChatPanel } from "../components/ChatPanel";
import { SuggestionsButton } from "../components/SuggestionsButton";
import { I18nProvider } from "../i18n";
import { classify, chunks, groupByFolder, walkEntry, type EntryLike } from "../lib/folderUpload";
import { NotificationsProvider, SNACK_MS } from "../lib/notifications";
import { useSuggestionEvents } from "../lib/suggestions";
import { Suggestions } from "../pages/Suggestions";

const sugg = (over: Partial<Suggestion>): Suggestion => ({
  id: "sug_1", kind: "modify_automation", status: "new", automation_id: "auto_1", document_ids: ["doc_1"],
  title: "Notify Purchasing too", body: "The new procedure asks to notify Purchasing.",
  prompt: "The new document asks to notify Purchasing too: add the notification.",
  evidence: [{ document_id: "doc_1", chunk_id: "doc_1#c0", quote: "notify Purchasing" }], confidence: 0.85,
  created_at: "2026-10-02T10:00:00Z", updated_at: "2026-10-02T10:00:00Z", ...over,
});

const automation: Automation = {
  id: "auto_1", title: "BOM revision check", description: "", status: "bozza", version: 1,
  graph: { nodes: [], edges: [] }, source_document_ids: [], hours_per_week: 2, priority: "media",
} as unknown as Automation;

let routes: Record<string, unknown> = {};
let posts: { url: string; body: unknown }[] = [];

beforeEach(() => {
  posts = [];
  routes = {
    "/api/v1/suggestions/summary": { unread: 0, open: 0, processing: false },
    "/api/v1/documents": [{ id: "doc_1", filename: "Procedura_Anomalie_Rev2.md" }],
    "/api/v1/automations": [automation],
    "/api/v1/agent/personalities": [],
    "/api/v1/agent/chats/automation/auto_1": { chat: { id: "chat_1", personality: "bilanciato" }, messages: [], proposals: [], suggestions: [] },
  };
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const path = url.split("?")[0];
    if (init?.method === "POST") posts.push({ url, body: init.body ? JSON.parse(String(init.body)) : null });
    const body = path in routes ? routes[path] : url in routes ? routes[url] : { updated: 0 };
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
});
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

function wrap(ui: ReactNode) {
  return render(
    <I18nProvider initial="en">
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter><NotificationsProvider>{ui}</NotificationsProvider></MemoryRouter>
      </QueryClientProvider>
    </I18nProvider>,
  );
}

describe("light bulb", () => {
  it("shows the accent dot only with unread suggestions", async () => {
    routes["/api/v1/suggestions/summary"] = { unread: 2, open: 2, processing: false };
    wrap(<SuggestionsButton />);
    expect(await screen.findByTestId("bulb-dot")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "2 new suggestions" })).toHaveAttribute("href", "/suggerimenti");
  });

  it("has no dot when everything is read", async () => {
    wrap(<SuggestionsButton />);
    await screen.findByRole("link", { name: "Suggestions" });
    expect(screen.queryByTestId("bulb-dot")).toBeNull();
  });
});

describe("snackbar", () => {
  class FakeES {
    static last: FakeES;
    listeners: Record<string, ((e: MessageEvent) => void)[]> = {};
    constructor(public url: string) { FakeES.last = this; }
    addEventListener(name: string, fn: (e: MessageEvent) => void) { (this.listeners[name] ??= []).push(fn); }
    emit(name: string, data: unknown) { this.listeners[name]?.forEach((fn) => fn(new MessageEvent(name, { data: JSON.stringify(data) }))); }
    close() {}
  }
  function Listener() { useSuggestionEvents(); return null; }

  it("appears on a new suggestion and disappears by itself", async () => {
    vi.stubGlobal("EventSource", FakeES);
    vi.useFakeTimers({ shouldAdvanceTime: true });
    wrap(<Listener />);
    expect(FakeES.last.url).toBe("/api/v1/suggestions/events");
    act(() => FakeES.last.emit("created", { type: "created", count: 2, top: { id: "s", kind: "modify_automation", title: "x" } }));
    expect(screen.getByText("2 new suggestions from your documents")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "View" })).toBeInTheDocument();
    act(() => { vi.advanceTimersByTime(SNACK_MS + 50); });
    expect(screen.queryByText("2 new suggestions from your documents")).toBeNull();
  });
});

describe("suggestions page", () => {
  it("renders the right actions per kind and marks new ones as seen", async () => {
    routes["/api/v1/suggestions"] = [
      sugg({}),
      sugg({ id: "sug_2", kind: "new_automation", automation_id: null, title: "Evening anomaly check", hours_per_week: 2 }),
      sugg({ id: "sug_3", kind: "memory_update", automation_id: null, title: "Company profile updated (1 change)", body: "+ fact", evidence: [] }),
      sugg({ id: "sug_4", kind: "source_deleted", status: "seen", title: "«BOM revision check» uses a deleted document", evidence: [] }),
    ];
    wrap(<Suggestions />);
    expect(await screen.findByText("Notify Purchasing too")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Modify automation/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create draft" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "View profile" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Review automation/ })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Dismiss" })).toHaveLength(4);
    expect(screen.getAllByText(/«notify Purchasing»/).length).toBeGreaterThan(0);
    await waitFor(() => expect(posts.find((p) => p.url === "/api/v1/suggestions/seen")?.body).toEqual({ ids: ["sug_1", "sug_2", "sug_3"] }));
  });
});

describe("chat suggestion mode", () => {
  it("pre-fills Husk's request, marks it, and becomes a user request when edited", async () => {
    wrap(<ChatPanel automation={automation} selectedNodeId={null} suggestion={sugg({})} />);
    const box = await screen.findByDisplayValue(/notify Purchasing too/);
    expect(screen.getByText("Husk suggestion")).toBeInTheDocument();
    expect(screen.getByText(/Nothing is applied without your confirmation/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send suggestion" })).toBeInTheDocument();
    expect(await screen.findByText(/Procedura_Anomalie_Rev2.md/)).toBeInTheDocument();
    fireEvent.change(box, { target: { value: "Notify Purchasing, please" } });
    expect(screen.getByText(/it will be sent as your own request/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send" })).toBeInTheDocument();
  });

  it("shows stored suggestion messages differently from the user's", async () => {
    routes["/api/v1/agent/chats/automation/auto_1"] = {
      chat: { id: "chat_1", personality: "bilanciato" }, proposals: [], suggestions: [],
      messages: [
        { id: "m1", chat_id: "chat_1", role: "user", content: "Add the notification.", meta: { origin: "suggestion", document_ids: ["doc_1"] }, created_at: "" },
        { id: "m2", chat_id: "chat_1", role: "user", content: "Thanks", meta: {}, created_at: "" },
      ],
    };
    const { container } = wrap(<ChatPanel automation={automation} selectedNodeId={null} />);
    await screen.findByText("Add the notification.");
    expect(container.querySelector(".msg.suggestion-msg")?.textContent).toContain("Husk suggestion");
    expect(screen.getByText("Thanks").closest(".msg")?.className).toContain("user");
  });
});

describe("folder upload", () => {
  const file = (name: string) => new File(["x"], name);
  it("skips system, hidden and unsupported files", () => {
    const { ok, skipped } = classify([
      { file: file("a.pdf"), path: "Docs/a.pdf" }, { file: file(".DS_Store"), path: "Docs/.DS_Store" },
      { file: file("~$b.docx"), path: "Docs/~$b.docx" }, { file: file("c.exe"), path: "Docs/c.exe" },
      { file: file("d.csv"), path: "Docs/.git/d.csv" }, { file: file("e.XLSX"), path: "Docs/Sub/e.XLSX" },
    ]);
    expect(ok.map((p) => p.path)).toEqual(["Docs/a.pdf", "Docs/Sub/e.XLSX"]);
    expect(skipped).toBe(4);
    expect(groupByFolder(ok)).toEqual([["Docs", 1], ["Docs/Sub", 1]]);
    expect(chunks([1, 2, 3, 4, 5], 2)).toEqual([[1, 2], [3, 4], [5]]);
  });

  it("walks dropped folders recursively, reading every batch", async () => {
    const leaf = (name: string): EntryLike => ({ isFile: true, isDirectory: false, name, file: (ok) => ok(file(name)) });
    const dir = (name: string, children: EntryLike[]): EntryLike => {
      let served = false;
      return {
        isFile: false, isDirectory: true, name,
        createReader: () => ({ readEntries: (ok) => { ok(served ? [] : children); served = true; } }),
      };
    };
    const tree = dir("Valsorda", [leaf("profilo.md"), dir("qualita", [leaf("nc.xlsx")])]);
    const picked = await walkEntry(tree);
    expect(picked.map((p) => p.path).sort()).toEqual(["Valsorda/profilo.md", "Valsorda/qualita/nc.xlsx"]);
  });
});
