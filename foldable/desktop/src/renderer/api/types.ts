/**
 * Types of the core HTTP API (docs/portable/API_CONTRACT.md, v1).
 * Field names are copied verbatim from the contract.
 */

export type RuntimeState = "stopped" | "starting" | "ready" | "error";

export interface Health {
  status: string;
  version: string;
  app_root: string;
  data_dir: string;
}

export interface SetupStatus {
  state: "needs_model" | "ready";
  active_chat_model: ModelInstalled | null;
  active_embedding_model: ModelInstalled | null;
  runtime_state: RuntimeState;
}

export interface SystemInfo {
  os: string;
  arch: string;
  hostname: string;
  cpu: string;
  threads: number;
  ram_total_bytes: number;
  ram_available_bytes: number;
  disk_total_bytes: number;
  disk_free_bytes: number;
  filesystem: string;
  max_file_bytes: number | null;
  app_root: string;
  data_dir: string;
  usage: {
    models_bytes: number;
    db_bytes: number;
    imports_bytes: number;
    logs_bytes: number;
    downloads_bytes: number;
  };
  runtime: { name: string; build: string; variant: string; path: string };
}

export interface EjectStatus {
  ready: boolean;
  active_streams: number;
  runtime_processes: number;
  db_open: boolean;
  running_jobs: number;
  message_de: string;
}

export interface Settings {
  thinking_enabled: boolean;
  context_tokens: number;
  max_answer_tokens: number;
  memory_auto_extract: boolean;
  embedding_enabled: boolean;
  threads: number | null;
  temperature: number;
  system_prompt_extra: string;
}

export interface License {
  id: string;
  name: string;
  url: string;
}

export type ModelRole = "chat" | "embedding";
export type FitLevel = "recommended" | "ok" | "tight" | "too_large" | "unknown";

export interface ModelCatalogEntry {
  id: string;
  name: string;
  role: ModelRole;
  family: string;
  publisher: string;
  source: "official" | "community";
  repo: string;
  revision: string;
  repo_url: string;
  files: { path: string; size: number; sha256: string }[];
  total_bytes: number;
  license: License;
  architecture: string;
  parameters: string;
  quantization: string;
  context_length: number;
  embedding_dimensions: number | null;
  min_ram_bytes: number;
  recommended_ram_bytes: number;
  support: "executed" | "documented";
  description_de: string;
  fit: { level: FitLevel; message_de: string };
  installed_model_id: string | null;
}

export interface ModelInstalled {
  id: string;
  name: string;
  role: ModelRole;
  catalog_id: string | null;
  source: "catalog" | "import";
  path: string;
  size_bytes: number;
  sha256: string | null;
  sha256_verified: boolean;
  architecture: string;
  quantization: string;
  context_length: number;
  embedding_dimensions: number | null;
  license: License | null;
  state: "ready" | "broken";
  active: boolean;
  created_at: string;
}

export type JobKind = "download" | "import" | "activate" | "delete";
export type JobState = "queued" | "running" | "paused" | "done" | "failed" | "cancelled";
export type JobPhase =
  | "check_space"
  | "download"
  | "verify"
  | "copy"
  | "load"
  | "probe"
  | "commit"
  | "cleanup"
  | "rollback"
  | null;

export interface Job {
  id: string;
  kind: JobKind;
  state: JobState;
  catalog_id: string | null;
  model_id: string | null;
  phase: JobPhase;
  progress: { done_bytes: number; total_bytes: number; speed_bps: number; eta_seconds: number | null };
  message_de: string;
  error: { detail: string; code: string } | null;
  result_model_id: string | null;
  created_at: string;
  updated_at: string;
  finished_at: string | null;
}

export const TERMINAL_JOB_STATES: JobState[] = ["done", "failed", "cancelled"];
export function isTerminalJob(job: Job): boolean {
  return TERMINAL_JOB_STATES.includes(job.state);
}

export interface ActiveModels {
  chat: ModelInstalled | null;
  embedding: ModelInstalled | null;
  runtime_state: RuntimeState;
  error: string | null;
}

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
  last_model_id: string | null;
}

export type MessageStatus = "complete" | "streaming" | "stopped" | "error";

export interface Usage {
  prompt_tokens: number;
  completion_tokens: number;
  time_to_first_token_ms: number;
  tokens_per_second: number;
}

export interface SourceOrigin {
  kind: "conversation" | "document" | "note";
  id: string;
  title: string;
  at: string;
}

export interface Source {
  kind: "fact" | "document" | "note" | "message";
  id: string;
  label: string;
  excerpt: string;
  status: "current" | "superseded" | "uncertain";
  origin: SourceOrigin;
  superseded_by: { id: string; excerpt: string } | null;
}

export interface Message {
  id: string;
  conversation_id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
  status: MessageStatus;
  model_id: string | null;
  sources: Source[];
  error: string | null;
  usage: Usage | null;
}

export type ChatPhase = "retrieving" | "generating" | "memorizing";

export type ChatEvent =
  | { type: "message"; data: { user_message: Message; assistant_message: Message } }
  | { type: "status"; data: { phase: ChatPhase; message_de: string } }
  | { type: "token"; data: { text: string } }
  | { type: "sources"; data: { sources: Source[] } }
  | { type: "done"; data: { message: Message } }
  | { type: "error"; data: { detail: string; code: string } };

export type DocumentStatus = "imported" | "indexing" | "indexed" | "failed";

export interface DocumentWarning {
  code: string;
  message_de: string;
}

export interface Document {
  id: string;
  name: string;
  content_type: string;
  size_bytes: number;
  text_chars: number;
  status: DocumentStatus;
  warnings: DocumentWarning[];
  conversation_id: string | null;
  created_at: string;
  facts_count: number;
  progress: { done: number; total: number };
}

export interface MemoryOverview {
  facts: number;
  entities: number;
  relations: number;
  superseded: number;
  documents: number;
  notes: number;
  conversations: number;
  messages: number;
  index: {
    fts: boolean;
    vectors: { enabled: boolean; model_id: string | null; dimensions: number | null; count: number };
  };
}

export type SearchResult = Source & { score: number };

export interface Note {
  id: string;
  text: string;
  created_at: string;
  updated_at: string;
}
