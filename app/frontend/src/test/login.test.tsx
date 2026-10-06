import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SystemStatus } from "../api/types";
import { I18nProvider, type Lang } from "../i18n";
import { ThemeProvider } from "../lib/theme";
import { AuthDone } from "../pages/AuthDone";
import { Login } from "../pages/Login";
import { NotEntitled } from "../pages/NotEntitled";

const SYS = { login_configured: true, maintenance: { active: false, blocking: false, source: "none", upcoming: [] }, release: { update_available: false } };

function stubApi(polls: Array<{ status: string; reason?: string }>) {
  const calls: string[] = [];
  const fetchMock = vi.fn(async (path: string) => {
    calls.push(path);
    const body = path.endsWith("/auth/login")
      ? { url: "https://accounts.example/oauth/authorize?x=1", poll_id: "p".repeat(40) }
      : path.endsWith("/auth/poll")
        ? polls.shift() ?? { status: "pending" }
        : SYS;  // the system-status query that Login also mounts
    return { ok: true, status: 200, statusText: "OK", json: async () => body } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

function renderLogin(lang: Lang = "en") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(["system"], SYS as unknown as SystemStatus);
  const spy = vi.spyOn(qc, "invalidateQueries");
  render(
    <I18nProvider initial={lang}><QueryClientProvider client={qc}><ThemeProvider><MemoryRouter><Login /></MemoryRouter></ThemeProvider></QueryClientProvider></I18nProvider>,
  );
  return spy;
}

const tick = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });

describe("Login (system browser + poll)", () => {
  beforeEach(() => { vi.useFakeTimers(); localStorage.clear(); });
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

  it("starts the login, waits, and refreshes the session once the browser has finished", async () => {
    const calls = stubApi([{ status: "pending" }, { status: "ok" }]);
    const invalidate = renderLogin();
    expect(screen.queryByLabelText(/email/i)).toBeNull(); // no email field any more
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Continue with your Husk account" })); });
    expect(screen.getByText("Finish signing in in your browser")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open the sign-in page again" })).toHaveAttribute("href", "https://accounts.example/oauth/authorize?x=1");
    await tick(1600);
    expect(invalidate).not.toHaveBeenCalled();
    await tick(1600);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["me"] });
    expect(calls.filter((c) => c.endsWith("/auth/poll")).length).toBeGreaterThanOrEqual(2);
  });

  it("explains a missing organisation and lets the user try again", async () => {
    stubApi([{ status: "error", reason: "no_org" }]);
    renderLogin("it");
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Continua con il tuo account Husk" })); });
    await tick(1600);
    expect(screen.getByText(/non appartiene ancora a un'organizzazione/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Continua con il tuo account Husk" })).toBeInTheDocument();
  });

  it("cancel goes back to the start", async () => {
    stubApi([]);
    renderLogin();
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Continue with your Husk account" })); });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("button", { name: "Continue with your Husk account" })).toBeInTheDocument();
  });
});

describe("AuthDone (the page the system browser lands on)", () => {
  const page = (query: string, lang: Lang) =>
    render(<I18nProvider initial={lang}><ThemeProvider><MemoryRouter initialEntries={[`/auth/done${query}`]}><AuthDone /></MemoryRouter></ThemeProvider></I18nProvider>);

  it("says it is authenticated and the tab can be closed, in both languages", () => {
    const { unmount } = page("?status=ok", "en");
    expect(screen.getByText("Authenticated. You can close this tab and go back to Husk.")).toBeInTheDocument();
    unmount();
    page("?status=ok", "it");
    expect(screen.getByText("Autenticato. Puoi chiudere questa scheda e tornare a Husk.")).toBeInTheDocument();
  });

  it("reports why it did not complete", () => {
    page("?status=error&reason=no_org", "en");
    expect(screen.getByText("Sign-in did not complete")).toBeInTheDocument();
    expect(screen.getByText(/does not belong to an organisation/)).toBeInTheDocument();
  });
});

describe("NotEntitled (gateway says the installation is not active)", () => {
  const show = (status: string, lang: Lang, onLogout = vi.fn()) => {
    const qc = new QueryClient();
    const spy = vi.spyOn(qc, "invalidateQueries");
    render(<I18nProvider initial={lang}><QueryClientProvider client={qc}><NotEntitled status={status} onLogout={onLogout} /></QueryClientProvider></I18nProvider>);
    return { spy, onLogout };
  };

  it("explains the state in the user's language, keeps local data safe and can re-check or sign out", () => {
    const { spy, onLogout } = show("none", "it");
    expect(screen.getByRole("heading", { name: "Questa installazione non è ancora attiva" })).toBeInTheDocument();
    expect(screen.getByText(/restano su questo computer/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Controlla di nuovo" }));
    expect(spy).toHaveBeenCalledWith({ queryKey: ["entitlement"] });
    fireEvent.click(screen.getByRole("button", { name: "Esci" }));
    expect(onLogout).toHaveBeenCalled();
  });

  it("tells a suspended organisation apart", () => {
    show("suspended", "en");
    expect(screen.getByText(/access is suspended/)).toBeInTheDocument();
  });
});
