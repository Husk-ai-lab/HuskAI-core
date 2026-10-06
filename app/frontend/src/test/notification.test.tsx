import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import en from "@i18n/en.json";
import it_ from "@i18n/it.json";
import type { Automation, Connector } from "../api/types";
import { FlowCanvas } from "../components/FlowCanvas";
import { NotificationPopup } from "../components/NotificationPopup";
import { I18nProvider, type Lang } from "../i18n";
import { CHANNELS, parseRecipients, recipientsOk } from "../lib/notification";

const node = (over: Record<string, unknown> = {}) => ({ id: "notify", kind: "notification", label: "Avvisa Acquisti", position: { x: 300, y: 100 }, channel: "desktop", ...over });
const automation = (n = node()): Automation => ({
  id: "auto_1", title: "Controllo", description: "", status: "bozza", version: 1, source_document_ids: [], hours_per_week: 1, priority: "media", updated_at: "",
  graph: { nodes: [{ id: "trigger", kind: "trigger", label: "Manuale", position: { x: 60, y: 100 }, trigger: "manual" }, n], edges: [{ id: "e1", source: "trigger", target: "notify" }] },
} as unknown as Automation);
const conn = (kind: "telegram" | "gmail", over: Partial<Connector> = {}): Connector => ({ kind, state: "not_configured", ...over });

let routes: Record<string, unknown> = {};
let calls: { method: string; path: string; body: any }[] = [];
beforeEach(() => {
  calls = [];
  routes = {
    "/api/v1/connectors": [conn("telegram", { state: "ready", hint: "@husk_bot" }), conn("gmail")],
    "/api/v1/user/me": { user: { id: "u", org_id: "o", email: "mario@officine.it" }, settings: { language: "en", connectors_onboarded: true } },
  };
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const path = url.split("?")[0];
    const method = init?.method ?? "GET";
    calls.push({ method, path, body: init?.body ? JSON.parse(String(init.body)) : null });
    if (method === "PUT") return new Response(JSON.stringify(automation(node({ channel: "gmail", recipients: "a@b.it" }))), { status: 200 });
    return new Response(JSON.stringify(routes[path] ?? {}), { status: 200 });
  }));
});
afterEach(() => vi.unstubAllGlobals());

const wrap = (ui: React.ReactNode, lang: Lang = "en") =>
  render(<I18nProvider initial={lang}><QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter>{ui}</MemoryRouter></QueryClientProvider></I18nProvider>);

describe("recipients", () => {
  it("splits on commas, semicolons and spaces, de-duplicates, and flags what is not an address", () => {
    expect(parseRecipients("a@x.it; b@y.it  a@x.it, nope")).toEqual([{ value: "a@x.it", valid: true }, { value: "b@y.it", valid: true }, { value: "nope", valid: false }]);
    expect(recipientsOk("a@x.it, b@y.it")).toBe(true);
    expect(recipientsOk("a@x.it, nope")).toBe(false);
    expect(recipientsOk(Array.from({ length: 11 }, (_, i) => `p${i}@x.it`).join(","))).toBe(false);
    expect(recipientsOk("")).toBe(true);
  });
});

describe("the notification node on the canvas", () => {
  it("is drawn in the accent colour with its channel and recipients in a chip", async () => {
    const { container } = wrap(<div style={{ width: 800, height: 400 }}><FlowCanvas automation={automation(node({ channel: "gmail", recipients: "boss@cliente.it" }))} selected={null} onSelect={() => {}} /></div>);
    // React Flow keeps nodes `visibility: hidden` until it can measure them (jsdom cannot), so query the DOM directly
    await waitFor(() => expect(container.querySelector(".notif-chip")).toBeTruthy());
    const chip = container.querySelector(".notif-chip") as HTMLElement;
    expect(chip.getAttribute("aria-label")).toBe("Change how this notification is sent: Avvisa Acquisti");
    expect(container.querySelector(".notif-node")).toBeTruthy();
    expect(chip.textContent).toContain("Gmail · boss@cliente.it");
  });

  it("clicking the chip opens the popup and does not select the node", async () => {
    const onSelect = vi.fn();
    const { container } = wrap(<div style={{ width: 800, height: 400 }}><FlowCanvas automation={automation()} selected={null} onSelect={onSelect} /></div>);
    await waitFor(() => expect(container.querySelector(".notif-chip")).toBeTruthy());
    fireEvent.click(container.querySelector(".notif-chip") as HTMLElement);
    expect(await screen.findByRole("dialog", { name: "Notification" })).toBeInTheDocument();
    expect(onSelect).not.toHaveBeenCalled();
  });
});

describe("the notification popup", () => {
  it("offers the three channels with their state, and Gmail asks for recipients", async () => {
    wrap(<NotificationPopup automation={automation()} node={automation().graph.nodes[1]} onClose={() => {}} />);
    const radios = await screen.findAllByRole("radio");
    expect(radios).toHaveLength(CHANNELS.length);
    expect(radios[0]).toHaveAttribute("aria-checked", "true");  // desktop
    expect(await screen.findByText("@husk_bot")).toBeInTheDocument();  // Telegram, set up
    expect(screen.queryByLabelText("To")).toBeNull();
    fireEvent.click(radios[2]);  // Gmail: not set up, can still be chosen
    expect(screen.getByLabelText("To")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Set up" })).toBeInTheDocument();
  });

  it("validates the recipients, can add my own address, and saves through the edit endpoint", async () => {
    const onClose = vi.fn();
    wrap(<NotificationPopup automation={automation()} node={automation().graph.nodes[1]} onClose={onClose} />);
    fireEvent.click((await screen.findAllByRole("radio"))[2]);
    const to = screen.getByLabelText("To");
    const save = screen.getByRole("button", { name: "Save" });
    fireEvent.change(to, { target: { value: "boss@cliente.it, nope" } });
    expect(save).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Add my address (mario@officine.it)" }));
    fireEvent.change(to, { target: { value: "boss@cliente.it" } });
    fireEvent.click(await screen.findByRole("button", { name: "Add my address (mario@officine.it)" }));
    expect((to as HTMLInputElement).value).toBe("boss@cliente.it, mario@officine.it");
    fireEvent.change(screen.getByLabelText("Subject"), { target: { value: "Esito" } });
    fireEvent.click(screen.getByLabelText(/Attach the PDF report/));
    fireEvent.click(save);
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    const put = calls.find((c) => c.method === "PUT")!;
    expect(put.path).toBe("/api/v1/automations/auto_1/nodes/notify/notification");
    expect(put.body).toEqual({ channel: "gmail", recipients: "boss@cliente.it, mario@officine.it", subject: "Esito", template: null, attach_report: true });
  });

  it("a desktop notification sends no recipients and no attachment", async () => {
    wrap(<NotificationPopup automation={automation(node({ channel: "gmail", recipients: "a@b.it", attach_report: true }))} node={automation(node({ channel: "gmail", recipients: "a@b.it", attach_report: true })).graph.nodes[1]} onClose={() => {}} />);
    fireEvent.click((await screen.findAllByRole("radio"))[0]);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(calls.find((c) => c.method === "PUT")!.body).toMatchObject({ channel: "desktop", recipients: null, attach_report: false });
  });
});

describe("copy", () => {
  it.each(["en", "it"])("every notification key exists in %s", (lang) => {
    const cat: Record<string, string> = lang === "en" ? en : it_;
    for (const c of CHANNELS) expect(cat[`notification.channel_${c}`], `${lang}: ${c}`).toBeTruthy();
    for (const k of ["kind", "chip_title", "no_recipients", "popup_title", "popup_subtitle", "recipients", "subject", "message", "attach_report", "save"]) expect(cat[`notification.${k}`], `${lang}: ${k}`).toBeTruthy();
  });
});
