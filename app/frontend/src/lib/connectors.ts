import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Connector } from "../api/types";

export const CONNECTOR_KINDS = ["telegram", "gmail"] as const;
export type ConnectorKind = (typeof CONNECTOR_KINDS)[number];

/** The guides: how many steps/notes each has and which step opens which page. The texts live in the catalogues as
 *  connectors.<kind>.step<n>_title / step<n>_body / link<n> / tip<n> or note<n>; the URLs live here, not in copy. */
export const GUIDE: Record<ConnectorKind, { steps: number; extras: "tip" | "note"; extraCount: number; links: Record<number, string> }> = {
  telegram: { steps: 6, extras: "tip", extraCount: 2, links: { 1: "https://t.me/BotFather" } },
  gmail: {
    steps: 5, extras: "note", extraCount: 3,
    links: { 1: "https://myaccount.google.com/security", 2: "https://myaccount.google.com/apppasswords" },
  },
};

export const useConnectors = (enabled = true) =>
  useQuery({ queryKey: ["connectors"], queryFn: () => api.get<Connector[]>("/api/v1/connectors"), enabled, retry: false });

export const allReady = (cs: Connector[] | undefined) => !!cs && cs.every((c) => c.state === "ready");
