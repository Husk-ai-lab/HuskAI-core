import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AiKeySetup } from "../pages/AiKeySetup";
import { MaintenanceAlert } from "../components/MaintenanceAlert";
import { acknowledge, acknowledged, minutesUntil, nextBlocking, upcoming } from "../lib/maintenance";
import { I18nProvider, type Lang } from "../i18n";
import type { Maintenance, SystemStatus } from "../api/types";

const inMin = (m: number) => new Date(Date.now() + m * 60_000).toISOString();
const win = (id: string, start: number, blocking = true) => ({
  id, message_it: `Aggiornamento ${id}`, message_en: `Update ${id}`, starts_at: inMin(start), ends_at: inMin(start + 60), blocking,
});

function withStatus(m: Partial<Maintenance>, ui: React.ReactNode, lang: Lang = "it") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const sys = { maintenance: { active: false, blocking: false, source: "neon", upcoming: [], ...m }, release: { update_available: false } };
  qc.setQueryData(["system"], sys as unknown as SystemStatus);
  return render(<I18nProvider initial={lang}><QueryClientProvider client={qc}>{ui}</QueryClientProvider></I18nProvider>);
}

describe("maintenance schedule helpers", () => {
  it("filters past windows and finds the next blocking one", () => {
    const m = { upcoming: [win("past", -5), win("soft", 10, false), win("hard", 30)] } as unknown as Maintenance;
    expect(upcoming(m).map((w) => w.id)).toEqual(["soft", "hard"]);
    expect(nextBlocking(m)?.id).toBe("hard");
    expect(minutesUntil(inMin(30))).toBeGreaterThanOrEqual(29);
  });

  it("remembers acknowledgements", () => {
    localStorage.clear();
    acknowledge("w1");
    expect(acknowledged()).toContain("w1");
  });
});

describe("MaintenanceAlert", () => {
  beforeEach(() => localStorage.clear());

  it("shows a scheduled window once and stays dismissed", () => {
    const { unmount } = withStatus({ upcoming: [win("w2", 45)] }, <MaintenanceAlert />);
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    expect(screen.getByText("Aggiornamento w2")).toBeInTheDocument();
    expect(screen.getByText(/Inizia tra/)).toBeInTheDocument();
    fireEvent.click(screen.getByText("Ho capito"));  // Italian UI
    expect(screen.queryByRole("alertdialog")).toBeNull();
    unmount();
    withStatus({ upcoming: [win("w2", 45)] }, <MaintenanceAlert />);
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });

  it("ignores windows more than 24 h away and uses the English message", () => {
    withStatus({ upcoming: [win("far", 60 * 30), win("en", 90)] }, <MaintenanceAlert />, "en");
    expect(screen.getByText("Update en")).toBeInTheDocument();
    expect(screen.getByText("Got it")).toBeInTheDocument();
    expect(screen.queryByText(/far/)).toBeNull();
  });
});

describe("AiKeySetup", () => {
  it("requires a key before verifying and offers logout", () => {
    const logout = vi.fn();
    render(<I18nProvider initial="it"><QueryClientProvider client={new QueryClient()}><AiKeySetup onLogout={logout} /></QueryClientProvider></I18nProvider>);
    const btn = screen.getByRole("button", { name: /Verifica e salva/ });
    expect(btn).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Chiave AI"), { target: { value: "rg-1234567890" } });
    expect(btn).toBeEnabled();
    expect(screen.getByLabelText("Chiave AI")).toHaveAttribute("type", "password");
    fireEvent.click(screen.getByText("Esci"));
    expect(logout).toHaveBeenCalled();
  });

  it("switches the whole screen language and remembers the choice", () => {
    localStorage.clear();
    render(<I18nProvider initial="en"><QueryClientProvider client={new QueryClient()}><AiKeySetup onLogout={() => {}} /></QueryClientProvider></I18nProvider>);
    expect(screen.getByRole("heading", { name: "Connect the AI assistant." })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "Italiano" }));
    expect(screen.getByRole("heading", { name: "Collega l’assistente AI." })).toBeInTheDocument();
    expect(localStorage.getItem("husk.lang")).toBe("it");
    expect(document.documentElement.lang).toBe("it");
  });
});
