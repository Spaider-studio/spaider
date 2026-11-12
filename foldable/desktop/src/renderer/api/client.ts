/**
 * HTTP client for the core API. Adds the X-SpAIder-Token header to every
 * request and maps every failure to an ApiError { detail, code, status }.
 */
import type {
  Action,
  ActiveModels,
  ActivityEvent,
  AgentCapabilities,
  AgentStopResult,
  AnswerRequest,
  BrowserInstallResponse,
  BrowserStatus,
  CatalogRefreshResult,
  CreateTaskRequest,
  ForegroundResponse,
  LiveAskRequest,
  LiveSession,
  LiveSources,
  Recipe,
  RecipeParameter,
  Snapshot,
  SnapshotRequest,
  StopEvent,
  Task,
  TaskStreamEvent,
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
  BindingResponse,
  Connection,
  ConnectionModel,
  ConnectionTestResult,
  Consent,
  CreateConnectionRequest,
  CredentialStoreStatus,
  DiscoverQuery,
  DiscoverResponse,
  ModelCard,
  PatchConnectionRequest,
  Project,
  ProjectsResponse,
  ProvidersResponse,
  RepoDetail,
  SetBindingRequest,
  VaultUnlockResult,
  BindingsResponse,
  CompileParameter,
  HostBinding,
  NegativeCheckResult,
  PatchWorkflowRequest,
  RunWorkflowRequest,
  SuccessCriterion,
  TeachCaptureResponse,
  TeachEvent,
  TeachFinishResponse,
  TeachSession,
  TeachStartRequest,
  TeachStatus,
  TeachStep,
  Workflow,
  WorkflowCheckResult,
  WorkflowDetail,
  WorkflowsEvent,
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
      /** v1.2 */
      onLive?: (session: LiveSession) => void;
      /** v1.2 */
      onTask?: (task: Task) => void;
      /** v1.2 */
      onStop?: (ev: StopEvent) => void;
      /** v1.5: a teach session changed (id, state, steps) */
      onTeach?: (ev: TeachEvent) => void;
      /** v1.5: dependencies of workflows changed (`stale` ids) */
      onWorkflows?: (ev: WorkflowsEvent) => void;
      onOpen?: () => void;
    },
    signal?: AbortSignal,
  ) =>
    this.subscribe<ActivityEvent | SchedulerEvent | RuntimeEvent | LiveSession | Task | StopEvent | TeachEvent | WorkflowsEvent>(
      "/api/activity",
      (event, data) => {
        if (event === "activity") handlers.onActivity?.(data as ActivityEvent);
        else if (event === "scheduler") handlers.onScheduler?.(data as SchedulerEvent);
        else if (event === "runtime") handlers.onRuntime?.(data as RuntimeEvent);
        else if (event === "live") handlers.onLive?.(data as LiveSession);
        else if (event === "task") handlers.onTask?.(data as Task);
        else if (event === "stop") handlers.onStop?.(data as StopEvent);
        else if (event === "teach") handlers.onTeach?.(data as TeachEvent);
        else if (event === "workflows") handlers.onWorkflows?.(data as WorkflowsEvent);
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

  // ----- v1.2: live sources and sessions ------------------------------------------------
  liveSources = () => this.request<LiveSources>("GET", "/api/live/sources");
  liveForeground = () => this.request<ForegroundResponse>("POST", "/api/live/foreground");
  liveSessions = async () => (await this.request<{ sessions: LiveSession[] }>("GET", "/api/live/sessions")).sessions;
  createLiveSession = async (sourceId: string, conversationId: string | null = null) =>
    (await this.request<{ session: LiveSession }>("POST", "/api/live/sessions", { json: { source_id: sourceId, conversation_id: conversationId } }))
      .session;
  getLiveSession = async (id: string) =>
    (await this.request<{ session: LiveSession }>("GET", `/api/live/sessions/${encodeURIComponent(id)}`)).session;
  /** Deliberate source switch (never silent). */
  switchLiveSource = async (id: string, sourceId: string) =>
    (await this.request<{ session: LiveSession }>("PATCH", `/api/live/sessions/${encodeURIComponent(id)}`, { json: { source_id: sourceId } })).session;
  endLiveSession = (id: string) => this.request<{ ok: boolean }>("DELETE", `/api/live/sessions/${encodeURIComponent(id)}`);
  liveSnapshot = async (id: string, req: SnapshotRequest) =>
    (await this.request<{ snapshot: Snapshot }>("POST", `/api/live/sessions/${encodeURIComponent(id)}/snapshot`, { json: req })).snapshot;
  /** Starts a live question; the returned response is an SSE stream (same events as chat + `observation`). */
  liveAsk = (id: string, req: LiveAskRequest, signal?: AbortSignal) =>
    this.raw("POST", `/api/live/sessions/${encodeURIComponent(id)}/ask`, { json: req, signal, accept: "text/event-stream" });
  liveStop = (id: string) =>
    this.request<{ ok: boolean; stopped: boolean }>("POST", `/api/live/sessions/${encodeURIComponent(id)}/stop`);

  // ----- v1.2: agent ---------------------------------------------------------------------------
  agentCapabilities = () => this.request<AgentCapabilities>("GET", "/api/agent/capabilities");
  tasks = async () => (await this.request<{ tasks: Task[] }>("GET", "/api/agent/tasks")).tasks;
  createTask = async (req: CreateTaskRequest) => (await this.request<{ task: Task }>("POST", "/api/agent/tasks", { json: req })).task;
  getTask = (id: string) => this.request<{ task: Task; actions: Action[] }>("GET", `/api/agent/tasks/${encodeURIComponent(id)}`);
  taskEvents = (id: string, onEvent: (ev: TaskStreamEvent) => void, signal?: AbortSignal) =>
    this.subscribe<TaskStreamEvent["data"]>(
      `/api/agent/tasks/${encodeURIComponent(id)}/events`,
      (event, data) => {
        if (["task", "action", "activity", "token", "question", "step", "done"].includes(event)) {
          onEvent({ type: event, data } as TaskStreamEvent);
        }
      },
      signal,
    );
  cancelTask = async (id: string) => (await this.request<{ task: Task }>("POST", `/api/agent/tasks/${encodeURIComponent(id)}/cancel`)).task;
  pauseTask = async (id: string) => (await this.request<{ task: Task }>("POST", `/api/agent/tasks/${encodeURIComponent(id)}/pause`)).task;
  resumeTask = async (id: string) => (await this.request<{ task: Task }>("POST", `/api/agent/tasks/${encodeURIComponent(id)}/resume`)).task;
  answerTask = async (id: string, answer: AnswerRequest) =>
    (await this.request<{ task: Task }>("POST", `/api/agent/tasks/${encodeURIComponent(id)}/answer`, { json: answer })).task;
  /** 0.3 recipes (kept for older cores; v1.5 answers `{recipe, workflow}`). */
  saveRecipe = async (taskId: string, name: string, parameters: RecipeParameter[]) =>
    (await this.request<{ recipe: Recipe; workflow?: Workflow }>("POST", `/api/agent/tasks/${encodeURIComponent(taskId)}/save-recipe`, { json: { name, parameters } }))
      .recipe;
  recipes = async () => (await this.request<{ recipes: Recipe[] }>("GET", "/api/agent/recipes")).recipes;
  recipe = async (id: string) => (await this.request<{ recipe: Recipe }>("GET", `/api/agent/recipes/${encodeURIComponent(id)}`)).recipe;
  deleteRecipe = (id: string) => this.request<{ ok: boolean }>("DELETE", `/api/agent/recipes/${encodeURIComponent(id)}`);
  /** Global stop (hotkey): locks immediately, does not wait for the model. */
  agentStop = () => this.request<AgentStopResult>("POST", "/api/agent/stop");

  // ----- v1.5 (0.5): workflows ---------------------------------------------------------------
  workflows = async () => (await this.request<{ workflows: Workflow[] }>("GET", "/api/agent/workflows")).workflows;
  workflow = (id: string) => this.request<WorkflowDetail>("GET", `/api/agent/workflows/${encodeURIComponent(id)}`);
  patchWorkflow = async (id: string, patch: PatchWorkflowRequest) =>
    (await this.request<{ workflow: Workflow }>("PATCH", `/api/agent/workflows/${encodeURIComponent(id)}`, { json: patch })).workflow;
  deleteWorkflow = (id: string) => this.request<{ ok: boolean }>("DELETE", `/api/agent/workflows/${encodeURIComponent(id)}`);
  /** Dry run of the reuse gate on this PC (nothing is executed). */
  checkWorkflow = (id: string) => this.request<WorkflowCheckResult>("GET", `/api/agent/workflows/${encodeURIComponent(id)}/check`);
  negativeCheckWorkflow = (id: string) => this.request<NegativeCheckResult>("POST", `/api/agent/workflows/${encodeURIComponent(id)}/negative-check`);
  /** Starts a task from a workflow (201 `{task}`; 422 `parameters_missing` lists the names). */
  runWorkflow = async (id: string, req: RunWorkflowRequest) =>
    (await this.request<{ task: Task }>("POST", `/api/agent/workflows/${encodeURIComponent(id)}/run`, { json: req })).task;
  /** Compiles a completed task into a candidate workflow (409 `not_completed` otherwise). */
  compileWorkflow = async (taskId: string, name: string, parameters: CompileParameter[]) =>
    (await this.request<{ workflow: Workflow }>("POST", `/api/agent/tasks/${encodeURIComponent(taskId)}/compile`, { json: { name, parameters } })).workflow;

  // ----- v1.5: host bindings (role -> target on this PC) -----------------------------------
  bindings = () => this.request<BindingsResponse>("GET", "/api/agent/bindings");
  putBinding = async (role: string, kind: HostBinding["kind"], target: string) =>
    (await this.request<{ binding: HostBinding }>("PUT", "/api/agent/bindings", { json: { role, kind, target } })).binding;
  deleteBinding = (role: string) => this.request<{ ok: boolean }>("DELETE", `/api/agent/bindings/${encodeURIComponent(role)}`);

  // ----- v1.5: teach ("Ablauf beibringen") ---------------------------------------------------
  teachStatus = () => this.request<TeachStatus>("GET", "/api/agent/teach");
  /** 409 `busy` while a task runs or another demonstration is active. */
  teachStart = async (req: TeachStartRequest) => (await this.request<{ session: TeachSession }>("POST", "/api/agent/teach", { json: req })).session;
  teachGet = async (sid: string) => (await this.request<{ session: TeachSession }>("GET", `/api/agent/teach/${encodeURIComponent(sid)}`)).session;
  /** Window source: diffs the window; `invokeName` records that the user pressed that button. */
  teachCapture = (sid: string, invokeName?: string) =>
    this.request<TeachCaptureResponse>("POST", `/api/agent/teach/${encodeURIComponent(sid)}/capture`, { json: invokeName ? { invoke_name: invokeName } : {} });
  teachAddCheck = (sid: string, check: SuccessCriterion) =>
    this.request<{ step: TeachStep; session: TeachSession }>("POST", `/api/agent/teach/${encodeURIComponent(sid)}/check`, { json: { check } });
  teachRemoveStep = async (sid: string, index: number) =>
    (await this.request<{ session: TeachSession }>("DELETE", `/api/agent/teach/${encodeURIComponent(sid)}/steps/${index}`)).session;
  teachFinish = (sid: string, parameters: CompileParameter[], checks: SuccessCriterion[]) =>
    this.request<TeachFinishResponse>("POST", `/api/agent/teach/${encodeURIComponent(sid)}/finish`, { json: { parameters, checks } });
  teachCancel = async (sid: string) => (await this.request<{ session: TeachSession }>("POST", `/api/agent/teach/${encodeURIComponent(sid)}/cancel`)).session;
  /** On-demand browser package for tasks (status). */
  agentBrowser = () => this.request<BrowserStatus>("GET", "/api/agent/browser");
  /** Starts the browser download (job kind `browser`, followed like a model download). */
  installBrowser = () => this.request<BrowserInstallResponse>("POST", "/api/agent/browser/install");

  // ----- v1.3: providers, connections, credentials -------------------------------------
  providers = () => this.request<ProvidersResponse>("GET", "/api/providers");
  connections = async () => (await this.request<{ connections: Connection[] }>("GET", "/api/connections")).connections;
  createConnection = async (body: CreateConnectionRequest) =>
    (await this.request<{ connection: Connection }>("POST", "/api/connections", { json: body })).connection;
  patchConnection = async (id: string, body: PatchConnectionRequest) =>
    (await this.request<{ connection: Connection }>("PATCH", `/api/connections/${encodeURIComponent(id)}`, { json: body })).connection;
  deleteConnection = (id: string) => this.request<{ ok: boolean }>("DELETE", `/api/connections/${encodeURIComponent(id)}`);
  /** Four-stage connection test; `allow_generation` sends a probe answer (may cost money). */
  testConnection = (id: string, body: { probe_model?: string; allow_generation: boolean }) =>
    this.request<{ result: ConnectionTestResult; connection: Connection }>("POST", `/api/connections/${encodeURIComponent(id)}/test`, { json: body });
  connectionModels = async (id: string, signal?: AbortSignal) =>
    (await this.request<{ models: ConnectionModel[] }>("GET", `/api/connections/${encodeURIComponent(id)}/models`, { signal })).models;
  credentialsStatus = () => this.request<CredentialStoreStatus>("GET", "/api/credentials/status");
  vaultCreate = (password: string) => this.request<VaultUnlockResult>("POST", "/api/credentials/vault/create", { json: { password } });
  vaultUnlock = (password: string) => this.request<VaultUnlockResult>("POST", "/api/credentials/vault/unlock", { json: { password } });
  vaultLock = () => this.request<CredentialStoreStatus>("POST", "/api/credentials/vault/lock");
  vaultChangePassword = (password: string, newPassword: string) =>
    this.request<CredentialStoreStatus>("POST", "/api/credentials/vault/change-password", { json: { password, new_password: newPassword } });

  // ----- v1.3: projects, consents, bindings --------------------------------------------
  projects = () => this.request<ProjectsResponse>("GET", "/api/projects");
  createProject = async (name: string, localOnly: boolean) =>
    (await this.request<{ project: Project }>("POST", "/api/projects", { json: { name, local_only: localOnly } })).project;
  patchProject = async (id: string, patch: { name?: string; local_only?: boolean }) =>
    (await this.request<{ project: Project }>("PATCH", `/api/projects/${encodeURIComponent(id)}`, { json: patch })).project;
  deleteProject = (id: string) => this.request<{ ok: boolean }>("DELETE", `/api/projects/${encodeURIComponent(id)}`);
  grantConsent = async (connectionId: string, projectId: string) =>
    (await this.request<{ consent: Consent }>("POST", "/api/consents", { json: { connection_id: connectionId, project_id: projectId, scope: "chat" } })).consent;
  revokeConsent = (connectionId: string, projectId: string) =>
    this.request<{ ok: boolean }>("DELETE", `/api/consents/${encodeURIComponent(connectionId)}/${encodeURIComponent(projectId)}`);
  getBinding = (conversationId: string) => this.request<BindingResponse>("GET", `/api/conversations/${encodeURIComponent(conversationId)}/binding`);
  setBinding = (conversationId: string, body: SetBindingRequest) =>
    this.request<BindingResponse>("PUT", `/api/conversations/${encodeURIComponent(conversationId)}/binding`, { json: body });
  setConversationProject = async (conversationId: string, projectId: string) =>
    (await this.request<{ conversation: Conversation }>("PUT", `/api/conversations/${encodeURIComponent(conversationId)}/project`, { json: { project_id: projectId } })).conversation;

  // ----- v1.3: model library ------------------------------------------------------------
  discoverModels = (query: DiscoverQuery, cursor: string | null, limit = 20, signal?: AbortSignal) =>
    this.request<DiscoverResponse>("GET", "/api/models/discover", {
      query: { q: query.q, author: query.author, task: query.task, gguf_only: query.gguf_only, sort: query.sort, limit, cursor: cursor ?? undefined },
      signal,
    });
  repoDetail = (author: string, name: string, signal?: AbortSignal) =>
    this.request<RepoDetail>("GET", `/api/models/repo/${encodeURIComponent(author)}/${encodeURIComponent(name)}`, { signal });
  modelCard = (author: string, name: string, revision?: string, signal?: AbortSignal) =>
    this.request<ModelCard>("GET", `/api/models/card/${encodeURIComponent(author)}/${encodeURIComponent(name)}`, { query: { revision }, signal });
}
