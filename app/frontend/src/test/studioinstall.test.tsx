import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { StudioAction } from "../components/StudioAction";
import { I18nProvider } from "../i18n";

const GITHUB = "https://github.com/Husk-ai-lab/husk-ai";

// Studio not running: the system status says so; the manager endpoint answers `queued` as set below.
let queued = false;
let open: ReturnType<typeof vi.fn>;
let calls: { path: string; body?: string }[] = [];

vi.mock("../lib/hooks", async (orig) => ({
  ...(await orig<typeof import("../lib/hooks")>()),
  useSystem: () => ({ data: { studio: { alive: false, install_url: GITHUB } } }),
}));

beforeEach(() => {
  queued = false;
  calls = [];
  open = vi.fn();
  vi.stubGlobal("open", open);
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ path: url, body: typeof init?.body === "string" ? init.body : undefined });
    return new Response(JSON.stringify({ queued, shell_attached: queued, fallback_url: GITHUB }), { status: 200 });
  }));
});
afterEach(() => vi.unstubAllGlobals());

const show = () => render(<I18nProvider initial="en"><StudioAction /></I18nProvider>);

describe("Install Husk Studio", () => {
  it("asks the desktop shell for its installation manager and does not open GitHub", async () => {
    queued = true;
    show();
    fireEvent.click(screen.getByRole("link", { name: /Install Husk Studio/ }));
    await waitFor(() => expect(calls.length).toBe(1));
    expect(calls[0].path).toBe("/api/v1/system/shell/open-manager");
    expect(JSON.parse(calls[0].body!)).toEqual({ tab: "components", component: "studio" });
    await Promise.resolve();
    expect(open).not.toHaveBeenCalled();
  });

  it("falls back to the GitHub page when no shell is attached (browser, older shell)", async () => {
    queued = false;
    show();
    fireEvent.click(screen.getByRole("link", { name: /Install Husk Studio/ }));
    await waitFor(() => expect(open).toHaveBeenCalledWith(GITHUB, "_blank", "noopener,noreferrer"));
  });

  it("keeps a real link for middle-click and accessibility", () => {
    show();
    expect(screen.getByRole("link", { name: /Install Husk Studio/ })).toHaveAttribute("href", GITHUB);
  });
});
