/**
 * Flow canvas. The structure is the assistant's to change; the only thing a user sets by hand is how a notification
 * node delivers (click its chip).
 * Designed from scratch for Husk on top of React Flow (@xyflow/react, MIT).
 */
import {
  Background, BackgroundVariant, Handle, Position, ReactFlow, ReactFlowProvider, useReactFlow, useViewport,
  type Edge, type Node, type NodeChange, type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Minus, Plus } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { Automation, FlowNode } from "../api/types";
import { translate, useI18n, type Lang } from "../i18n";
import { attachmentIcon, nodeIcon } from "./icons";
import { NotificationFlowNode } from "./NotificationNode";
import { NotificationPopup } from "./NotificationPopup";

type MainData = { node: FlowNode; selected: boolean };
type AttData = { label: string; subtitle?: string | null; role: string };

function subtitleFor(n: FlowNode, lang: Lang): string | undefined {
  if (n.subtitle) return n.subtitle;
  if (n.kind === "trigger")
    return n.trigger === "schedule" ? `cron · ${n.cron ?? ""}` : n.trigger === "file" ? `on file · ${n.folder ?? translate(lang, "editor.folder")}` : translate(lang, "editor.manual");
  if (n.kind === "condition") return `if · ${n.field} ${n.op} ${n.value}`;
  if (n.kind === "decision") return `${translate(lang, "editor.choice")} · ${(n.options ?? []).join(" / ")}`;
  if (n.kind === "action") return `${n.action}${n.target ? `: ${n.target}` : ""}`;
  return undefined;
}

function TileNode({ data }: NodeProps<Node<MainData>>) {
  const n = data.node;
  const { lang } = useI18n();
  return (
    <div className={`flow-node ${data.selected ? "selected" : ""}`}>
      <Handle type="target" position={Position.Left} style={{ top: 29 }} />
      <div className="tile">{nodeIcon(n)}</div>
      <div className="lbl">{n.label}</div>
      <div className="sub">{subtitleFor(n, lang)}</div>
      <Handle type="source" position={Position.Right} style={{ top: 29 }} />
    </div>
  );
}

function AgentNode({ data }: NodeProps<Node<MainData>>) {
  const n = data.node;
  const { t } = useI18n();
  return (
    <div>
      <div className={`agent-card ${data.selected ? "selected" : ""}`}>
        <Handle type="target" position={Position.Left} />
        <span className="ico">{nodeIcon(n, 17)}</span>
        <div><small>{t("editor.ai_agent")}</small><b>{n.label}</b></div>
        <Handle type="source" position={Position.Right} />
      </div>
      <div className="agent-ports"><span>{t("enums.attachment_role.modello")}*</span><span>{t("enums.attachment_role.memoria")}</span><span>{t("enums.attachment_role.strumento")}</span></div>
      <Handle type="source" id="att" position={Position.Bottom} style={{ bottom: 12, opacity: 0 }} />
    </div>
  );
}

function AttNode({ data }: NodeProps<Node<AttData>>) {
  const { t } = useI18n();
  return (
    <div className="att-node">
      <Handle type="target" position={Position.Top} style={{ opacity: 0 }} />
      <div className="circle">{attachmentIcon(data.role)}</div>
      <div className="lbl">{data.label}</div>
      <div className="sub">{data.subtitle ?? t(`enums.attachment_role.${data.role}`)}</div>
    </div>
  );
}

const nodeTypes = { tile: TileNode, agent: AgentNode, att: AttNode, notification: NotificationFlowNode };

/** "vero"/"falso" are stored ids; decision options are free text shown as written. */
function branchLabel(branch: string, lang: Lang): string {
  const key = `enums.branch.${branch}`;
  const label = translate(lang, key);
  return label === key ? branch : label;
}

/** Where a node sits while someone drags it. Only a view: nothing is saved, the stored layout is the assistant's. */
export type Moved = Record<string, { x: number; y: number }>;

export function toFlow(a: Automation, selected: string | null, lang: Lang, onEdit: (id: string) => void, moved: Moved = {}): { nodes: Node[]; edges: Edge[] } {
  const nodes: Node[] = [];
  const edges: Edge[] = [];
  for (const n of a.graph.nodes) {
    const at = moved[n.id] ?? n.position;
    nodes.push({
      id: n.id, type: n.kind === "agent" ? "agent" : n.kind === "notification" ? "notification" : "tile", position: at,
      data: { node: n, selected: selected === n.id, onEdit }, draggable: true,
    });
    if (n.kind === "agent") {
      const atts = n.attachments ?? [];
      atts.forEach((att, i) => {
        const id = `${n.id}::${att.id}`;
        nodes.push({
          id, type: "att", draggable: false, selectable: false,  // they follow their agent
          position: { x: at.x - 20 + (i - (atts.length - 1) / 2) * 96, y: at.y + 150 },
          data: { label: att.label, subtitle: att.subtitle, role: att.role },
        });
        edges.push({ id: `e-${id}`, source: n.id, sourceHandle: "att", target: id, type: "default", style: { stroke: "var(--border-strong)", strokeDasharray: "3 4" } });
      });
    }
  }
  for (const e of a.graph.edges) {
    edges.push({
      id: e.id, source: e.source, target: e.target, type: "default",
      label: e.branch ? branchLabel(e.branch, lang) : undefined,
      style: { stroke: "var(--accent-2)", strokeDasharray: "4 4", strokeWidth: 1.4 },
    });
  }
  return { nodes, edges };
}

function ZoomPill({ onReset }: { onReset?: () => void }) {
  const { zoomIn, zoomOut, fitView } = useReactFlow();
  const { t } = useI18n();
  const { zoom } = useViewport();
  return (
    <div className="zoom-pill">
      <button onClick={() => zoomOut()} aria-label={t("editor.zoom_out")}><Minus size={14} /></button>
      <span className="pct">{Math.round(zoom * 100)}%</span>
      <button onClick={() => zoomIn()} aria-label={t("editor.zoom_in")}><Plus size={14} /></button>
      <span className="sep" />
      <button onClick={() => fitView({ padding: 0.2, duration: 300 })}>{t("editor.fit")}</button>
      {onReset && <><span className="sep" /><button onClick={onReset}>{t("editor.reset_layout")}</button></>}
    </div>
  );
}

function Inner({ automation, selected, onSelect }: { automation: Automation; selected: string | null; onSelect: (id: string | null) => void }) {
  const { lang } = useI18n();
  const [editing, setEditing] = useState<string | null>(null);
  const onEdit = useCallback((id: string) => setEditing(id), []);
  const [moved, setMoved] = useState<Moved>({});
  useEffect(() => setMoved({}), [automation.id, automation.version]);  // a new version brings its own layout
  const { nodes, edges } = useMemo(() => toFlow(automation, selected, lang, onEdit, moved), [automation, selected, lang, onEdit, moved]);
  const onNodesChange = useCallback((changes: NodeChange[]) => {
    const drags = changes.filter((c): c is Extract<NodeChange, { type: "position" }> => c.type === "position" && !!c.position && !c.id.includes("::"));
    if (drags.length) setMoved((m) => ({ ...m, ...Object.fromEntries(drags.map((c) => [c.id, c.position!])) }));
  }, []);
  const editingNode = automation.graph.nodes.find((n) => n.id === editing && n.kind === "notification");
  const { fitView } = useReactFlow();
  useEffect(() => {
    const t = setTimeout(() => fitView({ padding: 0.2 }), 50);
    return () => clearTimeout(t);
  }, [automation.version, fitView]);
  return (
    <>
    <ReactFlow
      nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView fitViewOptions={{ padding: 0.2 }}
      nodesDraggable nodeDragThreshold={3} onNodesChange={onNodesChange} nodesConnectable={false} edgesFocusable={false} deleteKeyCode={null}
      minZoom={0.3} maxZoom={1.6} proOptions={{ hideAttribution: true }}
      onNodeClick={(_, n) => !n.id.includes("::") && onSelect(n.id === selected ? null : n.id)}
      onPaneClick={() => onSelect(null)}
    >
      <Background variant={BackgroundVariant.Dots} gap={16} size={1.8} color="var(--dot)" />
      <ZoomPill onReset={Object.keys(moved).length ? () => { setMoved({}); setTimeout(() => fitView({ padding: 0.2, duration: 300 }), 50); } : undefined} />
    </ReactFlow>
    {editingNode && <NotificationPopup automation={automation} node={editingNode} onClose={() => setEditing(null)} />}
    </>
  );
}

export function FlowCanvas(props: { automation: Automation; selected: string | null; onSelect: (id: string | null) => void }) {
  const { t } = useI18n();
  const main = props.automation.graph.nodes.length;
  const total = main + props.automation.graph.nodes.reduce((s, n) => s + (n.attachments?.length ?? 0), 0);
  return (
    <div className="canvas-wrap">
      <span className="canvas-chip">{t("editor.node_count", { count: total })}</span>
      <ReactFlowProvider>
        <Inner {...props} />
      </ReactFlowProvider>
    </div>
  );
}
