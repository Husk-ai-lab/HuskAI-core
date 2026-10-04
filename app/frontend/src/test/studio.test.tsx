import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, vi } from "vitest";
import type { Automation, StudioStatus } from "../api/types";
import { ChatPanel } from "../components/ChatPanel";
import { DiagnoseButton, StudioAction } from "../components/StudioAction";
import { StudioAdviceCard, StudioCard } from "../components/StudioCard";
import { I18nProvider } from "../i18n";
import { NotificationsProvider } from "../lib/notifications";
import { STUDIO_INSTALL_URL, studioTarget } from "../lib/studio";

const TRACE = "0af7651916cd43dd8448eb211c80319c";
const OFF: StudioStatus = { alive: false, otlp: false, mcp: false, mcp_tools: [], install_url: STUDIO_INSTALL_URL };
const ON: StudioStatus = {
  alive: true, ui_url: "http://127.0.0.1:7654", otlp: true, mcp: true, mcp_tools: ["explain_failure"],
  install_url: STUDIO_INSTALL_URL, run_url_template: "{base}/runs/{trace_id}",
};
const automation = { id: "auto_1", title: "BOM check", description: "", status: "bozza", version: 1,
  graph: { nodes: [], edges: [] }, source_document_ids: [], hours_per_week: 2, priority: "media" } as unknown as Automation;

let studio: StudioStatus = OFF;
let chatView: unknown;
const posts: unknown[] = [];

beforeEach(() => {
  studio = OFF;
  posts.length = 0;
  chatView = { chat: { id: "chat_1", personality: "bilanciato" }, messages: [], proposals: [], suggestions: [] };
  try { localStorage.clear(); } catch { /* ignore */ }
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "POST") posts.push(init.body ? JSON.parse(String(init.body)) : null);
    const path = url.split("?")[0];
    const body = path === "/api/v1/system/status" ? { studio, maintenance: {}, release: {} }
      : path === "/api/v1/agent/chats/automation/auto_1" ? chatView
      : path === "/api/v1/documents" || path === "/api/v1/agent/personalities" ? [] : {};
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
});
afterEach(() => vi.unstubAllGlobals());

function wrap(ui: ReactNode) {
  return render(
    <I18nProvider initial="en">
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter><NotificationsProvider>{ui}</NotificationsProvider></MemoryRouter>
      </QueryClientProvider>
    </I18nProvider>,
  );
}

describe("studio target", () => {
  it("links the run, Studio's home, or the install page", () => {
    expect(studioTarget(ON, TRACE, true)).toEqual({ kind: "run", url: `http://127.0.0.1:7654/runs/${TRACE}` });
    expect(studioTarget(ON, TRACE, false)).toEqual({ kind: "home", url: "http://127.0.0.1:7654" });
    expect(studioTarget(OFF, TRACE, true)).toEqual({ kind: "install", url: STUDIO_INSTALL_URL });
    expect(studioTarget(undefined).kind).toBe("install");
  });
});

describe("StudioAction", () => {
  it("offers the install link when Studio is not running", async () => {
    wrap(<StudioAction traceId={TRACE} traced />);
    expect(await screen.findByRole("link", { name: /Install Husk Studio/ })).toHaveAttribute("href", STUDIO_INSTALL_URL);
  });

  it("opens the run when Studio is running", async () => {
    studio = ON;
    wrap(<StudioAction traceId={TRACE} traced />);
    expect(await screen.findByRole("link", { name: /View in Husk Studio/ })).toHaveAttribute("href", `http://127.0.0.1:7654/runs/${TRACE}`);
  });

  it("hides Diagnose without Studio's MCP", async () => {
    wrap(<DiagnoseButton onClick={() => undefined} />);
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByRole("button", { name: /Diagnose/ })).toBeNull();
  });

  it("shows Diagnose with Studio's MCP", async () => {
    studio = ON;
    wrap(<DiagnoseButton onClick={() => undefined} />);
    expect(await screen.findByRole("button", { name: /Diagnose with Husk Studio/ })).toBeInTheDocument();
  });
});

describe("advice cards", () => {
  it("sidebar card: install when off, open when on, collapsible", async () => {
    const { unmount } = wrap(<StudioCard />);
    expect(await screen.findByRole("link", { name: /Install Husk Studio/ })).toHaveAttribute("href", STUDIO_INSTALL_URL);
    fireEvent.click(screen.getByRole("button", { name: "Collapse" }));
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByText("Husk Studio")).toBeInTheDocument(); // collapsed, never hidden
    unmount();
    studio = ON;
    try { localStorage.clear(); } catch { /* ignore */ }
    wrap(<StudioCard />);
    expect(await screen.findByRole("link", { name: /Open Husk Studio/ })).toHaveAttribute("href", "http://127.0.0.1:7654");
  });

  it("suggestions card hides for 30 days", async () => {
    const { unmount } = wrap(<StudioAdviceCard />);
    expect(await screen.findByText("Husk Studio is recommended for your work")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Hide for 30 days" }));
    expect(screen.queryByText("Husk Studio is recommended for your work")).toBeNull();
    unmount();
    wrap(<StudioAdviceCard />);
    expect(screen.queryByText("Husk Studio is recommended for your work")).toBeNull();
  });
});

describe("chat self-heal", () => {
  it("pre-fills a marked request from a failed test and renders self-heal bubbles", async () => {
    chatView = {
      chat: { id: "chat_1", personality: "bilanciato" }, proposals: [], suggestions: [],
      messages: [{ id: "m1", chat_id: "chat_1", role: "user", content: "Diagnose it.", meta: { origin: "self_heal", trace_id: TRACE }, created_at: "" }],
    };
    const { container } = wrap(
      <ChatPanel automation={automation} selectedNodeId={null} selfHeal={{ traceId: TRACE, prompt: "The test run failed at «Analysis»: diagnose it.", nonce: 1 }} />,
    );
    expect(await screen.findByDisplayValue(/failed at «Analysis»/)).toBeInTheDocument();
    expect(screen.getAllByText("Husk Studio diagnosis").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Send diagnosis request" })).toBeInTheDocument();
    await screen.findByText("Diagnose it.");
    expect(container.querySelector(".msg.studio-msg")).not.toBeNull();
  });

  it("error bubbles always offer a Studio action", async () => {
    chatView = {
      chat: { id: "chat_1", personality: "bilanciato" }, proposals: [], suggestions: [],
      messages: [{ id: "e1", chat_id: "chat_1", role: "assistant", content: "I couldn't complete the request.", meta: { error: true, trace_id: TRACE, traced: false }, created_at: "" }],
    };
    wrap(<ChatPanel automation={automation} selectedNodeId={null} />);
    expect(await screen.findByRole("link", { name: /Install Husk Studio/ })).toBeInTheDocument();
  });
});
