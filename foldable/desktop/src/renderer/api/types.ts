/**
 * Types of the core HTTP API (docs/portable/API_CONTRACT.md, v1 + Addendum
 * v1.1, and docs/portable/ACTIVITY_EVENTS.md). Field names are copied
 * verbatim from the contract. Fields introduced by v1.1 are optional so that
 * the renderer also tolerates a v1 core.
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

// ----- v1.1: hardware ---------------------------------------------------------

export type Backend = "cpu" | "vulkan" | string;

export interface HardwareGpu {
  name: string;
  backend: Backend;
  vram_total_bytes: number;
  vram_free_bytes: number;
  driver: string;
  usable: boolean;
}

export interface HardwareProfile {
  cpu: { name: string; cores_physical: number; threads: number; features: string[] };
  ram_total_bytes: number;
  ram_available_bytes: number;
  gpus: HardwareGpu[];
  disk_free_bytes: number;
  power: { on_battery: boolean; power_saver: boolean };
  fingerprint: string;
  runtime_variants: string[];
  active_backend: Backend;
  detected_at: string;
}

export type PerformanceMode = "auto" | "quiet" | "fast";
export type GpuMode = "auto" | "off";

export interface Settings {
  thinking_enabled: boolean;
  context_tokens: number;
  max_answer_tokens: number;
  memory_auto_extract: boolean;
  embedding_enabled: boolean;
  threads: number | null;
  temperature: number;
  system_prompt_extra: string;
  /** v1.1 */
  performance_mode?: PerformanceMode;
  /** v1.1 */
  gpu_mode?: GpuMode;
}

export type LicenseKind = "open-source" | "open-weights" | "other";

export interface License {
  id: string;
  name: string;
  url: string;
  /** v1.1 */
  kind?: LicenseKind;
  /** v1.1: access requires accepting terms on Hugging Face */
  gated?: boolean;
  /** v1.1 */
  requires_account?: boolean;
}

export type ModelRole = "chat" | "embedding";
export type FitLevel = "recommended" | "ok" | "tight" | "too_large" | "unknown";
export type VerificationStatus = "executed" | "metadata" | "unverified";

export interface Fit {
  level: FitLevel;
  message_de: string;
}

export interface Verification {
  status: VerificationStatus;
  date: string | null;
  scope: string | null;
}

export interface Measured {
  backend: Backend;
  tokens_per_second: number;
  time_to_first_token_ms: number;
  prompt_tokens_per_second: number;
  measured_at: string;
  fingerprint: string;
}

export interface Evidence {
  kind: "benchmark" | "local_measurement" | "model_card";
  note_de: string;
  scope: string;
}

export interface ModelResources {
  weights_bytes: number;
  kv_bytes_per_token_est: number;
  est_ram_bytes_8k: number;
  est_vram_bytes_8k: number;
}

export interface ModelCapabilities {
  chat_template: boolean;
  reasoning_toggle: string | null;
  system_role: boolean;
  context_length: number;
  modalities: string[];
  structured_output: boolean;
  backends: Backend[];
}

export type ModelType = "chat" | "embedding" | "base" | "vision" | "audio";

export interface ModelFile {
  path: string;
  size: number;
  sha256: string | null;
}

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
  files: ModelFile[];
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
  fit: Fit;
  installed_model_id: string | null;
  // ----- v1.1 ---------------------------------------------------------------
  original_model?: string | null;
  converter?: string | null;
  verification?: Verification;
  evidence?: Evidence[];
  resources?: ModelResources;
  template?: { reasoning_toggle: string | null; system_role: boolean };
  type?: ModelType;
  families?: string[];
  published?: string | null;
  checked_at?: string | null;
  source_kind?: "builtin" | "search" | "user";
}

export interface CatalogResponse {
  entries: ModelCatalogEntry[];
  catalog_date?: string;
  local_extensions?: number;
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
  /** v1.1 */
  capabilities?: ModelCapabilities | null;
  /** v1.1 */
  verification?: Verification | null;
  /** v1.1: measurement on this hardware, null when never measured */
  measured?: Measured | null;
}

export type JobKind = "download" | "import" | "activate" | "delete" | "calibrate";
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

// ----- v1.1: recommendations, search, resolve ----------------------------------

export type RecommendationFocus = "alltag" | "dokumente" | "coding";
export type RecommendationSlot = "balanced" | "fast" | "quality";

export interface RecommendationPick {
  slot: RecommendationSlot;
  entry: ModelCatalogEntry;
  reasons_de: string[];
  estimate: { ram_bytes: number; vram_bytes: number; download_bytes: number; context_tokens: number };
  measured: Measured | null;
  installed_model_id: string | null;
}

export interface Recommendations {
  hardware: HardwareProfile;
  picks: RecommendationPick[];
  excluded: { id: string; reason_de: string }[];
  catalog_date: string;
}

export interface ModelSearchResult {
  repo: string;
  author: string;
  name: string;
  likes: number;
  downloads: number;
  updated: string;
  gguf: boolean;
  status: "unverified";
}

export interface ModelSearchResponse {
  results: ModelSearchResult[];
  cached_at: string | null;
  offline: boolean;
}

export interface ResolveResponse {
  entry: ModelCatalogEntry;
  files: ModelFile[];
  compatibility: { ok: boolean; reasons_de: string[]; architecture: string; context_length: number; type: ModelType };
  fit: Fit;
}

export interface CatalogRefreshResult {
  catalog_date: string;
  checked: number;
  changed: number;
  errors: string[];
}

// ----- conversations ------------------------------------------------------------

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
  last_model_id: string | null;
  /** v1.1: open memory post-processing tasks of this conversation */
  postprocessing_pending?: number;
}

export type MessageStatus = "complete" | "streaming" | "stopped" | "error";

export interface Usage {
  prompt_tokens: number;
  completion_tokens: number;
  time_to_first_token_ms: number;
  tokens_per_second: number;
}

/** v1.1: measured durations only; a missing field means "not measured". */
export interface MessageTimings {
  memory_search_ms?: number | null;
  prompt_processing_ms?: number | null;
  first_token_ms?: number | null;
  generation_ms?: number | null;
  total_ms?: number | null;
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
  /** v1.1 */
  timings?: MessageTimings | null;
}

// ----- activity events (ACTIVITY_EVENTS.md) ---------------------------------------

export type ActivityPhase =
  | "queued"
  | "model_loading"
  | "memory_search"
  | "memory_reconcile"
  | "prompt_build"
  | "prompt_processing"
  | "generating"
  | "answer_done"
  | "saved"
  | "postprocess_pending"
  | "postprocess_running"
  | "postprocess_done"
  | "failed";

export type ActivityState = "started" | "progress" | "done" | "failed" | "cancelled";

export interface ActivityCounters {
  sources?: number;
  facts_pending?: number;
  prompt_total?: number;
  prompt_processed?: number;
  prompt_cached?: number;
  tokens?: number;
  tokens_per_second?: number;
  [key: string]: number | undefined;
}

export interface ActivityEvent {
  request_id: string;
  seq: number;
  phase: ActivityPhase | string;
  state: ActivityState;
  t_ms: number;
  at: string;
  message_de: string;
  counters?: ActivityCounters | null;
  source_ids?: string[];
  /** only on the global channel (post-processing events) */
  conversation_id?: string;
}

export interface SchedulerEvent {
  chat_active: boolean;
  background: { kind: "message" | "chunk" | "reindex" | null; pending: number };
  paused: boolean;
}

/** `event: runtime` on the global channel; the contract only says "Modellzustände". */
export interface RuntimeEvent {
  state?: RuntimeState;
  runtime_state?: RuntimeState;
  model_id?: string | null;
  message_de?: string;
  [key: string]: unknown;
}

export type ChatPhase = "retrieving" | "generating" | "memorizing";

export type ChatEvent =
  | { type: "message"; data: { user_message: Message; assistant_message: Message } }
  | { type: "status"; data: { phase: ChatPhase; message_de: string } }
  | { type: "token"; data: { text: string } }
  | { type: "thinking"; data: { text: string } }
  | { type: "activity"; data: ActivityEvent }
  | { type: "sources"; data: { sources: Source[] } }
  | { type: "done"; data: { message: Message } }
  | { type: "error"; data: { detail: string; code: string } };

// ----- documents, memory ----------------------------------------------------------------

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
