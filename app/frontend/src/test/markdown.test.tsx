import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Automation } from "../api/types";
import { ChatPanel } from "../components/ChatPanel";
import { Markdown } from "../components/Markdown";
import { I18nProvider } from "../i18n";

const SAMPLE = [
  "### Plan", "", "I will do two things:", "", "1. Add a **Telegram** notification", "2. Keep the *PDF* report", "",
  "- first point", "- second point", "", "> a quoted note", "", "```", "const x = 1;", "<script>alert(1)</script>", "```", "",
  "See [the guide](https://example.com/guide) and [bad](javascript:alert(1)) and `node_id`.",
].join("\n");

describe("Markdown", () => {
  it("renders headings, numbered and bullet lists, quotes, code, bold, italic and https links", () => {
    const { container } = render(<Markdown text={SAMPLE} variant="chat" />);
    expect(container.querySelector(".md.chat")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Plan" }).tagName).toBe("H3");
    expect(container.querySelectorAll("ol > li")).toHaveLength(2);
    expect(container.querySelectorAll("ul > li")).toHaveLength(2);
    expect(container.querySelector("blockquote")?.textContent).toBe("a quoted note");
    expect(container.querySelector("b")?.textContent).toBe("Telegram");
    expect(container.querySelector("i")?.textContent).toBe("PDF");
    expect(container.querySelector("p code")?.textContent).toBe("node_id");
    const link = screen.getByRole("link", { name: "the guide" });
    expect(link).toHaveAttribute("href", "https://example.com/guide");
    expect(link).toHaveAttribute("rel", "noreferrer noopener");
    expect(link).toHaveAttribute("target", "_blank");
  });

  it("never turns text into HTML or an unsafe link", () => {
    const { container } = render(<Markdown text={SAMPLE} />);
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("pre code")?.textContent).toContain("<script>alert(1)</script>");  // shown as text
    expect(screen.queryByRole("link", { name: "bad" })).toBeNull();
    expect(container.textContent).toContain("[bad](javascript:alert(1))");  // stays visible as plain text
    expect(container.querySelectorAll("a")).toHaveLength(1);
  });

  it("keeps the old behaviour: note ids as code, underscores inside words are not italics", () => {
    const { container } = render(<Markdown text={"# Title\n- **uno** [[note_1]] and add_node_x"} />);
    expect(container.querySelector("h1")?.textContent).toBe("Title");
    expect(container.querySelector("code")?.textContent).toBe("note_1");
    expect(container.querySelector("li")?.textContent).toContain("add_node_x");
    expect(container.querySelector("li i")).toBeNull();
  });
});

describe("side chat", () => {
  const automation = { id: "auto_1", title: "T", description: "", status: "bozza", version: 1, graph: { nodes: [], edges: [] }, source_document_ids: [] } as unknown as Automation;
  beforeEach(() => {
    const routes: Record<string, unknown> = {
      "/api/v1/agent/chats/automation/auto_1": {
        chat: { id: "chat_1", personality: "bilanciato" }, proposals: [], suggestions: [],
        messages: [
          { id: "m1", chat_id: "chat_1", role: "assistant", content: "Done:\n- added **Telegram**\n- kept the report", meta: {}, created_at: "" },
          { id: "m2", chat_id: "chat_1", role: "user", content: "please keep **this** literal", meta: {}, created_at: "" },
          { id: "m3", chat_id: "chat_1", role: "assistant", content: "boom **not formatted**", meta: { error: true }, created_at: "" },
        ],
      },
      "/api/v1/agent/personalities": [], "/api/v1/documents": [],
    };
    vi.stubGlobal("fetch", vi.fn(async (url: string) => new Response(JSON.stringify(routes[url.split("?")[0]] ?? {}), { status: 200 })));
  });
  afterEach(() => vi.unstubAllGlobals());

  it("formats the assistant's messages, but leaves the user's own words and error texts as typed", async () => {
    const { container } = render(
      <I18nProvider initial="en"><QueryClientProvider client={new QueryClient()}><MemoryRouter><ChatPanel automation={automation} selectedNodeId={null} /></MemoryRouter></QueryClientProvider></I18nProvider>,
    );
    await screen.findByText("kept the report");
    expect(container.querySelectorAll(".msg .md.chat ul > li")).toHaveLength(2);
    expect(container.querySelector(".msg .md.chat b")?.textContent).toBe("Telegram");
    expect(screen.getByText("please keep **this** literal")).toBeInTheDocument();  // user message: plain
    expect(screen.getByText("boom **not formatted**")).toBeInTheDocument();  // error message: plain
  });
});
