/**
 * HTTP client for the core API. Adds the X-SpAIder-Token header to every
 * request and maps every failure to an ApiError { detail, code, status }.
 */
import type {
  ActiveModels,
  ActivityEvent,
  CatalogRefreshResult,
  CatalogResponse,
  Conversation,
  Document,
  EjectStatus,
  HardwareProfile,
  Health,
  Job,
  MemoryOverview,
  Message,
  ModelCatalogEntry,
  ModelInstalled,
  ModelSearchResponse,
  Note,
  RecommendationFocus,
  Recommendations,
  ResolveResponse,
  RuntimeEvent,
  SchedulerEvent,
  SearchResult,
  Settings,
  SetupStatus,
  Source,
  SystemInfo,
} from "./types";
import { parseJsonData, readSseStream } from "./sse";

export interface ApiConfig {
  baseUrl: string;
  token: string;
}

export class ApiError extends Error {
  readonly detail: string;
  readonly code: string;
  readonly status: number;
  readonly extra: Record<string, unknown>;

  constructor(detail: string, code: string, status: number, extra: Record<string, unknown> = {}) {
    super(detail);
    this.name = "ApiError";
    this.detail = detail;
    this.code = code;
    this.status = status;
    this.extra = extra;
  }
}

export function isApiError(e: unknown): e is ApiError {
  return e instanceof ApiError;
}

/** Converts any thrown value into a German user-facing message. */
export function errorMessage(e: unknown): string {
  if (isApiError(e)) return e.detail;
  if (e instanceof Error) return e.message;
  return String(e);
}

const NETWORK_ERROR = "Keine Verbindung zum SpAIder-Kern.";

/** Builds an ApiError from a non-2xx response body (JSON or not). */
export function mapErrorBody(status: number, body: unknown): ApiError {
  if (body && typeof body === "object") {
    const obj = body as Record<string, unknown>;
    const detail = typeof obj.detail === "string" ? obj.detail : null;
    const code = typeof obj.code === "string" ? obj.code : null;
    if (detail || code) {
      const extra: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(obj)) if (k !== "detail" && k !== "code") extra[k] = v;
      return new ApiError(detail ?? `HTTP ${status}`, code ?? "http_error", status, extra);
    }
  }
  if (status === 401) return new ApiError("Zugriff verweigert (ungültiges Token).", "unauthorized", 401);
  return new ApiError(`Unerwartete Antwort des Kerns (HTTP ${status}).`, "http_error", status);
}

type FetchLike = typeof fetch;

interface RequestOptions {
  json?: unknown;
  form?: FormData;
  query?: Record<string, string | number | boolean | undefined>;
  signal?: AbortSignal;
  accept?: string;
}

export class ApiClient {
  readonly baseUrl: string;
  private readonly token: string;
  private readonly fetchImpl: FetchLike;

  constructor(config: ApiConfig, fetchImpl: FetchLike = (...args) => globalThis.fetch(...args)) {
    this.baseUrl = config.baseUrl.replace(/\/+$/, "");
    this.token = config.token;
    this.fetchImpl = fetchImpl;
  }

  private url(path: string, query?: RequestOptions["query"]): string {
    let url = `${this.baseUrl}${path}`;
    if (query) {
      const params = new URLSearchParams();
      for (const [k, v] of Object.entries(query)) if (v !== undefined) params.set(k, String(v));
      const qs = params.toString();
      if (qs) url += `?${qs}`;
    }
    return url;
  }

  /** Performs a request and returns the raw Response (status already checked). */
  async raw(method: string, path: string, opts: RequestOptions = {}): Promise<Response> {
    const headers: Record<string, string> = {
      "X-SpAIder-Token": this.token,
      Accept: opts.accept ?? "application/json",
    };
    let body: BodyInit | undefined;
    if (opts.form) {
      body = opts.form;
    } else if (opts.json !== undefined) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(opts.json);
    }
    let res: Response;
    try {
      res = await this.fetchImpl(this.url(path, opts.query), { method, headers, body, signal: opts.signal });
    } catch (e) {
      if (e instanceof Error && e.name === "AbortError") throw new ApiError("Abgebrochen.", "aborted", 0);
      throw new ApiError(NETWORK_ERROR, "network_error", 0);
    }
    if (!res.ok) {
      const parsed: unknown = await res.json().catch(() => null);
      throw mapErrorBody(res.status, parsed);
    }
    return res;
  }

  async request<T>(method: string, path: string, opts: RequestOptions = {}): Promise<T> {
    const res = await this.raw(method, path, opts);
    if (res.status === 204) return undefined as T;
    try {
      return (await res.json()) as T;
    } catch {
      throw new ApiError("Ungültige Antwort des Kerns.", "invalid_response", res.status);
    }
  }

  /** Subscribes to an SSE endpoint until it ends or the signal aborts. */
  async subscribe<T>(
    path: string,
    onEvent: (event: string, data: T) => void,
    signal?: AbortSignal,
    onOpen?: () => void,
  ): Promise<void> {
    const res = await this.raw("GET", path, { signal, accept: "text/event-stream" });
    onOpen?.();
    await readSseStream(
      res,
      (ev) => {
        const data = parseJsonData<T>(ev);
        if (data !== null) onEvent(ev.event, data);
      },
      signal,
    );
  }

  // ----- health / setup / system ------------------------------------------
  health = () => this.request<Health>("GET", "/api/health");
  setupStatus = () => this.request<SetupStatus>("GET", "/api/setup/status");
  systemInfo = () => this.request<SystemInfo>("GET", "/api/system/info");
  ejectStatus = () => this.request<EjectStatus>("GET", "/api/system/eject-status");

  // ----- settings -----------------------------------------------------------
  getSettings = () => this.request<Settings>("GET", "/api/settings");
  patchSettings = (patch: Partial<Settings>) => this.request<Settings>("PATCH", "/api/settings", { json: patch });

  /** v1.1: `GET /api/system/hardware`. */
  hardware = () => this.request<HardwareProfile>("GET", "/api/system/hardware");

  // ----- models --------------------------------------------------------------
  catalog = async () => (await this.request<{ entries: ModelCatalogEntry[] }>("GET", "/api/models/catalog")).entries;
  /** v1.1: full catalog response incl. `catalog_date` (v1 cores just omit it). */
  catalogResponse = () => this.request<CatalogResponse>("GET", "/api/models/catalog");
  recommendations = (focus: RecommendationFocus) =>
    this.request<Recommendations>("GET", "/api/models/recommendations", { query: { focus } });
  searchModels = (q: string, limit = 20, signal?: AbortSignal) =>
    this.request<ModelSearchResponse>("GET", "/api/models/search", { query: { q, limit }, signal });
  resolveModel = (repo: string, file: string | null, signal?: AbortSignal) =>
    this.request<ResolveResponse>("POST", "/api/models/resolve", { json: { repo, file }, signal });
  refreshCatalog = () => this.request<CatalogRefreshResult>("POST", "/api/models/catalog/refresh");
  calibrateModel = async (modelId: string) =>
    (await this.request<{ job: Job }>("POST", `/api/models/${encodeURIComponent(modelId)}/calibrate`)).job;
  installedModels = async () => (await this.request<{ models: ModelInstalled[] }>("GET", "/api/models/installed")).models;
  activeModels = () => this.request<ActiveModels>("GET", "/api/models/active");
  downloadModel = async (catalogId: string) =>
    (await this.request<{ job: Job }>("POST", "/api/models/download", { json: { catalog_id: catalogId } })).job;
  importModel = async (path: string) =>
    (await this.request<{ job: Job }>("POST", "/api/models/import", { json: { path } })).job;
  activateModel = async (modelId: string) =>
    (await this.request<{ job: Job }>("POST", "/api/models/activate", { json: { model_id: modelId } })).job;
  deleteModel = (id: string) => this.request<{ ok: boolean }>("DELETE", `/api/models/${encodeURIComponent(id)}`);
  jobs = async () => (await this.request<{ jobs: Job[] }>("GET", "/api/models/jobs")).jobs;
  job = async (id: string) => (await this.request<{ job: Job }>("GET", `/api/models/jobs/${encodeURIComponent(id)}`)).job;
  cancelJob = async (id: string) =>
    (await this.request<{ job: Job }>("POST", `/api/models/jobs/${encodeURIComponent(id)}/cancel`)).job;
  resumeJob = async (id: string) =>
    (await this.request<{ job: Job }>("POST", `/api/models/jobs/${encodeURIComponent(id)}/resume`)).job;
  jobEvents = (id: string, onJob: (job: Job) => void, signal?: AbortSignal) =>
    this.subscribe<Job>(
      `/api/models/jobs/${encodeURIComponent(id)}/events`,
      (event, data) => {
        if (event === "job") onJob(data);
      },
      signal,
    );

  // ----- conversations --------------------------------------------------------
  conversations = async () =>
    (await this.request<{ conversations: Conversation[] }>("GET", "/api/conversations")).conversations;
  createConversation = (title: string | null = null) =>
    this.request<Conversation>("POST", "/api/conversations", { json: { title } });
  getConversation = (id: string) =>
    this.request<{ conversation: Conversation; messages: Message[] }>(
      "GET",
      `/api/conversations/${encodeURIComponent(id)}`,
    );
  renameConversation = (id: string, title: string) =>
    this.request<Conversation>("PATCH", `/api/conversations/${encodeURIComponent(id)}`, { json: { title } });
  deleteConversation = (id: string) =>
    this.request<{ ok: boolean }>("DELETE", `/api/conversations/${encodeURIComponent(id)}`);
  /** Starts a chat turn; the returned response is an SSE stream. */
  sendMessage = (id: string, content: string, signal?: AbortSignal) =>
    this.raw("POST", `/api/conversations/${encodeURIComponent(id)}/messages`, {
      json: { content },
      signal,
      accept: "text/event-stream",
    });
  stopGeneration = (id: string) =>
    this.request<{ ok: boolean; stopped: boolean }>("POST", `/api/conversations/${encodeURIComponent(id)}/stop`);
  messageSources = async (id: string, mid: string) =>
    (
      await this.request<{ sources: Source[] }>(
        "GET",
        `/api/conversations/${encodeURIComponent(id)}/messages/${encodeURIComponent(mid)}/sources`,
      )
    ).sources;
  /** v1.1: activity events kept in memory by the running core (empty after a restart). */
  messageActivity = async (id: string, mid: string) =>
    (
      await this.request<{ events: ActivityEvent[] }>(
        "GET",
        `/api/conversations/${encodeURIComponent(id)}/messages/${encodeURIComponent(mid)}/activity`,
      )
    ).events;

  // ----- activity (global channel, v1.1) ----------------------------------------------
  /** One long `GET /api/activity` connection; resolves when the stream ends. */
  activityStream = (
    handlers: {
      onActivity?: (ev: ActivityEvent) => void;
      onScheduler?: (ev: SchedulerEvent) => void;
      onRuntime?: (ev: RuntimeEvent) => void;
      onOpen?: () => void;
    },
    signal?: AbortSignal,
  ) =>
    this.subscribe<ActivityEvent | SchedulerEvent | RuntimeEvent>(
      "/api/activity",
      (event, data) => {
        if (event === "activity") handlers.onActivity?.(data as ActivityEvent);
        else if (event === "scheduler") handlers.onScheduler?.(data as SchedulerEvent);
        else if (event === "runtime") handlers.onRuntime?.(data as RuntimeEvent);
      },
      signal,
      handlers.onOpen,
    );

  // ----- documents ---------------------------------------------------------------
  documents = async () => (await this.request<{ documents: Document[] }>("GET", "/api/documents")).documents;
  uploadDocument = (file: File, conversationId: string | null) => {
    const form = new FormData();
    form.append("file", file, file.name);
    if (conversationId) form.append("conversation_id", conversationId);
    return this.request<Document>("POST", "/api/documents", { form });
  };
  document = (id: string) => this.request<Document>("GET", `/api/documents/${encodeURIComponent(id)}`);
  deleteDocument = (id: string) => this.request<{ ok: boolean }>("DELETE", `/api/documents/${encodeURIComponent(id)}`);
  documentEvents = (id: string, onDoc: (doc: Document) => void, signal?: AbortSignal) =>
    this.subscribe<Document>(
      `/api/documents/${encodeURIComponent(id)}/events`,
      (event, data) => {
        if (event === "document") onDoc(data);
      },
      signal,
    );

  // ----- memory ----------------------------------------------------------------------
  memoryOverview = () => this.request<MemoryOverview>("GET", "/api/memory/overview");
  memorySearch = async (q: string, limit = 20) =>
    (await this.request<{ results: SearchResult[] }>("GET", "/api/memory/search", { query: { q, limit } })).results;
  notes = async () => (await this.request<{ notes: Note[] }>("GET", "/api/memory/notes")).notes;
  createNote = (text: string) => this.request<Note>("POST", "/api/memory/notes", { json: { text } });
  updateNote = (id: string, text: string) =>
    this.request<Note>("PATCH", `/api/memory/notes/${encodeURIComponent(id)}`, { json: { text } });
  deleteNote = (id: string) => this.request<{ ok: boolean }>("DELETE", `/api/memory/notes/${encodeURIComponent(id)}`);

  // ----- system --------------------------------------------------------------------------
  shutdown = () => this.request<{ ok: boolean }>("POST", "/api/system/shutdown");
}
