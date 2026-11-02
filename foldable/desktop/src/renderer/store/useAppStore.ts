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
import type { AppInfo, BackendInfo, BackendStatus } from "../../shared-types/bridge";
import { ApiClient, errorMessage, isApiError } from "../api/client";
import { parseJsonData, readSseStream } from "../api/sse";
import type {
  ActivityEvent,
  ChatEvent,
  Conversation,
  Document,
  HardwareProfile,
  Message,
  RuntimeEvent,
  RuntimeState,
  SchedulerEvent,
  Settings,
  SetupStatus,
} from "../api/types";
import { startActivityChannel, type ChannelState } from "../lib/activityChannel";
import { createTokenBatcher } from "../lib/batch";
import { CHAT_ERROR_LABEL, documentWarningText } from "../lib/labels";
import { notify } from "../lib/notify";
import { getPerf } from "../lib/perf";
import { createTrack, reduceActivity, type ActivityTrack } from "./activity";
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

export type SettingsTab = "models" | "storage" | "memory" | "documents" | "preferences" | "info";

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

  scheduler: SchedulerEvent | null;
  activityChannel: ChannelState;

  documents: Document[];

  sidebarCollapsed: boolean;
  settingsOpen: boolean;
  settingsTab: SettingsTab;
  quitting: boolean;

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

let channelStop: (() => void) | null = null;
let reloadTimer: ReturnType<typeof setTimeout> | null = null;

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

  scheduler: null,
  activityChannel: "closed",

  documents: [],

  sidebarCollapsed: false,
  settingsOpen: false,
  settingsTab: "models",
  quitting: false,

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
    set({ streaming: { conversationId: id, assistantId: null, statusText: null, stopping: false } });

    let response: Response;
    try {
      response = await client.sendMessage(id, text);
    } catch (e) {
      set({ streaming: null });
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
          case "error":
            if (typeof data.detail === "string") streamError = data.detail;
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
    if (streamError) notify(streamError, "error");
    void get().loadConversations();
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
    try {
      await window.spaider.requestQuit();
    } catch {
      /* the window is being closed by the main process */
    }
  },
}));
