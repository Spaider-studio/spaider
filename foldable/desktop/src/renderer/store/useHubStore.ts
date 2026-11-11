/**
 * State of the model library and the provider connections (Addendum v1.3):
 * presets + credential store, connections (+ their model lists and test
 * results), projects + consents, the vault dialogs and the discover search.
 * The client comes from the app store; nothing here renders.
 */
import { create } from "zustand";
import { errorMessage, isApiError } from "../api/client";
import type {
  Connection,
  ConnectionModel,
  ConnectionTestResult,
  Consent,
  CreateConnectionRequest,
  CredentialStoreStatus,
  DiscoverItem,
  DiscoverQuery,
  ModelCard,
  PatchConnectionRequest,
  Project,
  ProviderPreset,
  RepoDetail,
} from "../api/types";
import { notify } from "../lib/notify";
import { DEFAULT_DISCOVER_QUERY, mergeDiscoverPage, parseRepo } from "./hub";
import { useAppStore } from "./useAppStore";

export type VaultDialogMode = "create" | "unlock" | null;

export interface DiscoverState {
  query: DiscoverQuery;
  items: DiscoverItem[];
  nextCursor: string | null;
  cachedAt: string | null;
  offline: boolean;
  detail: string | null;
  loading: boolean;
  loadingMore: boolean;
  error: string | null;
  loaded: boolean;
}

export interface RepoState {
  repo: string;
  detail: RepoDetail | null;
  card: ModelCard | null;
  loading: boolean;
  error: string | null;
  requiresAccount: boolean;
}

export interface HubState {
  providers: ProviderPreset[];
  credentialStore: CredentialStoreStatus | null;
  providersLoaded: boolean;
  providersError: string | null;
  connections: Connection[];
  connectionsLoaded: boolean;
  modelsByConnection: Record<string, ConnectionModel[]>;
  modelsLoading: Record<string, boolean>;
  modelsError: Record<string, string>;
  testResults: Record<string, ConnectionTestResult>;
  testing: Record<string, boolean>;
  projects: Project[];
  consents: Consent[];
  projectsLoaded: boolean;
  vaultDialog: VaultDialogMode;
  vaultBusy: boolean;
  vaultError: string | null;
  discover: DiscoverState;
  repo: RepoState | null;

  loadProviders(): Promise<void>;
  loadConnections(): Promise<void>;
  loadProjects(): Promise<void>;
  loadAll(): Promise<void>;
  createConnection(body: CreateConnectionRequest): Promise<Connection | null>;
  updateConnection(id: string, patch: PatchConnectionRequest): Promise<Connection | null>;
  deleteConnection(id: string): Promise<boolean>;
  testConnection(id: string, allowGeneration: boolean, probeModel?: string | null): Promise<ConnectionTestResult | null>;
  loadConnectionModels(id: string, force?: boolean): Promise<ConnectionModel[]>;
  openVault(mode: Exclude<VaultDialogMode, null>): void;
  closeVault(): void;
  vaultCreate(password: string): Promise<boolean>;
  vaultUnlock(password: string): Promise<boolean>;
  vaultLock(): Promise<void>;
  grantConsent(connectionId: string, projectId: string): Promise<boolean>;
  revokeConsent(connectionId: string, projectId: string): Promise<void>;
  createProject(name: string, localOnly: boolean): Promise<Project | null>;
  setDiscoverQuery(patch: Partial<DiscoverQuery>): void;
  runDiscover(append: boolean): Promise<void>;
  openRepo(repo: string): Promise<void>;
  closeRepo(): void;
}

const client = () => useAppStore.getState().client;

let discoverAbort: AbortController | null = null;
let repoAbort: AbortController | null = null;

export function emptyDiscover(query: DiscoverQuery = DEFAULT_DISCOVER_QUERY): DiscoverState {
  return { query, items: [], nextCursor: null, cachedAt: null, offline: false, detail: null, loading: false, loadingMore: false, error: null, loaded: false };
}

export const useHubStore = create<HubState>()((set, get) => ({
  providers: [],
  credentialStore: null,
  providersLoaded: false,
  providersError: null,
  connections: [],
  connectionsLoaded: false,
  modelsByConnection: {},
  modelsLoading: {},
  modelsError: {},
  testResults: {},
  testing: {},
  projects: [],
  consents: [],
  projectsLoaded: false,
  vaultDialog: null,
  vaultBusy: false,
  vaultError: null,
  discover: emptyDiscover(),
  repo: null,

  async loadProviders() {
    const c = client();
    if (!c) return;
    try {
      const res = await c.providers();
      set({ providers: res.providers, credentialStore: res.credential_store, providersLoaded: true, providersError: null });
    } catch (e) {
      set({ providersError: errorMessage(e), providersLoaded: true });
    }
  },
  async loadConnections() {
    const c = client();
    if (!c) return;
    try {
      set({ connections: await c.connections(), connectionsLoaded: true });
    } catch (e) {
      notify(`Verbindungen konnten nicht geladen werden: ${errorMessage(e)}`, "error");
    }
  },
  async loadProjects() {
    const c = client();
    if (!c) return;
    try {
      const res = await c.projects();
      set({ projects: res.projects, consents: res.consents, projectsLoaded: true });
    } catch (e) {
      notify(`Projekte konnten nicht geladen werden: ${errorMessage(e)}`, "error");
    }
  },
  async loadAll() {
    await Promise.all([get().loadProviders(), get().loadConnections(), get().loadProjects()]);
  },
  async createConnection(body) {
    const c = client();
    if (!c) return null;
    try {
      const conn = await c.createConnection(body);
      set((s) => ({ connections: [...s.connections.filter((x) => x.id !== conn.id), conn] }));
      void get().loadProviders();
      return conn;
    } catch (e) {
      if (isApiError(e) && e.code === "locked") {
        set({ vaultDialog: get().credentialStore?.vault.exists ? "unlock" : "create", vaultError: null });
      }
      notify(`Verbindung konnte nicht angelegt werden: ${errorMessage(e)}`, "error");
      return null;
    }
  },
  async updateConnection(id, patch) {
    const c = client();
    if (!c) return null;
    try {
      const conn = await c.patchConnection(id, patch);
      set((s) => ({ connections: s.connections.map((x) => (x.id === id ? conn : x)) }));
      return conn;
    } catch (e) {
      notify(`Änderung nicht übernommen: ${errorMessage(e)}`, "error");
      return null;
    }
  },
  async deleteConnection(id) {
    const c = client();
    if (!c) return false;
    try {
      await c.deleteConnection(id);
      set((s) => {
        const models = { ...s.modelsByConnection };
        delete models[id];
        const tests = { ...s.testResults };
        delete tests[id];
        return { connections: s.connections.filter((x) => x.id !== id), modelsByConnection: models, testResults: tests, consents: s.consents.filter((x) => x.connection_id !== id) };
      });
      void get().loadProviders();
      return true;
    } catch (e) {
      notify(`Löschen fehlgeschlagen: ${errorMessage(e)}`, "error");
      return false;
    }
  },
  async testConnection(id, allowGeneration, probeModel = null) {
    const c = client();
    if (!c) return null;
    set((s) => ({ testing: { ...s.testing, [id]: true } }));
    try {
      const res = await c.testConnection(id, { allow_generation: allowGeneration, ...(probeModel ? { probe_model: probeModel } : {}) });
      set((s) => ({
        testResults: { ...s.testResults, [id]: res.result },
        connections: s.connections.map((x) => (x.id === id ? res.connection : x)),
      }));
      return res.result;
    } catch (e) {
      if (isApiError(e) && e.code === "locked") set({ vaultDialog: "unlock", vaultError: null });
      notify(`Verbindungstest fehlgeschlagen: ${errorMessage(e)}`, "error");
      return null;
    } finally {
      set((s) => ({ testing: { ...s.testing, [id]: false } }));
    }
  },
  async loadConnectionModels(id, force = false) {
    const c = client();
    if (!c) return [];
    const cached = get().modelsByConnection[id];
    if (cached && !force) return cached;
    set((s) => ({ modelsLoading: { ...s.modelsLoading, [id]: true }, modelsError: { ...s.modelsError, [id]: "" } }));
    try {
      const models = await c.connectionModels(id);
      set((s) => ({ modelsByConnection: { ...s.modelsByConnection, [id]: models } }));
      return models;
    } catch (e) {
      if (isApiError(e) && e.code === "locked") set({ vaultDialog: "unlock", vaultError: null });
      set((s) => ({ modelsError: { ...s.modelsError, [id]: errorMessage(e) }, modelsByConnection: { ...s.modelsByConnection, [id]: [] } }));
      return [];
    } finally {
      set((s) => ({ modelsLoading: { ...s.modelsLoading, [id]: false } }));
    }
  },
  openVault(mode) {
    set({ vaultDialog: mode, vaultError: null });
  },
  closeVault() {
    set({ vaultDialog: null, vaultError: null, vaultBusy: false });
  },
  async vaultCreate(password) {
    const c = client();
    if (!c) return false;
    set({ vaultBusy: true, vaultError: null });
    try {
      const status = await c.vaultCreate(password);
      set({ credentialStore: { host_available: status.host_available, vault: status.vault, session_items: status.session_items }, vaultDialog: null });
      notify("Tresor angelegt und entsperrt.", "success");
      return true;
    } catch (e) {
      set({ vaultError: errorMessage(e) });
      return false;
    } finally {
      set({ vaultBusy: false });
    }
  },
  async vaultUnlock(password) {
    const c = client();
    if (!c) return false;
    set({ vaultBusy: true, vaultError: null });
    try {
      const status = await c.vaultUnlock(password);
      set({ credentialStore: { host_available: status.host_available, vault: status.vault, session_items: status.session_items }, vaultDialog: null });
      notify(`Tresor entsperrt (${status.items ?? 0} Schlüssel).`, "success");
      return true;
    } catch (e) {
      set({ vaultError: isApiError(e) && e.code === "wrong_password" ? "Falsches Passwort." : errorMessage(e) });
      return false;
    } finally {
      set({ vaultBusy: false });
    }
  },
  async vaultLock() {
    const c = client();
    if (!c) return;
    try {
      const status = await c.vaultLock();
      set({ credentialStore: status });
      notify("Tresor gesperrt.", "info");
    } catch (e) {
      notify(`Sperren fehlgeschlagen: ${errorMessage(e)}`, "error");
    }
  },
  async grantConsent(connectionId, projectId) {
    const c = client();
    if (!c) return false;
    try {
      const consent = await c.grantConsent(connectionId, projectId);
      set((s) => ({ consents: [...s.consents.filter((x) => !(x.connection_id === connectionId && x.project_id === projectId)), consent] }));
      return true;
    } catch (e) {
      notify(`Freigabe nicht möglich: ${errorMessage(e)}`, "error");
      return false;
    }
  },
  async revokeConsent(connectionId, projectId) {
    const c = client();
    if (!c) return;
    try {
      await c.revokeConsent(connectionId, projectId);
      set((s) => ({ consents: s.consents.filter((x) => !(x.connection_id === connectionId && x.project_id === projectId)) }));
      notify("Freigabe widerrufen.", "info");
    } catch (e) {
      notify(`Widerruf fehlgeschlagen: ${errorMessage(e)}`, "error");
    }
  },
  async createProject(name, localOnly) {
    const c = client();
    if (!c) return null;
    try {
      const project = await c.createProject(name, localOnly);
      set((s) => ({ projects: [...s.projects, project] }));
      return project;
    } catch (e) {
      notify(`Projekt konnte nicht angelegt werden: ${errorMessage(e)}`, "error");
      return null;
    }
  },
  setDiscoverQuery(patch) {
    set((s) => ({ discover: { ...s.discover, query: { ...s.discover.query, ...patch } } }));
  },
  async runDiscover(append) {
    const c = client();
    if (!c) return;
    const { discover } = get();
    if (append && (!discover.nextCursor || discover.loadingMore)) return;
    discoverAbort?.abort();
    const controller = new AbortController();
    discoverAbort = controller;
    const query = discover.query;
    set((s) => ({ discover: { ...s.discover, loading: !append, loadingMore: append, error: null } }));
    try {
      const page = await c.discoverModels(query, append ? discover.nextCursor : null, 20, controller.signal);
      if (controller.signal.aborted) return;
      set((s) => ({
        discover: {
          ...s.discover,
          items: mergeDiscoverPage(s.discover.items, page, append),
          nextCursor: page.next_cursor,
          cachedAt: page.cached_at,
          offline: page.offline,
          detail: page.detail ?? null,
          loading: false,
          loadingMore: false,
          loaded: true,
        },
      }));
    } catch (e) {
      if (controller.signal.aborted || (isApiError(e) && e.code === "aborted")) return;
      set((s) => ({ discover: { ...s.discover, loading: false, loadingMore: false, error: errorMessage(e), loaded: true } }));
    }
  },
  async openRepo(repo) {
    const c = client();
    const parts = parseRepo(repo);
    if (!c || !parts) return;
    repoAbort?.abort();
    const controller = new AbortController();
    repoAbort = controller;
    set({ repo: { repo, detail: null, card: null, loading: true, error: null, requiresAccount: false } });
    try {
      const [detail, card] = await Promise.all([
        c.repoDetail(parts.author, parts.name, controller.signal),
        c.modelCard(parts.author, parts.name, undefined, controller.signal).catch(() => null),
      ]);
      if (controller.signal.aborted) return;
      set((s) => (s.repo?.repo === repo ? { repo: { ...s.repo, detail, card, loading: false } } : {}));
    } catch (e) {
      if (controller.signal.aborted) return;
      const requiresAccount = isApiError(e) && (e.code === "gated" || e.extra.requires_account === true);
      set((s) => (s.repo?.repo === repo ? { repo: { ...s.repo, loading: false, error: errorMessage(e), requiresAccount } } : {}));
    }
  },
  closeRepo() {
    repoAbort?.abort();
    set({ repo: null });
  },
}));
