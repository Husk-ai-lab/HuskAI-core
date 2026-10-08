import { describe, expect, it } from "vitest";
import type { Automation } from "../api/types";
import { toFlow } from "../components/FlowCanvas";

const auto = {
  id: "auto_1", title: "T", description: "", status: "bozza", version: 1, source_document_ids: [], hours_per_week: 1, priority: "media", updated_at: "",
  graph: {
    nodes: [
      { id: "trigger", kind: "trigger", label: "Manuale", position: { x: 60, y: 100 }, trigger: "manual" },
      { id: "ag", kind: "agent", label: "Analizza", position: { x: 300, y: 100 }, instructions: "x", attachments: [
        { id: "a1", role: "memoria", label: "Doc 1", document_ids: [] }, { id: "a2", role: "modello", label: "Doc 2", document_ids: [] }] },
    ],
    edges: [{ id: "e1", source: "trigger", target: "ag" }],
  },
} as unknown as Automation;

describe("dragging nodes (a view only)", () => {
  it("every step is draggable, the attachment bubbles are not", () => {
    const { nodes } = toFlow(auto, null, "en", () => {});
    expect(nodes.filter((n) => !n.id.includes("::")).every((n) => n.draggable)).toBe(true);
    expect(nodes.filter((n) => n.id.includes("::")).every((n) => n.draggable === false)).toBe(true);
  });

  it("without a drag the stored positions are used", () => {
    const { nodes } = toFlow(auto, null, "en", () => {});
    expect(nodes.find((n) => n.id === "ag")!.position).toEqual({ x: 300, y: 100 });
  });

  it("a dragged position overrides the stored one and the attachments follow their agent", () => {
    const base = toFlow(auto, null, "en", () => {}).nodes;
    const moved = toFlow(auto, null, "en", () => {}, { ag: { x: 500, y: 400 } }).nodes;
    expect(moved.find((n) => n.id === "ag")!.position).toEqual({ x: 500, y: 400 });
    expect(moved.find((n) => n.id === "trigger")!.position).toEqual({ x: 60, y: 100 });
    const a1 = base.find((n) => n.id === "ag::a1")!.position, b1 = moved.find((n) => n.id === "ag::a1")!.position;
    expect(b1).toEqual({ x: a1.x + 200, y: a1.y + 300 });
    expect(auto.graph.nodes[1].position).toEqual({ x: 300, y: 100 });  // the automation itself is untouched
  });
});
