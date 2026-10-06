import {
  Bot, Clock, Database, FileSpreadsheet, FileText, Files, GitBranch, Layers, LineChart, Mail, Archive,
  FileDown, ScrollText, Cpu, Wrench, FolderInput, Play, Table2, Split, Send, Monitor,
} from "lucide-react";
import type { Automation, FlowNode } from "../api/types";

export function automationIcon(a: Automation, size = 18) {
  const t = a.title.toLowerCase();
  if (t.includes("report") || t.includes("produzione")) return <LineChart size={size} />;
  if (t.includes("distinta") || t.includes("revision")) return <Layers size={size} />;
  if (a.graph.nodes.some((n) => n.kind === "agent")) return <Bot size={size} />;
  return <GitBranch size={size} />;
}

export function docIcon(kind: string, size = 14) {
  return ["xlsx", "csv", "db"].includes(kind) ? <Table2 size={size} /> : <FileText size={size} />;
}

export function nodeIcon(n: FlowNode, size = 22) {
  if (n.kind === "trigger") {
    if (n.trigger === "schedule") return <Clock size={size} />;
    if (n.trigger === "file") return <FileText size={size} />;
    return <Play size={size} />;
  }
  if (n.kind === "agent") return <Bot size={size} />;
  if (n.kind === "decision") return <Split size={size} />;
  if (n.kind === "condition") return <GitBranch size={size} />;
  if (n.kind === "notification") return channelIcon(n.channel, size);
  switch (n.action) {
    case "pdf_report": return <Archive size={size} />;
    case "write_xlsx": return <FileSpreadsheet size={size} />;
    case "erp_update": return <Database size={size} />;
    case "write_file": return <FileDown size={size} />;
    default: return <ScrollText size={size} />;
  }
}

export function channelIcon(channel: string | undefined, size = 18) {
  if (channel === "telegram") return <Send size={size} />;
  if (channel === "gmail") return <Mail size={size} />;
  return <Monitor size={size} />;
}

export function attachmentIcon(role: string, size = 18) {
  if (role === "modello") return <Cpu size={size} />;
  if (role === "memoria") return <Layers size={size} />;
  return <Wrench size={size} />;
}

export { Files, FolderInput };
