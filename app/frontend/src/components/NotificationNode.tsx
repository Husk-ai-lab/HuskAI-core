import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { ChevronDown } from "lucide-react";
import type { FlowNode } from "../api/types";
import { useT } from "../i18n";
import { useConnectors } from "../lib/connectors";
import { channelIcon } from "./icons";

export type NotificationData = { node: FlowNode; selected: boolean; onEdit: (nodeId: string) => void };

/** The notification node: filled with the accent colour so the places where Husk tells someone something stand out.
 *  The chip is the "connector": a click opens the popup to choose the channel and, for email, the recipients. */
export function NotificationFlowNode({ data }: NodeProps<Node<NotificationData>>) {
  const n = data.node;
  const t = useT();
  const { data: connectors } = useConnectors();
  const channel = n.channel ?? "desktop";
  const state = channel === "desktop" ? "ready" : connectors?.find((c) => c.kind === channel)?.state ?? "not_configured";
  const to = channel === "gmail" ? (n.recipients?.trim() || t("notification.no_recipients")) : null;
  return (
    <div className={`notif-node ${data.selected ? "selected" : ""}`}>
      <Handle type="target" position={Position.Left} />
      <small>{t("notification.kind")}</small>
      <b>{n.label}</b>
      <button type="button" className={`notif-chip nodrag nopan ${state !== "ready" ? "attention" : ""}`}
              onClick={(e) => { e.stopPropagation(); data.onEdit(n.id); }}
              aria-label={`${t("notification.chip_title")}: ${n.label}`} title={t("notification.chip_title")}>
        {channelIcon(channel, 14)}
        <span>{t(`notification.channel_${channel}`)}{to ? ` · ${to}` : ""}</span>
        <ChevronDown size={12} />
      </button>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
