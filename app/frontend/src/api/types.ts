export type Theme = "light" | "dark";
export type PersonalityId = "bilanciato" | "ultra_prudente" | "esplicativo" | "conciso";

export interface User { id: string; org_id: string; org_name?: string | null; email?: string | null; display_name?: string | null; roles: string[] }
export interface UserSettings { theme: Theme; language: "it" | "en"; personality: PersonalityId; connectors_onboarded: boolean }
export interface Me { user: User; settings: UserSettings }

export interface MaintenanceWindow { id: string; message_it: string; message_en: string; starts_at: string; ends_at: string; blocking: boolean }
export interface Maintenance {
  active: boolean; blocking: boolean; message_it?: string | null; message_en?: string | null; starts_at?: string | null; ends_at?: string | null;
  source: string; upcoming: MaintenanceWindow[];
}
export interface StudioStatus {
  alive: boolean; ui_url?: string | null; otlp: boolean; mcp: boolean; mcp_tools: string[];
  tracing?: boolean; install_url?: string; run_url_template?: string;
}
export interface Entitlement { allowed: boolean | null; status: string; source: "gateway" | "cache" | "unknown" }
export interface SystemStatus {
  version: string; llm_model?: string | null; login_configured: boolean; neon_configured: boolean;
  maintenance: Maintenance; release: { current: string; latest?: string | null; update_available: boolean; update_required: boolean };
  studio: StudioStatus; data_dir: string;
}

export interface UsedBy { automation_id: string; title: string }
export interface Doc {
  id: string; filename: string; title: string; kind: string; department?: string | null; size_bytes: number; rows?: number | null;
  status: "pending" | "ingested" | "failed"; excerpt?: string | null; error?: string | null; created_at: string; used_by?: UsedBy[];
  source_path?: string | null;
}
export interface Highlight { start: number; end: number }
export interface SearchHit { document_id: string; chunk_id: string; score: number; heading?: string | null; text: string; highlights: Highlight[] }
export interface DocSearchResult { document: Doc; matches: number; best: SearchHit }
export interface ColumnProfile { name: string; dtype: string; non_null: number; unique: number; sample: string[] }
export interface TabularProfile { sheet: string; n_rows: number; n_cols: number; columns: ColumnProfile[] }
export interface CardEvidence { document_id: string; chunk_id?: string | null; quote?: string | null; stat_id?: string | null; cells?: string | null }
export interface CardActivity { id: string; what: string; frequency?: string | null; actors: string[]; volume?: string | null; evidence: CardEvidence[] }
export interface CardStat { id: string; sheet?: string | null; metric: string; text: string; cells?: string | null }
export interface DocumentCard { document_id: string; kind: "text" | "table"; title: string; sentence: string; signal: number; activities: CardActivity[]; stats: CardStat[]; model?: string | null }
export interface DocDetail { document: Doc; profiles: TabularProfile[]; parts: number; card?: DocumentCard | null }

export type StepStatus = "pending" | "running" | "done" | "failed";
export interface StepView { step: string; label: string; status: StepStatus }
export interface DiscoveryRun { id: string; status: "pending" | "running" | "waiting_network" | "paused" | "failed" | "completed"; current_step?: string | null; progress: number; error?: string | null; document_ids: string[]; trace_id?: string | null; traced?: boolean }
export interface DiscoveryRunView { run: DiscoveryRun; steps: StepView[]; studio_url?: string | null }
export interface DiscoveryStateResp { state: "da_fare" | "in_corso" | "fatta"; documents_ready: number; run?: DiscoveryRunView | null; last_completed_run_id?: string | null }
export interface Opportunity { id: string; title: string; explanation: string; source_document_ids: string[]; hours_per_week: number; priority: string; repetitive_activities: number; automation_id?: string | null }
export interface UsageTotals { calls: number; tokens_in: number; tokens_out: number }
export interface DiscoveryReport { run_id: string; documents_analyzed: number; opportunities: number; repetitive_activities: number; total_hours_per_week: number; items: Opportunity[];
  usage?: { total?: UsageTotals; by_model?: Record<string, UsageTotals>; steps?: Record<string, { total: UsageTotals }> } }
export interface Fact { id: string; text: string; category: string; importance: number }
export interface MemoryView { company_profile_markdown: string; facts: Fact[]; max_facts: number; networks: { network_id: string; automation_id?: string | null; label: string; notes: number; summary_markdown?: string | null }[] }

export interface Attachment { id: string; role: "modello" | "memoria" | "strumento"; label: string; subtitle?: string | null; document_ids: string[] }
export interface FlowNode {
  id: string; kind: "trigger" | "agent" | "decision" | "condition" | "action"; label: string; subtitle?: string | null;
  position: { x: number; y: number };
  trigger?: string; cron?: string | null; folder?: string | null;
  instructions?: string; attachments?: Attachment[];
  question?: string; options?: string[];
  field?: string; op?: string; value?: number | string;
  action?: string; target?: string | null;
}
export interface FlowEdge { id: string; source: string; target: string; branch?: string | null }
export interface Automation {
  id: string; title: string; description: string; status: "bozza" | "caricata" | "attiva" | "errore";
  graph: { nodes: FlowNode[]; edges: FlowEdge[] }; source_document_ids: string[]; hours_per_week: number; priority: string; version: number; updated_at: string;
}
export interface NodeRunLog { node_id: string; label: string; status: "ok" | "error" | "skipped" | "simulated"; output?: unknown; message?: string | null; duration_ms: number }
export interface AutomationRun { id: string; automation_id: string; trigger: string; status: "running" | "success" | "failed" | "skipped"; log: NodeRunLog[]; error?: string | null; trace_id?: string | null; traced?: boolean; started_at: string; ended_at?: string | null }
export interface AutomationView { automation: Automation; next_run?: string | null; last_run?: AutomationRun | null }

export interface Personality { id: PersonalityId; label: string; description: string; requires_confirmation: boolean }
export interface ChatMessage { id: string; chat_id: string; role: "user" | "assistant" | "tool" | "system"; content: string; meta: Record<string, unknown>; created_at: string }
export interface Proposal { id: string; summary: string; operations: Record<string, unknown>[]; status: string; automation_id: string; suggestion_id?: string | null; origin?: "suggestion" | "self_heal" | null }
export interface Chat { id: string; automation_id?: string | null; personality: PersonalityId }
export interface ChatView { chat: Chat; messages: ChatMessage[]; proposals: Proposal[]; suggestions: string[] }

export type SuggestionKind = "modify_automation" | "new_automation" | "memory_update" | "source_deleted" | "run_discovery" | "configure_connector";
export type SuggestionStatus = "new" | "seen" | "applied" | "dismissed";
export interface SuggestionEvidence { document_id: string; chunk_id?: string | null; quote: string }
export interface Suggestion {
  id: string; kind: SuggestionKind; status: SuggestionStatus; automation_id?: string | null; connector?: string | null; document_ids: string[];
  title: string; body: string; prompt?: string | null; evidence: SuggestionEvidence[]; confidence: number;
  hours_per_week?: number | null; created_at: string; updated_at: string;
}
export interface SuggestionSummary { unread: number; open: number; processing: boolean }

export type ConnectorState = "not_configured" | "ready" | "error";
export interface Connector {
  kind: "telegram" | "gmail"; state: ConnectorState; hint?: string | null; target_hint?: string | null;
  verified_at?: string | null; last_ok_at?: string | null; last_error?: string | null;
}
