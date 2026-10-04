import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { AiKeyStatus, Automation, DiscoveryStateResp, Doc, Me, Suggestion, SuggestionSummary, SystemStatus } from "../api/types";
import { useI18n } from "../i18n";

export const useMe = () => useQuery({ queryKey: ["me"], queryFn: () => api.get<Me>("/api/v1/user/me"), retry: false });
export const useSystem = () =>
  useQuery({ queryKey: ["system"], queryFn: () => api.get<SystemStatus>("/api/v1/system/status"), refetchInterval: 30_000 });
export const useAiKey = (enabled = true) =>
  useQuery({ queryKey: ["ai-key"], queryFn: () => api.get<AiKeyStatus>("/api/v1/user/ai-key"), enabled, retry: false });
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
