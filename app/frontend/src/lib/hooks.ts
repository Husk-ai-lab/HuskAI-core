import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Automation, DiscoveryStateResp, Doc, Entitlement, Me, Suggestion, SuggestionSummary, SystemStatus } from "../api/types";
import { useI18n } from "../i18n";

export const useMe = () => useQuery({ queryKey: ["me"], queryFn: () => api.get<Me>("/api/v1/user/me"), retry: false });
/** Studio is started after Husk by the shell, so ask more often until it shows up. */
export const SYSTEM_POLL_MS = { studioUp: 30_000, studioDown: 5_000 };
export const systemPollMs = (s?: Pick<SystemStatus, "studio"> | null): number =>
  s?.studio?.alive ? SYSTEM_POLL_MS.studioUp : SYSTEM_POLL_MS.studioDown;
export const useSystem = () =>
  useQuery({
    queryKey: ["system"],
    queryFn: () => api.get<SystemStatus>("/api/v1/system/status"),
    refetchInterval: (q) => systemPollMs(q.state.data),
  });
/** Has the organisation's installation been activated (husk-gateway billing state)? `allowed: null` = unknown. */
export const useEntitlement = (enabled = true) =>
  useQuery({ queryKey: ["entitlement"], queryFn: () => api.get<Entitlement>("/api/v1/user/entitlement"), enabled, retry: false });
export const useAutomations = () => useQuery({ queryKey: ["automations"], queryFn: () => api.get<Automation[]>("/api/v1/automations") });
export const useDocuments = () => useQuery({ queryKey: ["documents"], queryFn: () => api.get<Doc[]>("/api/v1/documents") });
export const useDiscoveryState = () =>
  useQuery({
    // the server renders run errors in the request language
    queryKey: ["discovery-state", useI18n().lang],
    queryFn: () => api.get<DiscoveryStateResp>("/api/v1/discovery/state"),
    refetchInterval: (q) => (q.state.data?.state === "in_corso" ? 3000 : false),
  });
export const useSuggestionSummary = () =>
  useQuery({ queryKey: ["suggestions-summary"], queryFn: () => api.get<SuggestionSummary>("/api/v1/suggestions/summary"), refetchInterval: 60_000 });
export const useSuggestions = (filter: string) =>
  useQuery({ queryKey: ["suggestions", filter], queryFn: () => api.get<Suggestion[]>(`/api/v1/suggestions?status=${filter}`) });
export const useDocQuota = () =>
  useQuery({ queryKey: ["doc-quota"], queryFn: () => api.get<{ count: number; limit: number }>("/api/v1/documents/quota") });
