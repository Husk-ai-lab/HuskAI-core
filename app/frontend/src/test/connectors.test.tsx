import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import en from "@i18n/en.json";
import it_ from "@i18n/it.json";
import type { Connector, Suggestion } from "../api/types";
import { ConnectorModal } from "../components/ConnectorModal";
import { ConnectorsWelcome } from "../components/ConnectorsWelcome";
import { I18nProvider, type Lang } from "../i18n";
import { CONNECTOR_KINDS, GUIDE } from "../lib/connectors";
import { Suggestions } from "../pages/Suggestions";

const CATALOGUES: Record<string, Record<string, string>> = { en, it: it_ };
const conn = (kind: "telegram" | "gmail", over: Partial<Connector> = {}): Connector => ({ kind, state: "not_configured", ...over });

let routes: Record<string, unknown> = {};
let calls: { method: string; path: string; body: any }[] = [];
let failNext: string | null = null;

beforeEach(() => {
  calls = [];
  failNext = null;
  routes = {
    "/api/v1/connectors": [conn("telegram"), conn("gmail")],
    "/api/v1/user/me": { user: { id: "u", org_id: "o" }, settings: { theme: "light", language: "en", personality: "bilanciato", connectors_onboarded: false } },
    "/api/v1/documents": [], "/api/v1/automations": [],
    "/api/v1/suggestions": [], "/api/v1/suggestions/summary": { unread: 0, open: 0, processing: false },
  };
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const path = url.split("?")[0];
    const method = init?.method ?? "GET";
    calls.push({ method, path, body: init?.body ? JSON.parse(String(init.body)) : null });
    if (failNext && path.includes(failNext)) {
      failNext = null;
      return new Response(JSON.stringify({ detail: "Telegram does not accept this bot token.", code: "errors.connector_invalid_token" }), { status: 400 });
    }
    if (method === "POST" && path.endsWith("/detect-chat")) return new Response(JSON.stringify({ chat_id: "4242", label: "Mario" }), { status: 200 });
    if (method === "PUT" || method === "POST" || method === "PATCH" || method === "DELETE") return new Response(JSON.stringify({ ok: true }), { status: 200 });
    return new Response(JSON.stringify(path in routes ? routes[path] : {}), { status: 200 });
  }));
});
afterEach(() => vi.unstubAllGlobals());

function wrap(ui: ReactNode, lang: Lang = "en", route = "/") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const spy = vi.spyOn(qc, "invalidateQueries");
  render(<I18nProvider initial={lang}><QueryClientProvider client={qc}><MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter></QueryClientProvider></I18nProvider>);
  return spy;
}

describe("guides", () => {
  // keys built from the kind and the step number are invisible to the "key exists" guard: check them here
  it.each(["en", "it"])("every step, link, tip and note exists in %s", (lang) => {
    const cat = CATALOGUES[lang];
    for (const kind of CONNECTOR_KINDS) {
      const g = GUIDE[kind];
      const keys = [`connectors.${kind}.title`];
      for (let n = 1; n <= g.steps; n++) keys.push(`connectors.${kind}.step${n}_title`, `connectors.${kind}.step${n}_body`);
      for (const n of Object.keys(g.links)) keys.push(`connectors.${kind}.link${n}`);
      for (let n = 1; n <= g.extraCount; n++) keys.push(`connectors.${kind}.${g.extras}${n}`);
      for (const k of keys) expect(cat[k], `${lang}: ${k}`).toBeTruthy();
      expect(Object.keys(cat).filter((k) => k.startsWith(`connectors.${kind}.step`) && k.endsWith("_title")).length).toBe(g.steps);
    }
  });

  it("the popup shows the guide with its links, then the setup form", () => {
    wrap(<ConnectorModal kind="gmail" onClose={() => {}} />, "it");
    expect(screen.getByRole("dialog", { name: "Gmail" })).toBeInTheDocument();
    expect(screen.getByText("Attiva la verifica in due passaggi")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Apri le password per le app/ })).toHaveAttribute("href", "https://myaccount.google.com/apppasswords");
    fireEvent.click(screen.getByRole("button", { name: "Avanti: configura" }));
    expect(screen.getByLabelText("Indirizzo Gmail")).toBeInTheDocument();
  });
});

describe("first-login popup", () => {
  it("asks to configure the connectors, and a click on Later is remembered for the user", async () => {
    const spy = wrap(<ConnectorsWelcome />);
    expect(await screen.findByRole("dialog", { name: "Configure your connectors!" })).toBeInTheDocument();
    expect(screen.getAllByText("Not set up")).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "Later" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH" && c.body?.connectors_onboarded === true)).toBe(true));
    expect(spy).toHaveBeenCalledWith({ queryKey: ["me"] });
  });

  it("is not shown again once seen, nor when every connector already works", async () => {
    routes["/api/v1/user/me"] = { user: { id: "u" }, settings: { language: "en", connectors_onboarded: true } };
    const first = render(<I18nProvider initial="en"><QueryClientProvider client={new QueryClient()}><ConnectorsWelcome /></QueryClientProvider></I18nProvider>);
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByRole("dialog")).toBeNull();
    first.unmount();
    routes["/api/v1/user/me"] = { user: { id: "u" }, settings: { language: "en", connectors_onboarded: false } };
    routes["/api/v1/connectors"] = [conn("telegram", { state: "ready" }), conn("gmail", { state: "ready" })];
    render(<I18nProvider initial="en"><QueryClientProvider client={new QueryClient()}><ConnectorsWelcome /></QueryClientProvider></I18nProvider>);
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("opens the guide of a connector from the popup", async () => {
    wrap(<ConnectorsWelcome />);
    await screen.findByRole("dialog", { name: "Configure your connectors!" });
    fireEvent.click(screen.getAllByRole("button", { name: "Set up" })[0]);
    expect(await screen.findByRole("link", { name: /Open BotFather/ })).toHaveAttribute("href", "https://t.me/BotFather");
  });
});

describe("setup form", () => {
  it("telegram: detect the chat, save, and the token is never kept in the page", async () => {
    wrap(<ConnectorModal kind="telegram" initialTab="setup" onClose={() => {}} />);
    const token = screen.getByLabelText("Bot token") as HTMLInputElement;
    expect(token.type).toBe("password");
    expect(screen.getByRole("button", { name: "Verify and save" })).toBeDisabled();  // no chat yet
    fireEvent.change(token, { target: { value: "123456789:AAH-secret_bot_token_VALUE" } });
    fireEvent.click(screen.getByRole("button", { name: "Detect my chat" }));
    expect(await screen.findByText("Chat found: Mario")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Verify and save" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")).toBeTruthy());
    expect(calls.find((c) => c.method === "PUT")!.body).toEqual({ token: "123456789:AAH-secret_bot_token_VALUE", chat_id: "4242", label: "Mario" });
    await waitFor(() => expect(token.value).toBe(""));  // emptied after saving
    expect(await screen.findByText("Saved: the connector is ready.")).toBeInTheDocument();
  });

  it("shows why the provider refused the credentials", async () => {
    wrap(<ConnectorModal kind="telegram" initialTab="setup" onClose={() => {}} />);
    fireEvent.change(screen.getByLabelText("Bot token"), { target: { value: "123456789:AAH-secret_bot_token_VALUE" } });
    failNext = "/detect-chat";
    fireEvent.click(screen.getByRole("button", { name: "Detect my chat" }));
    expect(await screen.findByText("Telegram does not accept this bot token.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Verify and save" })).toBeDisabled();
  });

  it("gmail: needs an address and a 16-letter code, and a configured connector offers test and remove", async () => {
    routes["/api/v1/connectors"] = [conn("telegram"), conn("gmail", { state: "ready", hint: "reports@officine.it" })];
    wrap(<ConnectorModal kind="gmail" initialTab="setup" onClose={() => {}} />, "it");
    expect(await screen.findByText("reports@officine.it")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Verifica e salva" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Indirizzo Gmail"), { target: { value: "reports@officine.it" } });
    fireEvent.change(screen.getByLabelText("Password per le app"), { target: { value: "abcd efgh ijkl mnop" } });
    expect(screen.getByRole("button", { name: "Verifica e salva" })).toBeEnabled();
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Invia prova" })); });
    expect(await screen.findByText(/controlla la posta/)).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST" && c.path === "/api/v1/connectors/gmail/test")).toBe(true);
  });
});

describe("suggestion for a connector that is not alive", () => {
  const sugg: Suggestion = {
    id: "sug_c", kind: "configure_connector", status: "new", automation_id: null, connector: "gmail", document_ids: [],
    title: "Set up Gmail so your automations can send", body: "«Weekly report» sends through Gmail, but it is not set up yet.",
    evidence: [], confidence: 1, created_at: "2026-10-06T10:00:00Z", updated_at: "2026-10-06T10:00:00Z",
  };

  function Where() { const l = useLocation(); return <div data-testid="where">{l.pathname + l.search}</div>; }

  it("sends the user to the connector setup in Settings", async () => {
    routes["/api/v1/suggestions"] = [sugg];
    wrap(<><Routes><Route path="*" element={<Suggestions />} /></Routes><Where /></>);
    expect(await screen.findByText("Set up Gmail so your automations can send")).toBeInTheDocument();
    expect(screen.getByText("Connector")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Set up/ }));
    expect(screen.getByTestId("where")).toHaveTextContent("/impostazioni?connector=gmail");
  });

  it("offers to reconnect when the connector stopped working", async () => {
    routes["/api/v1/suggestions"] = [sugg];
    routes["/api/v1/connectors"] = [conn("telegram"), conn("gmail", { state: "error", last_error: "auth_failed" })];
    wrap(<Suggestions />);
    expect(await screen.findByRole("button", { name: /Reconnect/ })).toBeInTheDocument();
  });
});
