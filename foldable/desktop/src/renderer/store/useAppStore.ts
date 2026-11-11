/**
 * Application state (zustand). Holds backend connection, setup state,
 * conversations/messages (incl. live streaming), activity tracks, settings,
 * documents and a few UI flags. Network calls go through ApiClient; user
 * feedback through notify() (forwarded to the toast provider by App.tsx).
 *
 * Streaming performance: token/thinking text is batched (~40 ms) before it
 * touches the store, activity events are applied as they arrive (the core
 * sends generating progress at most every 250 ms).
 */
import { create } from "zustand";
import { DEFAULT_HOTKEYS, type AppInfo, type BackendInfo, type BackendStatus, type HotkeyEvent, type TaskFormPrefill } from "../../shared-types/bridge";
import { ApiClient, errorMessage, isApiError } from "../api/client";
import { parseJsonData, readSseStream } from "../api/sse";
import {
  isTerminalJob,
  type Action,
  type ActivityEvent,
  type AgentCapabilities,
  type AnswerRequest,
  type BrowserStatus,
  type ChatEvent,
  type Job,
} from "../api/types";
import type {
  BindingResponse,
  ChatErrorEvent,
  Conversation,
  Document,
  HardwareProfile,
  LiveSession,
  Message,
  Recipe,
  RecipeParameter,
  RuntimeEvent,
  RuntimeState,
  SchedulerEvent,
  Settings,
  SetBindingRequest,
  SetupStatus,
  SharingEvent,
  StopEvent,
  Task,
} from "../api/types";
import { startActivityChannel, type ChannelState } from "../lib/activityChannel";
import { createTokenBatcher } from "../lib/batch";
import { followJob } from "../lib/jobs";
import { CHAT_ERROR_LABEL, documentWarningText } from "../lib/labels";
import { notify } from "../lib/notify";
import { getPerf } from "../lib/perf";
import { createTrack, reduceActivity, type ActivityTrack } from "./activity";
import { consentFromError, type ConsentRequest } from "./hub";
import {
  applyTaskStreamEvent,
  draftFromScopes,
  draftToRequest,
  emptyDraft,
  emptyFeed,
  isTerminalTaskState,
  sortTasksNewestFirst,
  upsertTask,
  validateDraft,
  type TaskDraft,
  type TaskFeed,
} from "./tasks";
import {
  adjustPostprocessing,
  applyChatEvent,
  finalizeDanglingStream,
  nextActiveAfterDelete,
  removeConversation,
  renameConversation as renameInList,
  sortNewestFirst,
  upsertConversation,
} from "./reducers";

export type SettingsTab = "models" | "connections" | "storage" | "memory" | "documents" | "preferences" | "live" | "info";
export type MainView = "chat" | "tasks";
export type TasksView = "create" | "detail" | "recipes";

export interface StreamingState {
  conversationId: string;
  /** Assistant message id (= activity request_id) once the `message` event arrived. */
  assistantId: string | null;
  statusText: string | null;
  /** "Stopp" was clicked; shown as "Wird gestoppt …" until the core confirms. */
  stopping: boolean;
}

const MAX_TRACKS = 30;
const RUNTIME_STATES: RuntimeState[] = ["stopped", "starting", "ready", "error"];

export interface AppState {
  backend: BackendStatus;
  backendInfo: BackendInfo | null;
  appInfo: AppInfo | null;
  client: ApiClient | null;

  setup: SetupStatus | null;
  setupError: string | null;
  settings: Settings | null;
  hardware: HardwareProfile | null;

  conversations: Conversation[];
  conversationsLoaded: boolean;
  activeId: string | null;
  messages: Record<string, Message[]>;
  loadingConversation: boolean;
  streaming: StreamingState | null;
  /** Activity tracks by assistant message id (session memory only). */
  activity: Record<string, ActivityTrack>;
  /** Thinking text by assistant message id (session memory only). */
  thinking: Record<string, string>;
  /** v1.3: binding/project/policy per conversation id (GET /conversations/{id}/binding). */
  bindings: Record<string, BindingResponse>;
  /** v1.3: `event: sharing` by assistant message id (what went to a connection). */
  sharing: Record<string, SharingEvent>;
  /** v1.3: a `consent_required` error waiting for the user's decision. */
  pendingConsent: ConsentRequest | null;

  scheduler: SchedulerEvent | null;
  activityChannel: ChannelState;

  documents: Document[];

  sidebarCollapsed: boolean;
  settingsOpen: boolean;
  settingsTab: SettingsTab;
  quitting: boolean;

  // ----- v1.2: tasks, recipes, live sessions -----------------------------------
  tasks: Task[];
  tasksLoaded: boolean;
  /** Action timeline / notes / question per task id (from the task event stream). */
  taskFeeds: Record<string, TaskFeed>;
  recipes: Recipe[];
  recipesLoaded: boolean;
  agentCapabilities: AgentCapabilities | null;
  liveSessions: LiveSession[];
  mainView: MainView;
  tasksView: TasksView;
  selectedTaskId: string | null;
  taskDraft: TaskDraft;
  taskSubmitting: boolean;
  /** Last global stop (hotkey / api), shown briefly in the UI. */
  lastStop: StopEvent | null;
  /** On-demand browser package for tasks (GET /api/agent/browser). */
  browserStatus: BrowserStatus | null;
  /** Running / last browser download job (kind `browser`). */
  browserJob: Job | null;

  loadBrowserStatus(): Promise<void>;
  installBrowser(): Promise<void>;
  cancelBrowserJob(): Promise<void>;

  setMainView(view: MainView): void;
  loadTasks(): Promise<void>;
  loadRecipes(): Promise<void>;
  loadAgentCapabilities(): Promise<void>;
  loadLiveSessions(): Promise<void>;
  selectTask(id: string | null): Promise<void>;
  /** Opens the create form, optionally prefilled (recipe / overlay). */
  openTaskForm(prefill?: Partial<TaskDraft>): void;
  openTaskFormFromOverlay(prefill: TaskFormPrefill): void;
  showRecipes(): void;
  setTaskDraft(patch: Partial<TaskDraft>): void;
  resetTaskDraft(): void;
  submitTask(): Promise<Task | null>;
  cancelTask(id: string): Promise<void>;
  pauseTask(id: string): Promise<void>;
  resumeTask(id: string): Promise<void>;
  answerTask(id: string, answer: AnswerRequest): Promise<void>;
  saveRecipe(taskId: string, name: string, parameters: RecipeParameter[]): Promise<Recipe | null>;
  deleteRecipe(id: string): Promise<void>;
  startWithRecipe(recipe: Recipe): void;
  /** Subscribes to GET /api/agent/tasks/{id}/events until the task is terminal. */
  followTask(id: string): void;
  applyGlobalTask(task: Task): void;
  applyGlobalLive(session: LiveSession): void;
  applyGlobalStop(ev: StopEvent): void;
  /** Hotkey events from the main process (stop: mark streaming as stopping). */
  applyHotkey(ev: HotkeyEvent): void;
  /** Re-registers the global hotkeys from the current settings (after a change). */
  syncHotkeys(): Promise<void>;

  setBackendStatus(s: BackendStatus): void;
  connect(info: BackendInfo): ApiClient;
  setAppInfo(info: AppInfo): void;
  refreshSetup(): Promise<SetupStatus | null>;
  loadSettings(): Promise<void>;
  updateSettings(patch: Partial<Settings>): Promise<boolean>;
  loadHardware(): Promise<HardwareProfile | null>;

  loadConversations(): Promise<void>;
  selectConversation(id: string | null): Promise<void>;
  newConversation(): Promise<Conversation | null>;
  renameConversation(id: string, title: string): Promise<void>;
  deleteConversation(id: string): Promise<void>;
  sendMessage(content: string): Promise<void>;
  stopGeneration(): Promise<void>;
  /** v1.3 */
  loadBinding(conversationId: string): Promise<void>;
  setBinding(conversationId: string, body: SetBindingRequest): Promise<BindingResponse | null>;
  setConversationProject(conversationId: string, projectId: string): Promise<void>;
  dismissConsent(): void;
  grantConsentAndResend(): Promise<void>;
  useLocalAndResend(): Promise<void>;

  /** Applies an activity event from the chat stream (only for the current request). */
  applyStreamActivity(ev: ActivityEvent): void;
  /** Applies an event from the global channel (post-processing, carries conversation_id). */
  applyGlobalActivity(ev: ActivityEvent): void;
  applyScheduler(ev: SchedulerEvent): void;
  applyRuntime(ev: RuntimeEvent): void;
  /** Subscribes once per session to GET /api/activity; returns the stop function. */
  startActivityChannel(): () => void;

  loadDocuments(): Promise<void>;
  uploadDocument(file: File): Promise<Document | null>;
  deleteDocument(id: string): Promise<void>;
  patchDocument(doc: Document): void;

  toggleSidebar(): void;
  openSettings(tab?: SettingsTab): void;
  closeSettings(): void;
  requestQuit(): Promise<void>;
}

const TERMINAL_DOC_STATES = new Set(["indexed", "failed"]);

/** Keeps the newest MAX_TRACKS tracks (insertion order of the record). */
function pruneTracks(tracks: Record<string, ActivityTrack>): Record<string, ActivityTrack> {
  const keys = Object.keys(tracks);
  if (keys.length <= MAX_TRACKS) return tracks;
  const next: Record<string, ActivityTrack> = {};
  for (const k of keys.slice(keys.length - MAX_TRACKS)) next[k] = tracks[k];
  return next;
}

/** Actions from GET /tasks/{id} merged with those already received through events. */
function mergeActions(current: Action[], loaded: Action[]): Action[] {
  const byId = new Map<string, Action>();
  for (const a of loaded) byId.set(a.action_id, a);
  for (const a of current) if (!byId.has(a.action_id)) byId.set(a.action_id, a);
  return [...byId.values()].sort((a, b) => a.seq - b.seq);
}

let channelStop: (() => void) | null = null;
let reloadTimer: ReturnType<typeof setTimeout> | null = null;
/** Open task event streams by task id. */
const taskFollowers = new Map<string, AbortController>();
const TASK_FOLLOW_RETRY_MS = 1000;
const TASK_FOLLOW_MAX_RETRIES = 30;

/** Tells the main process which conversations are streaming (for the stop hotkey). */
function reportActive(streaming: StreamingState | null): void {
  try {
    window.spaider?.reportActiveConversations(streaming ? [streaming.conversationId] : []);
  } catch {
    /* no bridge (tests) */
  }
}

export const useAppStore = create<AppState>()((set, get) => ({
  backend: { state: "starting", message: "SpAIder startet …" },
  backendInfo: null,
  appInfo: null,
  client: null,

  setup: null,
  setupError: null,
  settings: null,
  hardware: null,

  conversations: [],
  conversationsLoaded: false,
  activeId: null,
  messages: {},
  loadingConversation: false,
  streaming: null,
  activity: {},
  thinking: {},
  bindings: {},
  sharing: {},
  pendingConsent: null,

  scheduler: null,
  activityChannel: "closed",

  documents: [],

  sidebarCollapsed: false,
  settingsOpen: false,
  settingsTab: "models",
  quitting: false,

  tasks: [],
  tasksLoaded: false,
  taskFeeds: {},
  recipes: [],
  recipesLoaded: false,
  agentCapabilities: null,
  liveSessions: [],
  mainView: "chat",
  tasksView: "create",
  selectedTaskId: null,
  taskDraft: emptyDraft(),
  taskSubmitting: false,
  lastStop: null,
  browserStatus: null,
  browserJob: null,

  // ----- v1.2: browser package -------------------------------------------------
  async loadBrowserStatus() {
    const { client } = get();
    if (!client) return;
    try {
      set({ browserStatus: await client.agentBrowser() });
    } catch {
      // Core without the browser endpoint: the toggle stays available, no hint.
      set({ browserStatus: null });
    }
  },
  async installBrowser() {
    const { client, browserJob } = get();
    if (!client) return;
    if (browserJob && !isTerminalJob(browserJob)) return;
    try {
      const res = await client.installBrowser();
      if (!res.job) {
        notify("Der Browser ist bereits installiert.", "info");
        await get().loadBrowserStatus();
        return;
      }
      set({ browserJob: res.job });
      const final = await followJob(client, res.job.id, (job) => set({ browserJob: job }));
      set({ browserJob: final });
      await get().loadBrowserStatus();
      void get().loadAgentCapabilities();
      if (final.state === "done") notify("Browser für Aufgaben installiert.", "success");
      else if (final.state === "failed") notify(final.error?.detail ?? "Browser-Download fehlgeschlagen.", "error");
    } catch (e) {
      notify(`Browser-Download fehlgeschlagen: ${errorMessage(e)}`, "error");
    }
  },
  async cancelBrowserJob() {
    const { client, browserJob } = get();
    if (!client || !browserJob || isTerminalJob(browserJob)) return;
    try {
      set({ browserJob: await client.cancelJob(browserJob.id) });
    } catch (e) {
      notify(`Abbrechen fehlgeschlagen: ${errorMessage(e)}`, "error");
    }
  },

  // ----- v1.2: tasks ----------------------------------------------------------
  setMainView(view) {
    set({ mainView: view });
    if (view === "tasks") {
      const s = get();
      if (!s.tasksLoaded) void s.loadTasks();
      if (!s.agentCapabilities) void s.loadAgentCapabilities();
    }
  },
  async loadTasks() {
    const { client } = get();
    if (!client) return;
    try {
      const list = sortTasksNewestFirst(await client.tasks());
      set({ tasks: list, tasksLoaded: true });
      // Keep following every task that is still running.
      for (const t of list) if (!isTerminalTaskState(t.state)) get().followTask(t.id);
    } catch (e) {
      set({ tasksLoaded: true });
      notify(`Aufgaben konnten nicht geladen werden: ${errorMessage(e)}`, "error");
    }
  },
  async loadRecipes() {
    const { client } = get();
    if (!client) return;
    try {
      set({ recipes: await client.recipes(), recipesLoaded: true });
    } catch (e) {
      set({ recipesLoaded: true });
      notify(`Abläufe konnten nicht geladen werden: ${errorMessage(e)}`, "error");
    }
  },
  async loadAgentCapabilities() {
    const { client } = get();
    if (!client) return;
    try {
      set({ agentCapabilities: await client.agentCapabilities() });
    } catch {
      // Older core without the agent: the panel simply shows no tool details.
      set({ agentCapabilities: null });
    }
  },
  async loadLiveSessions() {
    const { client } = get();
    if (!client) return;
    try {
      set({ liveSessions: await client.liveSessions() });
    } catch {
      set({ liveSessions: [] });
    }
  },
  async selectTask(id) {
    const { client } = get();
    if (!id) {
      set({ selectedTaskId: null, tasksView: "create" });
      return;
    }
    set({ selectedTaskId: id, tasksView: "detail", mainView: "tasks" });
    if (!client) return;
    try {
      const { task, actions } = await client.getTask(id);
      set((s) => {
        const existing = s.taskFeeds[id];
        const feed: TaskFeed = existing ? { ...existing, actions: mergeActions(existing.actions, actions), question: task.pending_question } : { ...emptyFeed(actions), question: task.pending_question };
        return { tasks: upsertTask(s.tasks, task), taskFeeds: { ...s.taskFeeds, [id]: feed } };
      });
      if (!isTerminalTaskState(task.state)) get().followTask(id);
    } catch (e) {
      if (isApiError(e) && e.status === 404) {
        set((s) => ({ tasks: s.tasks.filter((t) => t.id !== id), selectedTaskId: null, tasksView: "create" }));
      }
      notify(`Aufgabe konnte nicht geladen werden: ${errorMessage(e)}`, "error");
    }
  },
  openTaskForm(prefill) {
    const { settings } = get();
    const base = emptyDraft({ max_steps: settings?.agent_max_steps, max_seconds: settings?.agent_max_seconds });
    set({ taskDraft: { ...base, ...(prefill ?? {}) }, tasksView: "create", selectedTaskId: null, mainView: "tasks" });
    const s = get();
    if (!s.tasksLoaded) void s.loadTasks();
    if (!s.agentCapabilities) void s.loadAgentCapabilities();
  },
  openTaskFormFromOverlay(prefill) {
    get().openTaskForm({
      goal: prefill.goal,
      live_session_id: prefill.live_session_id,
      conversation_id: prefill.conversation_id,
      windowsEnabled: Boolean(prefill.source_id),
      windowSourceIds: prefill.source_id ? [prefill.source_id] : [],
      allowedProcesses: prefill.process ? [prefill.process] : [],
    });
  },
  showRecipes() {
    set({ tasksView: "recipes", mainView: "tasks", selectedTaskId: null });
    if (!get().recipesLoaded) void get().loadRecipes();
  },
  setTaskDraft(patch) {
    set((s) => ({ taskDraft: { ...s.taskDraft, ...patch } }));
  },
  resetTaskDraft() {
    const { settings } = get();
    set({ taskDraft: emptyDraft({ max_steps: settings?.agent_max_steps, max_seconds: settings?.agent_max_seconds }) });
  },
  async submitTask() {
    const { client, taskDraft } = get();
    if (!client) return null;
    const problem = validateDraft(taskDraft);
    if (problem) {
      notify(problem, "error");
      return null;
    }
    set({ taskSubmitting: true });
    try {
      const task = await client.createTask(draftToRequest(taskDraft));
      set((s) => ({
        tasks: upsertTask(s.tasks, task),
        taskFeeds: { ...s.taskFeeds, [task.id]: { ...emptyFeed(), question: task.pending_question } },
        selectedTaskId: task.id,
        tasksView: "detail",
      }));
      get().resetTaskDraft();
      get().followTask(task.id);
      notify("Aufgabe gestartet.", "success");
      return task;
    } catch (e) {
      notify(`Aufgabe konnte nicht gestartet werden: ${errorMessage(e)}`, "error");
      return null;
    } finally {
      set({ taskSubmitting: false });
    }
  },
  async cancelTask(id) {
    const { client } = get();
    if (!client) return;
    try {
      const task = await client.cancelTask(id);
      set((s) => ({ tasks: upsertTask(s.tasks, task) }));
    } catch (e) {
      notify(`Stoppen fehlgeschlagen: ${errorMessage(e)}`, "error");
    }
  },
  async pauseTask(id) {
    const { client } = get();
    if (!client) return;
    try {
      const task = await client.pauseTask(id);
      set((s) => ({ tasks: upsertTask(s.tasks, task) }));
    } catch (e) {
      notify(`Pausieren fehlgeschlagen: ${errorMessage(e)}`, "error");
    }
  },
  async resumeTask(id) {
    const { client } = get();
    if (!client) return;
    try {
      const task = await client.resumeTask(id);
      set((s) => ({ tasks: upsertTask(s.tasks, task) }));
      get().followTask(id);
    } catch (e) {
      notify(`Fortsetzen fehlgeschlagen: ${errorMessage(e)}`, "error");
    }
  },
  async answerTask(id, answer) {
    const { client } = get();
    if (!client) return;
    try {
      const task = await client.answerTask(id, answer);
      set((s) => {
        const feed = s.taskFeeds[id];
        return {
          tasks: upsertTask(s.tasks, task),
          taskFeeds: feed ? { ...s.taskFeeds, [id]: { ...feed, question: task.pending_question } } : s.taskFeeds,
        };
      });
      get().followTask(id);
    } catch (e) {
      notify(`Antwort konnte nicht übermittelt werden: ${errorMessage(e)}`, "error");
    }
  },
  async saveRecipe(taskId, name, parameters) {
    const { client } = get();
    if (!client) return null;
    try {
      const recipe = await client.saveRecipe(taskId, name, parameters);
      set((s) => ({ recipes: [recipe, ...s.recipes.filter((r) => r.id !== recipe.id)] }));
      // The core owns the list (other recipes may exist): re-sync.
      void get().loadRecipes();
      notify(`Ablauf „${recipe.name}“ gespeichert.`, "success");
      return recipe;
    } catch (e) {
      notify(`Ablauf konnte nicht gespeichert werden: ${errorMessage(e)}`, "error");
      return null;
    }
  },
  async deleteRecipe(id) {
    const { client } = get();
    if (!client) return;
    try {
      await client.deleteRecipe(id);
      set((s) => ({ recipes: s.recipes.filter((r) => r.id !== id) }));
      notify("Ablauf gelöscht.", "success");
    } catch (e) {
      notify(`Löschen fehlgeschlagen: ${errorMessage(e)}`, "error");
    }
  },
  startWithRecipe(recipe) {
    const { settings } = get();
    const base = emptyDraft({ max_steps: settings?.agent_max_steps, max_seconds: settings?.agent_max_seconds });
    const draft = draftFromScopes({ ...base, goal: recipe.purpose_de, recipe_id: recipe.id }, recipe.scope);
    set({ taskDraft: draft, tasksView: "create", selectedTaskId: null, mainView: "tasks" });
  },
  followTask(id) {
    const { client } = get();
    if (!client || taskFollowers.has(id)) return;
    const controller = new AbortController();
    taskFollowers.set(id, controller);
    let retries = 0;
    const run = async (): Promise<void> => {
      while (!controller.signal.aborted) {
        let done = false;
        try {
          await client.taskEvents(
            id,
            (ev) => {
              set((s) => {
                const feed = s.taskFeeds[id] ?? emptyFeed();
                const next = applyTaskStreamEvent(feed, ev);
                const patch: Partial<AppState> = { taskFeeds: { ...s.taskFeeds, [id]: next.feed } };
                if (next.task) patch.tasks = upsertTask(s.tasks, next.task);
                return patch;
              });
              if (ev.type === "done") done = true;
            },
            controller.signal,
          );
        } catch {
          /* dropped / refused: fall through */
        }
        if (controller.signal.aborted || done) break;
        // Stream ended without `done`: re-check the task, resubscribe while it runs.
        try {
          const { task } = await client.getTask(id);
          set((s) => ({ tasks: upsertTask(s.tasks, task) }));
          if (isTerminalTaskState(task.state)) break;
        } catch {
          break;
        }
        retries += 1;
        if (retries > TASK_FOLLOW_MAX_RETRIES) break;
        await new Promise((r) => setTimeout(r, TASK_FOLLOW_RETRY_MS));
      }
      taskFollowers.delete(id);
    };
    void run();
  },
  applyGlobalTask(task) {
    set((s) => ({ tasks: upsertTask(s.tasks, task), tasksLoaded: true }));
    if (!isTerminalTaskState(task.state)) get().followTask(task.id);
  },
  applyGlobalLive(session) {
    set((s) => {
      const idx = s.liveSessions.findIndex((x) => x.id === session.id);
      const list = idx === -1 ? [session, ...s.liveSessions] : s.liveSessions.map((x) => (x.id === session.id ? session : x));
      return { liveSessions: list.filter((x) => x.state !== "ended") };
    });
  },
  applyGlobalStop(ev) {
    // A stop the launcher triggered from the hotkey arrives here as "api" a
    // moment later; keep the more precise local source for that window.
    const local = get().lastStop;
    const recentHotkey = local?.source === "hotkey" && Date.now() - Date.parse(local.at) < 3000;
    set({ lastStop: recentHotkey ? local : ev });
    // The core locked everything; refresh the lists so the badges tell the truth.
    void get().loadTasks();
  },
  applyHotkey(ev) {
    if (ev.kind === "stop") {
      set((s) => (s.streaming ? { streaming: { ...s.streaming, stopping: true } } : {}));
      set({ lastStop: { source: "hotkey", at: new Date().toISOString() } });
      setTimeout(() => void get().loadTasks(), 300);
    }
  },
  async syncHotkeys() {
    const { settings } = get();
    const bridge = window.spaider;
    if (!bridge?.registerHotkeys) return;
    try {
      const result = await bridge.registerHotkeys({
        overlay: settings?.hotkey_overlay || DEFAULT_HOTKEYS.overlay,
        stop: settings?.hotkey_stop || DEFAULT_HOTKEYS.stop,
      });
      for (const err of result.errors) notify(err, "error");
    } catch (e) {
      notify(`Hotkeys konnten nicht registriert werden: ${errorMessage(e)}`, "error");
    }
  },

  // ----- lifecycle ---------------------------------------------------------
  setBackendStatus(backend) {
    set({ backend });
    if (backend.state === "stopping") set({ quitting: true });
  },
  connect(info) {
    const client = new ApiClient(info);
    set({ backendInfo: info, client });
    return client;
  },
  setAppInfo(appInfo) {
    set({ appInfo });
  },
  async refreshSetup() {
    const { client } = get();
    if (!client) return null;
    try {
      const setup = await client.setupStatus();
      set({ setup, setupError: null });
      return setup;
    } catch (e) {
      set({ setupError: errorMessage(e) });
      return null;
    }
  },
  async loadSettings() {
    const { client } = get();
    if (!client) return;
    try {
      set({ settings: await client.getSettings() });
    } catch (e) {
      notify(`Einstellungen konnten nicht geladen werden: ${errorMessage(e)}`, "error");
    }
  },
  async updateSettings(patch) {
    const { client, settings } = get();
    if (!client) return false;
    const previous = settings;
    if (settings) set({ settings: { ...settings, ...patch } });
    try {
      set({ settings: await client.patchSettings(patch) });
      // The launcher owns the global hotkeys and the tray behaviour: re-apply.
      if (patch.hotkey_overlay !== undefined || patch.hotkey_stop !== undefined) void get().syncHotkeys();
      if (patch.tray_mode !== undefined) void window.spaider?.setTrayMode?.(Boolean(get().settings?.tray_mode)).catch(() => undefined);
      return true;
    } catch (e) {
      set({ settings: previous });
      notify(`Einstellung nicht übernommen: ${errorMessage(e)}`, "error");
      return false;
    }
  },
  async loadHardware() {
    const { client } = get();
    if (!client) return null;
    try {
      const hardware = await client.hardware();
      set({ hardware });
      return hardware;
    } catch {
      // v1 core without /api/system/hardware: the UI simply hides hardware details.
      set({ hardware: null });
      return null;
    }
  },

  // ----- conversations ------------------------------------------------------
  async loadConversations() {
    const { client } = get();
    if (!client) return;
    try {
      const list = sortNewestFirst(await client.conversations());
      set({ conversations: list, conversationsLoaded: true });
    } catch (e) {
      notify(`Gespräche konnten nicht geladen werden: ${errorMessage(e)}`, "error");
      set({ conversationsLoaded: true });
    }
  },
  async selectConversation(id) {
    const { client, messages, streaming } = get();
    set({ activeId: id });
    if (!id || !client) return;
    void get().loadBinding(id);
    // Keep the live buffer while a stream for this conversation is running.
    if (streaming?.conversationId === id && messages[id]) return;
    set({ loadingConversation: true });
    try {
      const { conversation, messages: loaded } = await client.getConversation(id);
      set((s) => ({
        messages: { ...s.messages, [id]: loaded },
        conversations: upsertConversation(s.conversations, conversation),
      }));
    } catch (e) {
      if (isApiError(e) && e.status === 404) {
        set((s) => ({ conversations: removeConversation(s.conversations, id), activeId: null }));
      }
      notify(`Gespräch konnte nicht geladen werden: ${errorMessage(e)}`, "error");
    } finally {
      set({ loadingConversation: false });
    }
  },
  async newConversation() {
    const { client } = get();
    if (!client) return null;
    try {
      const conv = await client.createConversation(null);
      set((s) => ({
        conversations: upsertConversation(s.conversations, conv),
        messages: { ...s.messages, [conv.id]: [] },
        activeId: conv.id,
      }));
      void get().loadBinding(conv.id);
      return conv;
    } catch (e) {
      notify(`Gespräch konnte nicht angelegt werden: ${errorMessage(e)}`, "error");
      return null;
    }
  },
  async renameConversation(id, title) {
    const { client } = get();
    const trimmed = title.trim();
    if (!client || !trimmed) return;
    const before = get().conversations;
    set({ conversations: renameInList(before, id, trimmed) });
    try {
      const conv = await client.renameConversation(id, trimmed);
      set((s) => ({ conversations: upsertConversation(s.conversations, conv) }));
    } catch (e) {
      set({ conversations: before });
      notify(`Umbenennen fehlgeschlagen: ${errorMessage(e)}`, "error");
    }
  },
  async deleteConversation(id) {
    const { client, conversations, activeId } = get();
    if (!client) return;
    try {
      await client.deleteConversation(id);
      const nextActive = nextActiveAfterDelete(conversations, id, activeId);
      set((s) => {
        const messages = { ...s.messages };
        delete messages[id];
        return { conversations: removeConversation(s.conversations, id), messages };
      });
      if (nextActive !== activeId) await get().selectConversation(nextActive);
      notify("Gespräch gelöscht.", "success");
    } catch (e) {
      notify(`Löschen fehlgeschlagen: ${errorMessage(e)}`, "error");
    }
  },
  async sendMessage(content) {
    const text = content.trim();
    const { client, streaming } = get();
    if (!client || !text || streaming) return;
    let convId = get().activeId;
    if (!convId) {
      const conv = await get().newConversation();
      if (!conv) return;
      convId = conv.id;
    }
    const id = convId;
    const streamingState: StreamingState = { conversationId: id, assistantId: null, statusText: null, stopping: false };
    set({ streaming: streamingState });
    reportActive(streamingState);

    let response: Response;
    try {
      response = await client.sendMessage(id, text);
    } catch (e) {
      set({ streaming: null });
      reportActive(null);
      const label = isApiError(e) ? CHAT_ERROR_LABEL[e.code] : undefined;
      notify(label ?? errorMessage(e), "error");
      return;
    }

    const perf = getPerf();
    perf?.start("stream");

    // Token text reaches the store at most every ~40 ms.
    const tokens = createTokenBatcher((chunk) => {
      set((s) => ({ messages: { ...s.messages, [id]: applyChatEvent(s.messages[id] ?? [], { type: "token", data: { text: chunk } }) } }));
    });
    let assistantId: string | null = null;
    const thinking = createTokenBatcher((chunk) => {
      const mid = assistantId;
      if (!mid) return;
      set((s) => ({ thinking: { ...s.thinking, [mid]: (s.thinking[mid] ?? "") + chunk } }));
    });

    let streamError: string | null = null;
    let errorEvent: ChatErrorEvent | null = null;
    let gotDone = false;
    try {
      await readSseStream(response, (ev) => {
        const data = parseJsonData<Record<string, unknown>>(ev);
        if (!data) return;
        const type = ev.event as ChatEvent["type"];
        switch (type) {
          case "token":
            if (typeof data.text === "string") tokens.push(data.text);
            return;
          case "thinking":
            if (typeof data.text === "string") thinking.push(data.text);
            return;
          case "activity":
            get().applyStreamActivity(data as unknown as ActivityEvent);
            return;
          case "status": {
            const statusText = typeof data.message_de === "string" ? data.message_de : null;
            set((s) => (s.streaming ? { streaming: { ...s.streaming, statusText } } : {}));
            return;
          }
          case "message": {
            const assistant = (data as { assistant_message?: Message }).assistant_message;
            assistantId = assistant?.id ?? null;
            set((s) => (s.streaming ? { streaming: { ...s.streaming, assistantId } } : {}));
            break;
          }
          case "done":
            gotDone = true;
            break;
          case "sharing": {
            const mid = assistantId;
            if (mid) set((s) => ({ sharing: { ...s.sharing, [mid]: data as unknown as SharingEvent } }));
            return;
          }
          case "error":
            if (typeof data.detail === "string") streamError = data.detail;
            errorEvent = {
              ...(data as unknown as ChatErrorEvent),
              detail: typeof data.detail === "string" ? data.detail : "Bei der Antwort ist ein Fehler aufgetreten.",
              code: typeof data.code === "string" ? data.code : "error",
            };
            break;
          default:
            break;
        }
        // Ordering: queued text must land before sources/done/error are applied.
        tokens.flush();
        thinking.flush();
        const event = { type, data } as ChatEvent;
        set((s) => ({ messages: { ...s.messages, [id]: applyChatEvent(s.messages[id] ?? [], event) } }));
      });
    } catch (e) {
      streamError = streamError ?? errorMessage(e);
    }
    tokens.dispose();
    thinking.dispose();
    perf?.stop();

    set((s) => {
      const list = s.messages[id] ?? [];
      const finalized = gotDone ? list : finalizeDanglingStream(list, streamError);
      return { messages: { ...s.messages, [id]: finalized }, streaming: null };
    });
    reportActive(null);
    // A consent_required error becomes the consent dialog; every other error is shown on the message.
    const consent = errorEvent ? consentFromError(id, text, errorEvent, assistantId ? (get().sharing[assistantId] ?? null) : null, get().bindings[id] ?? null) : null;
    if (consent) set({ pendingConsent: consent });
    else if (streamError) notify(streamError, "error");
    void get().loadConversations();
    if (errorEvent) void get().loadBinding(id);
  },
  async stopGeneration() {
    const { client, streaming } = get();
    if (!client || !streaming) return;
    // Local, immediate feedback; the core's `answer_done: cancelled` replaces it.
    set((s) => (s.streaming ? { streaming: { ...s.streaming, stopping: true } } : {}));
    try {
      await client.stopGeneration(streaming.conversationId);
    } catch (e) {
      set((s) => (s.streaming ? { streaming: { ...s.streaming, stopping: false } } : {}));
      notify(`Stoppen fehlgeschlagen: ${errorMessage(e)}`, "error");
    }
  },

  // ----- v1.3: bindings, projects, consent ------------------------------------------
  async loadBinding(conversationId) {
    const { client } = get();
    if (!client) return;
    try {
      const res = await client.getBinding(conversationId);
      set((s) => ({ bindings: { ...s.bindings, [conversationId]: res } }));
    } catch {
      /* older core without bindings: the header falls back to the local model */
    }
  },
  async setBinding(conversationId, body) {
    const { client } = get();
    if (!client) return null;
    try {
      const res = await client.setBinding(conversationId, body);
      set((s) => ({
        bindings: { ...s.bindings, [conversationId]: res },
        conversations: s.conversations.map((c) => (c.id === conversationId ? { ...c, binding: res.binding } : c)),
      }));
      return res;
    } catch (e) {
      notify(`Modellbindung nicht übernommen: ${errorMessage(e)}`, "error");
      return null;
    }
  },
  async setConversationProject(conversationId, projectId) {
    const { client } = get();
    if (!client) return;
    try {
      const conv = await client.setConversationProject(conversationId, projectId);
      set((s) => ({ conversations: upsertConversation(s.conversations, conv) }));
      await get().loadBinding(conversationId);
    } catch (e) {
      notify(`Projekt nicht übernommen: ${errorMessage(e)}`, "error");
    }
  },
  dismissConsent() {
    set({ pendingConsent: null });
  },
  async grantConsentAndResend() {
    const { client, pendingConsent } = get();
    if (!client || !pendingConsent) return;
    set({ pendingConsent: null });
    try {
      await client.grantConsent(pendingConsent.connectionId, pendingConsent.projectId);
    } catch (e) {
      notify(`Freigabe nicht möglich: ${errorMessage(e)}`, "error");
      return;
    }
    await get().loadBinding(pendingConsent.conversationId);
    if (get().activeId !== pendingConsent.conversationId) await get().selectConversation(pendingConsent.conversationId);
    await get().sendMessage(pendingConsent.text);
  },
  async useLocalAndResend() {
    const { pendingConsent } = get();
    if (!pendingConsent) return;
    set({ pendingConsent: null });
    const res = await get().setBinding(pendingConsent.conversationId, { kind: "local" });
    if (!res) return;
    if (get().activeId !== pendingConsent.conversationId) await get().selectConversation(pendingConsent.conversationId);
    await get().sendMessage(pendingConsent.text);
  },

  // ----- activity -----------------------------------------------------------------
  applyStreamActivity(ev) {
    set((s) => {
      const current = s.streaming?.assistantId;
      // Events of stopped/foreign requests are discarded by request_id.
      if (!current || ev.request_id !== current) return {};
      const existing = s.activity[ev.request_id];
      const next = existing ? reduceActivity(existing, ev) : createTrack(ev.request_id, ev, Date.now());
      if (next === existing) return {};
      return { activity: pruneTracks({ ...s.activity, [ev.request_id]: next }) };
    });
  },
  applyGlobalActivity(ev) {
    set((s) => {
      const patch: Partial<AppState> = {};
      const existing = s.activity[ev.request_id];
      if (existing) {
        const next = reduceActivity(existing, ev);
        if (next !== existing) patch.activity = { ...s.activity, [ev.request_id]: next };
      }
      if (ev.conversation_id) {
        if (ev.phase === "postprocess_pending" && ev.state !== "failed") {
          patch.conversations = adjustPostprocessing(s.conversations, ev.conversation_id, 1);
        } else if (ev.phase === "postprocess_done" || (ev.phase.startsWith("postprocess") && ev.state === "failed")) {
          patch.conversations = adjustPostprocessing(s.conversations, ev.conversation_id, -1);
        }
      }
      return patch;
    });
    // Re-sync the authoritative counts shortly after (debounced).
    if (ev.conversation_id) {
      if (reloadTimer) clearTimeout(reloadTimer);
      reloadTimer = setTimeout(() => {
        reloadTimer = null;
        void get().loadConversations();
      }, 800);
    }
  },
  applyScheduler(ev) {
    set({ scheduler: ev });
  },
  applyRuntime(ev) {
    const state = ev.state ?? ev.runtime_state;
    if (state && RUNTIME_STATES.includes(state)) {
      set((s) => (s.setup && s.setup.runtime_state !== state ? { setup: { ...s.setup, runtime_state: state } } : {}));
    }
    void get().refreshSetup();
  },
  startActivityChannel() {
    if (channelStop) return channelStop;
    const { client } = get();
    if (!client) return () => undefined;
    const stop = startActivityChannel(client, {
      onActivity: (ev) => get().applyGlobalActivity(ev),
      onScheduler: (ev) => get().applyScheduler(ev),
      onRuntime: (ev) => get().applyRuntime(ev),
      onLive: (session) => get().applyGlobalLive(session),
      onTask: (task) => get().applyGlobalTask(task),
      onStop: (ev) => get().applyGlobalStop(ev),
      onState: (state) => set({ activityChannel: state }),
    });
    channelStop = () => {
      stop();
      channelStop = null;
      set({ activityChannel: "closed", scheduler: null });
    };
    return channelStop;
  },

  // ----- documents -------------------------------------------------------------
  async loadDocuments() {
    const { client } = get();
    if (!client) return;
    try {
      set({ documents: await client.documents() });
    } catch (e) {
      notify(`Dokumente konnten nicht geladen werden: ${errorMessage(e)}`, "error");
    }
  },
  async uploadDocument(file) {
    const { client, activeId } = get();
    if (!client) return null;
    try {
      const doc = await client.uploadDocument(file, activeId);
      get().patchDocument(doc);
      for (const w of doc.warnings ?? []) notify(documentWarningText(w), "error");
      if (TERMINAL_DOC_STATES.has(doc.status)) {
        notify(
          doc.status === "indexed"
            ? `„${doc.name}“ importiert (${doc.facts_count} Fakten).`
            : `„${doc.name}“ konnte nicht verarbeitet werden.`,
          doc.status === "indexed" ? "success" : "error",
        );
      } else {
        notify(`„${doc.name}“ importiert – wird indexiert …`, "info");
        void client
          .documentEvents(doc.id, (snapshot) => {
            get().patchDocument(snapshot);
            if (TERMINAL_DOC_STATES.has(snapshot.status)) {
              for (const w of snapshot.warnings ?? []) {
                if (!doc.warnings?.some((x) => x.code === w.code)) notify(documentWarningText(w), "error");
              }
              notify(
                snapshot.status === "indexed"
                  ? `„${snapshot.name}“ indexiert (${snapshot.facts_count} Fakten).`
                  : `„${snapshot.name}“: Indexierung fehlgeschlagen.`,
                snapshot.status === "indexed" ? "success" : "error",
              );
            }
          })
          .catch(() => get().loadDocuments());
      }
      return doc;
    } catch (e) {
      notify(`Import fehlgeschlagen: ${errorMessage(e)}`, "error");
      return null;
    }
  },
  async deleteDocument(id) {
    const { client } = get();
    if (!client) return;
    try {
      await client.deleteDocument(id);
      set((s) => ({ documents: s.documents.filter((d) => d.id !== id) }));
      notify("Dokument gelöscht.", "success");
    } catch (e) {
      notify(`Löschen fehlgeschlagen: ${errorMessage(e)}`, "error");
    }
  },
  patchDocument(doc) {
    set((s) => {
      const exists = s.documents.some((d) => d.id === doc.id);
      return {
        documents: exists ? s.documents.map((d) => (d.id === doc.id ? doc : d)) : [doc, ...s.documents],
      };
    });
  },

  // ----- ui ------------------------------------------------------------------
  toggleSidebar() {
    set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed }));
  },
  openSettings(tab) {
    set((s) => ({ settingsOpen: true, settingsTab: tab ?? s.settingsTab }));
  },
  closeSettings() {
    set({ settingsOpen: false });
  },
  async requestQuit() {
    set({ quitting: true, settingsOpen: false });
    channelStop?.();
    for (const c of taskFollowers.values()) c.abort();
    taskFollowers.clear();
    try {
      await window.spaider.requestQuit();
    } catch {
      /* the window is being closed by the main process */
    }
  },
}));
