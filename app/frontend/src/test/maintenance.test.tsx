import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
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
