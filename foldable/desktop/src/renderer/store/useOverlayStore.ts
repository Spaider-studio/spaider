/**
 * State of the live overlay window (route `#/overlay`). Separate from the
 * main store: the overlay has its own backend connection (same token through
 * the bridge), its own source selection, one live session and the turns of
 * that session's conversation.
 *
 * Nothing here acts on the target application: the overlay only asks
 * questions (`POST /api/live/sessions/{id}/ask`); tasks are created
 * explicitly in the main window ("Als Aufgabe ausführen" only prefills).
 *
 * Session lifetime: the session lives while the overlay is visible. Hiding
 * the overlay (Esc, toggle hotkey, tray, close) ends it with
 * `DELETE /api/live/sessions/{id}` - unless a task that is not finished
 * references it - and the next question creates a fresh session on the same
 * conversation (`conversationId`), so the turns stay.
 */
import { create } from "zustand";
import type { BackendInfo, BackendStatus, HotkeyEvent, OverlayShownEvent } from "../../shared-types/bridge";
import { ApiClient, errorMessage, isApiError } from "../api/client";
import {
  isWindowSource,
  type ActivityEvent,
  type LiveSession,
  type LiveSource,
  type LiveSources,
  type Message,
  type ObservationEvent,
  type Settings,
  type SnapshotQuality,
  type StopEvent,
} from "../api/types";
import { startActivityChannel } from "../lib/activityChannel";
import { consumeChatStream } from "../lib/chatStream";
import { notify } from "../lib/notify";
import { getPerf } from "../lib/perf";
import { createTrack, reduceActivity, type ActivityTrack } from "./activity";
import {
  applyShown,
  closePicker,
  confirmSource,
  effectiveTarget,
  initialSourceState,
  isSessionReferenced,
  openPicker,
  type OverlaySourceState,
} from "./overlay";
import { applyChatEvent, finalizeDanglingStream } from "./reducers";

export interface OverlayStreaming {
  conversationId: string;
  assistantId: string | null;
  statusText: string | null;
  stopping: boolean;
}

const LIVE_ERROR_LABEL: Record<string, string> = {
  source_not_found: "Die Quelle existiert nicht mehr. Bitte neu wählen.",
  source_is_self: "SpAIder selbst kann nicht beobachtet werden.",
  live_unavailable: "Live-Beobachtung ist auf diesem Desktop nicht verfügbar.",
  source_lost: "Quelle verloren – das Fenster ist nicht mehr vorhanden.",
  vision_unavailable: "Kein Bildmodell aktiv – die Frage wurde ohne Bild gestellt.",
  generation_running: "Es läuft bereits eine Antwort.",
  no_model: "Kein Chat-Modell aktiv (Einstellungen → Modelle).",
  runtime_not_ready: "Die Laufzeit ist noch nicht bereit.",
};

export interface OverlayAppState {
  backend: BackendStatus;
  client: ApiClient | null;
  settings: Settings | null;
  visible: boolean;
  /** Counter from the main process, used for the first-paint report. */
  showId: number;

  sources: LiveSources | null;
  sourcesLoading: boolean;
  sourceState: OverlaySourceState;

  session: LiveSession | null;
  /** Conversation shared by all live sessions of this overlay (survives a session end). */
  conversationId: string | null;
  quality: SnapshotQuality | null;
  observation: ObservationEvent | null;
  preview: string | null;
  previewLoading: boolean;
  /** Last problem shown above the input (source lost, 409 …). */
  problem: string | null;

  messages: Message[];
  streaming: OverlayStreaming | null;
  activity: Record<string, ActivityTrack>;
  lastStop: StopEvent | null;

  setBackendStatus(s: BackendStatus): void;
  connect(info: BackendInfo): void;
  loadSettings(): Promise<void>;
  onShown(e: OverlayShownEvent): void;
  hide(): void;
  /** The main process hid the window (Esc, toggle hotkey, tray, close). */
  onHidden(): void;
  /** Ends the live session (DELETE) unless a running task references it; keeps the conversation. */
  endSession(): Promise<void>;
  refreshSources(): Promise<void>;
  openPicker(): void;
  closePicker(): void;
  /** Picker click or "Bestätigen": confirms the source (deliberate switch when a session exists). */
  choose(source: LiveSource): Promise<void>;
  ask(text: string): Promise<void>;
  stop(): Promise<void>;
  refreshPreview(): Promise<void>;
  setPreviewEnabled(enabled: boolean): Promise<void>;
  openInMain(): Promise<void>;
  runAsTask(text: string): Promise<void>;
  applyHotkey(ev: HotkeyEvent): void;
  applyLive(session: LiveSession): void;
}

let channelStop: (() => void) | null = null;

function reportActive(streaming: OverlayStreaming | null): void {
  try {
    window.spaider?.reportActiveConversations(streaming ? [streaming.conversationId] : []);
  } catch {
    /* no bridge (tests) */
  }
}

function liveErrorText(e: unknown): string {
  if (isApiError(e) && LIVE_ERROR_LABEL[e.code]) return LIVE_ERROR_LABEL[e.code];
  return errorMessage(e);
}

export const useOverlayStore = create<OverlayAppState>()((set, get) => ({
  backend: { state: "starting", message: "SpAIder startet …" },
  client: null,
  settings: null,
  visible: false,
  showId: 0,

  sources: null,
  sourcesLoading: false,
  sourceState: initialSourceState(),

  session: null,
  conversationId: null,
  quality: null,
  observation: null,
  preview: null,
  previewLoading: false,
  problem: null,

  messages: [],
  streaming: null,
  activity: {},
  lastStop: null,

  setBackendStatus(backend) {
    set({ backend });
  },
  connect(info) {
    const client = new ApiClient(info);
    set({ client });
    void get().loadSettings();
    if (!channelStop) {
      channelStop = startActivityChannel(client, {
        onLive: (session) => get().applyLive(session),
        onStop: (ev) => set({ lastStop: ev }),
      });
    }
  },
  async loadSettings() {
    const { client } = get();
    if (!client) return;
    try {
      set({ settings: await client.getSettings() });
    } catch {
      /* the overlay works without settings (preview on by default) */
    }
  },
  onShown(e) {
    const next = applyShown(get().sourceState, e.sourceHint);
    set({ visible: true, showId: e.showId, sourceState: next, problem: null });
    if (next.pickerOpen) void get().refreshSources();
  },
  hide() {
    set({ visible: false, sourceState: closePicker(get().sourceState) });
    // The main process answers with the hidden event (-> onHidden -> endSession);
    // without a bridge (tests) the session is ended right here.
    if (window.spaider) void window.spaider.overlay.hide();
    else void get().endSession();
  },
  onHidden() {
    set({ visible: false, sourceState: closePicker(get().sourceState) });
    void get().endSession();
  },
  async endSession() {
    const { client, session, streaming } = get();
    if (!client || !session) return;
    // While an answer streams the session is needed; ask() ends it afterwards.
    if (streaming) return;
    if (session.state !== "ended") {
      try {
        const tasks = await client.tasks();
        if (isSessionReferenced(tasks, session.id)) return;
      } catch {
        /* unknown task state: end the session anyway (the task keeps its own hooks) */
      }
      if (get().session?.id !== session.id || get().visible) return;
      try {
        await client.endLiveSession(session.id);
      } catch {
        /* already gone (404) or core unreachable: nothing to keep */
      }
    }
    if (get().session?.id === session.id) {
      set({ session: null, preview: null, previewLoading: false, observation: null, quality: null });
    }
  },
  async refreshSources() {
    const { client } = get();
    if (!client) return;
    set({ sourcesLoading: true });
    try {
      set({ sources: await client.liveSources() });
    } catch (e) {
      set({ problem: `Quellen konnten nicht geladen werden: ${errorMessage(e)}` });
    } finally {
      set({ sourcesLoading: false });
    }
  },
  openPicker() {
    set({ sourceState: openPicker(get().sourceState) });
    void get().refreshSources();
  },
  closePicker() {
    set({ sourceState: closePicker(get().sourceState) });
  },
  async choose(source) {
    if (isWindowSource(source) && source.is_self) return;
    const { client, session } = get();
    set({ sourceState: confirmSource(get().sourceState, source), problem: null });
    if (client && session && session.state !== "ended" && session.source.id !== source.id) {
      try {
        const updated = await client.switchLiveSource(session.id, source.id);
        set({ session: updated, quality: updated.last_snapshot?.quality ?? null, preview: null, observation: null });
        void get().refreshPreview();
      } catch (e) {
        set({ problem: liveErrorText(e), session: null, preview: null });
      }
    }
  },
  async ask(content) {
    const text = content.trim();
    const { client, streaming } = get();
    if (!client || !text || streaming) return;
    let state = get().sourceState;
    const target = effectiveTarget(state);
    if (!target) {
      // Never without a confirmed, visible source.
      set({ sourceState: openPicker(state) });
      void get().refreshSources();
      return;
    }
    if (state.hintPending) {
      // One Enter confirms the preselected foreground window.
      state = confirmSource(state, target);
      set({ sourceState: state });
    }

    // Session: create on the first question (continuing the conversation of the
    // previous session, if any), switch deliberately when the target changed.
    let session = get().session;
    try {
      if (!session || session.state === "ended") {
        const previousConversation = get().conversationId;
        let created: LiveSession;
        try {
          created = await client.createLiveSession(target.id, previousConversation);
        } catch (e) {
          // The conversation was deleted in the meantime: start a fresh one.
          if (!previousConversation || !isApiError(e) || e.code !== "not_found") throw e;
          created = await client.createLiveSession(target.id, null);
        }
        session = created;
        const sameConversation = previousConversation !== null && created.conversation_id === previousConversation;
        set((s) => ({
          session: created,
          conversationId: created.conversation_id,
          messages: sameConversation ? s.messages : [],
          activity: sameConversation ? s.activity : {},
          observation: null,
          preview: null,
          quality: created.last_snapshot?.quality ?? null,
        }));
      } else if (session.source.id !== target.id) {
        session = await client.switchLiveSource(session.id, target.id);
        set({ session, observation: null, preview: null, quality: session.last_snapshot?.quality ?? null });
      }
    } catch (e) {
      set({ problem: liveErrorText(e), session: null });
      if (isApiError(e) && (e.code === "source_not_found" || e.status === 404)) set({ quality: "lost" });
      return;
    }
    const sessionId = session.id;
    const conversationId = session.conversation_id;
    const live: OverlayStreaming = { conversationId, assistantId: null, statusText: null, stopping: false };
    set({ streaming: live, problem: null });
    reportActive(live);

    let response: Response;
    try {
      response = await client.liveAsk(sessionId, { content: text, use_memory: true, use_image: "auto" });
    } catch (e) {
      set({ streaming: null, problem: liveErrorText(e) });
      reportActive(null);
      if (isApiError(e) && e.code === "source_lost") set({ quality: "lost" });
      return;
    }

    const perf = getPerf();
    perf?.start("overlay-stream");
    let assistantId: string | null = null;
    const result = await consumeChatStream(response, {
      onMessage: (id) => {
        assistantId = id;
        set((s) => (s.streaming ? { streaming: { ...s.streaming, assistantId: id } } : {}));
      },
      onStatus: (statusText) => set((s) => (s.streaming ? { streaming: { ...s.streaming, statusText } } : {})),
      onActivity: (ev: ActivityEvent) => {
        set((s) => {
          if (!assistantId || ev.request_id !== assistantId) return {};
          const existing = s.activity[ev.request_id];
          const next = existing ? reduceActivity(existing, ev) : createTrack(ev.request_id, ev, Date.now());
          if (next === existing) return {};
          return { activity: { ...s.activity, [ev.request_id]: next } };
        });
      },
      onObservation: (ev) => set({ observation: ev, quality: ev.quality }),
      onTokens: (chunk) => set((s) => ({ messages: applyChatEvent(s.messages, { type: "token", data: { text: chunk } }) })),
      applyEvent: (event) => set((s) => ({ messages: applyChatEvent(s.messages, event) })),
    });
    perf?.stop();

    set((s) => ({ messages: result.gotDone ? s.messages : finalizeDanglingStream(s.messages, result.error), streaming: null }));
    reportActive(null);
    if (result.error) set({ problem: result.error });
    if (!get().visible) {
      // Hidden while the answer streamed: end the session now (the turns stay).
      void get().endSession();
      return;
    }
    if (get().settings?.live_preview !== false) void get().refreshPreview();
  },
  async stop() {
    const { client, session, streaming } = get();
    if (!client || !session || !streaming) return;
    set((s) => (s.streaming ? { streaming: { ...s.streaming, stopping: true } } : {}));
    try {
      await client.liveStop(session.id);
    } catch (e) {
      set((s) => (s.streaming ? { streaming: { ...s.streaming, stopping: false } } : {}));
      notify(`Stoppen fehlgeschlagen: ${errorMessage(e)}`, "error");
    }
  },
  async refreshPreview() {
    const { client, session, settings } = get();
    if (!client || !session || session.state === "ended") return;
    if (settings && settings.live_preview === false) return;
    set({ previewLoading: true });
    try {
      // The core requires max_elements >= 1 (422 otherwise); the elements are not used here.
      const snap = await client.liveSnapshot(session.id, { include_image: false, preview: true, max_elements: 1 });
      set({ preview: snap.image?.preview_png_base64 ?? null, quality: snap.quality });
      if (snap.quality === "lost") set((s) => (s.session ? { session: { ...s.session, state: "lost" } } : {}));
    } catch (e) {
      if (isApiError(e) && (e.code === "source_lost" || e.status === 404)) set({ quality: "lost", preview: null });
    } finally {
      set({ previewLoading: false });
    }
  },
  async setPreviewEnabled(enabled) {
    const { client, settings } = get();
    if (!client) return;
    const previous = settings;
    if (settings) set({ settings: { ...settings, live_preview: enabled } });
    try {
      set({ settings: await client.patchSettings({ live_preview: enabled }) });
      if (enabled) void get().refreshPreview();
      else set({ preview: null });
    } catch (e) {
      set({ settings: previous });
      notify(`Einstellung nicht übernommen: ${errorMessage(e)}`, "error");
    }
  },
  async openInMain() {
    const { session } = get();
    await window.spaider?.focusMain(session?.conversation_id ?? null);
  },
  async runAsTask(text) {
    const { session, sourceState } = get();
    const target = effectiveTarget(sourceState);
    await window.spaider?.requestTaskForm({
      goal: text.trim(),
      source_id: target?.id ?? null,
      process: target && isWindowSource(target) ? target.process : null,
      live_session_id: session?.id ?? null,
      conversation_id: session?.conversation_id ?? null,
    });
  },
  applyHotkey(ev) {
    if (ev.kind === "stop") {
      set((s) => (s.streaming ? { streaming: { ...s.streaming, stopping: true } } : {}));
    }
  },
  applyLive(session) {
    const current = get().session;
    if (!current || current.id !== session.id) return;
    set({ session, quality: session.state === "lost" ? "lost" : (session.last_snapshot?.quality ?? get().quality) });
    if (session.state === "lost") set({ preview: null });
  },
}));
