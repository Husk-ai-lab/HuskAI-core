import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConfirmModal } from "../components/ConfirmModal";
import { I18nProvider } from "../i18n";
import { Settings } from "../pages/Settings";

const ME = {
  user: { id: "u", org_id: "o", email: "op@example.com", display_name: "Operatore", org_name: "Officine", roles: [] },
  settings: { theme: "light", language: "en", personality: "professionale" },
};

const wrap = (ui: React.ReactNode) =>
  render(<I18nProvider initial="en"><QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter>{ui}</MemoryRouter></QueryClientProvider></I18nProvider>);

describe("ConfirmModal", () => {
  it("asks first and only acts on the button", () => {
    const onConfirm = vi.fn();
    const onClose = vi.fn();
    wrap(<ConfirmModal title="Import" body="Replace everything?" onConfirm={onConfirm} onClose={onClose} />);
    expect(screen.getByText("Replace everything?")).toBeInTheDocument();
    expect(onConfirm).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(onConfirm).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalledOnce();
  });
});

describe("Settings: backup export", () => {
  let calls: { method: string; path: string; body?: string }[] = [];
  let exportStatus = 200;
  beforeEach(() => {
    calls = [];
    exportStatus = 200;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      const path = url.split("?")[0];
      const method = init?.method ?? "GET";
      calls.push({ method, path, body: typeof init?.body === "string" ? init.body : undefined });
      const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status });
      if (path === "/api/v1/user/me") return json(ME);
      if (path === "/api/v1/user/data/export" && method === "POST") {
        return exportStatus === 200
          ? json({ name: "husk-export-2026-10-14-124944.db", path: "/data/husk/exports/husk-export-2026-10-14-124944.db", size_bytes: 10 })
          : json({ detail: "No space left", code: "errors.export_failed" }, exportStatus);
      }
      if (path === "/api/v1/user/data/export/reveal") return json({ ok: true });
      if (path === "/api/v1/system/status") return json({ data_dir: "/data/husk", studio: { alive: false, mcp: false }, maintenance: { blocking: false, active: false } });
      if (path === "/api/v1/user/usage") return json({ tokens_in: 0, tokens_out: 0, calls: 0, rows: [] });
      return json([]);
    }));
  });
  afterEach(() => vi.unstubAllGlobals());

  it("saves into the data folder, shows where, and reveals the file", async () => {
    wrap(<Settings />);
    fireEvent.click((await screen.findAllByRole("button", { name: /^Export$/ }))[0]);
    expect(await screen.findByText(/Backup saved: \/data\/husk\/exports\/husk-export-2026-10-14-124944\.db/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Show in folder/ }));
    await waitFor(() => expect(calls.some((c) => c.path === "/api/v1/user/data/export/reveal")).toBe(true));
    const reveal = calls.find((c) => c.path === "/api/v1/user/data/export/reveal")!;
    expect(JSON.parse(reveal.body!)).toEqual({ name: "husk-export-2026-10-14-124944.db" });
  });

  it("says so when the backup could not be written", async () => {
    exportStatus = 500;
    wrap(<Settings />);
    fireEvent.click((await screen.findAllByRole("button", { name: /^Export$/ }))[0]);
    expect(await screen.findByText("No space left")).toBeInTheDocument();
    expect(screen.queryByText(/Backup saved/)).not.toBeInTheDocument();
  });
});
