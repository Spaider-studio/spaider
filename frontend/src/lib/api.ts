import type {
  ClusterGraphPayload,
  GraphPayload,
  GraphStats,
  Agent,
  AgentImportResponse,
  ClearanceLevel,
  IngestResponse,
  QueryResponse,
  SwarmConnection,
  SynthesizeResult,
  TraversalResult,
  CypherResponse,
  WorkflowRun,
  ReplayEvent,
} from "./types";

// Relative URL — Next.js rewrite proxies /api/* → backend
const BASE_URL = "/api/v1";

async function request<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const url = `${BASE_URL}${path}`;
  const res = await fetch(url, {
    headers: {
      "Content-Type": "application/json",
      ...options.headers,
    },
    ...options,
  });

  if (!res.ok) {
    let message = `HTTP ${res.status}: ${res.statusText}`;
    try {
      const err = await res.json();
      message = err.detail ?? err.message ?? message;
    } catch {
      // ignore parse errors
    }
    throw new Error(message);
  }

  return res.json() as Promise<T>;
}

// ---- Ingest ----------------------------------------------------------------

export async function ingestText(
  text: string,
  agentId: string,
  source?: string
): Promise<IngestResponse> {
  return request<IngestResponse>("/ingest", {
    method: "POST",
    body: JSON.stringify({ text, agent_id: agentId, source }),
  });
}

export async function ingestSync(
  text: string,
  agentId: string,
  source?: string
): Promise<IngestResponse> {
  return request<IngestResponse>("/ingest/sync", {
    method: "POST",
    body: JSON.stringify({ text, agent_id: agentId, source }),
  });
}

export async function ingestFile(
  file: File,
  agentId: string
): Promise<IngestResponse> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("agent_id", agentId);

  const res = await fetch(`${BASE_URL}/ingest/file`, {
    method: "POST",
    body: formData,
  });

  if (!res.ok) {
    let message = `HTTP ${res.status}: ${res.statusText}`;
    try {
      const err = await res.json();
      message = err.detail ?? err.message ?? message;
    } catch {
      // ignore
    }
    throw new Error(message);
  }

  return res.json() as Promise<IngestResponse>;
}

/** Upload multiple files (PDF, DOCX, PPTX, HTML, MD, TXT) in one request. */
export async function ingestFiles(
  files: File[],
  agentId: string
): Promise<IngestResponse> {
  const formData = new FormData();
  files.forEach((f) => formData.append("files", f));
  formData.append("agent_id", agentId);

  const res = await fetch(`${BASE_URL}/ingest/files`, {
    method: "POST",
    body: formData,
  });

  if (!res.ok) {
    let message = `HTTP ${res.status}: ${res.statusText}`;
    try {
      const err = await res.json();
      message = err.detail ?? err.message ?? message;
    } catch {
      // ignore
    }
    throw new Error(message);
  }

  return res.json() as Promise<IngestResponse>;
}

/** Fetch one or more URLs and ingest their content (supports incremental sync). */
export async function ingestUrl(
  urls: string[],
  agentId: string
): Promise<IngestResponse> {
  return request<IngestResponse>("/ingest/url", {
    method: "POST",
    body: JSON.stringify({ urls, agent_id: agentId }),
  });
}

// ---- Connectors (observability) --------------------------------------------

export interface ConnectorStatus {
  connector_id: string;
  /** "idle" before first run; "done" or "error" after. */
  status: "idle" | "done" | "error";
  last_run_at: string | null;
  records_processed: number;
  last_error: string | null;
}

/** Poll GET /connectors/{id}/status for last-run stats. */
export async function getConnectorStatus(
  connectorId: string
): Promise<ConnectorStatus> {
  return request<ConnectorStatus>(`/connectors/${connectorId}/status`);
}

// ---- Graph -----------------------------------------------------------------

export async function getGraph(
  agentId?: string,
  limit = 2000,
  offset = 0,
): Promise<GraphPayload> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (agentId) params.set("agent_id", encodeURIComponent(agentId));
  return request<GraphPayload>(`/graph?${params}`);
}

export async function getGraphStats(agentId?: string): Promise<GraphStats> {
  const params = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : "";
  return request<GraphStats>(`/graph/stats${params}`);
}

export async function getMultiverseGraph(limit = 2000): Promise<GraphPayload> {
  return request<GraphPayload>(`/graph/multiverse?limit=${limit}`);
}

export async function getGraphClusters(
  agentId: string,
  zoomLevel = 0
): Promise<ClusterGraphPayload> {
  const params = new URLSearchParams({
    agent_id: agentId,
    zoom_level: String(zoomLevel),
  });
  return request<ClusterGraphPayload>(`/graph/clusters?${params}`);
}

// ---- Query -----------------------------------------------------------------

export async function queryNL(
  question: string,
  agentId: string,
  topK?: number
): Promise<QueryResponse> {
  return request<QueryResponse>("/query", {
    method: "POST",
    body: JSON.stringify({ question, agent_id: agentId, ...(topK != null && { top_k: topK }) }),
  });
}

export async function queryCypher(
  cypher: string,
  agentId: string
): Promise<CypherResponse> {
  return request<CypherResponse>("/query/cypher", {
    method: "POST",
    body: JSON.stringify({ cypher, agent_id: agentId }),
  });
}

export async function traverseGraph(
  nodeId: string,
  depth: number = 2,
  agentId?: string
): Promise<TraversalResult> {
  const params = new URLSearchParams({ depth: String(depth) });
  if (agentId) params.set("agent_id", agentId);
  return request<TraversalResult>(`/graph/traverse/${nodeId}?${params}`);
}

// ---- Nodes -----------------------------------------------------------------

export async function deleteNode(
  nodeId: string
): Promise<{ success: boolean; deleted_node_id: string; deleted_edges: number }> {
  return request<{ success: boolean; deleted_node_id: string; deleted_edges: number }>(
    `/node/${nodeId}`,
    {
      method: "DELETE",
      headers: {
        "X-Agent-Permission": "admin"
      }
    }
  );
}

// ---- Agents ----------------------------------------------------------------

export async function getAgents(): Promise<Agent[]> {
  const res = await request<{ agents: Agent[]; total: number } | Agent[]>("/agents");
  return Array.isArray(res) ? res : res.agents;
}

/** Request shape for agent creation — mirrors backend AgentCreateRequest. */
export interface AgentCreateRequest {
  name: string;
  description?: string;
  permissions: string[];
  /**
   * Diplomat Protocol clearance level (1 = Public … 5 = Top Secret).
   * Defaults to 1 on the backend when omitted; always send explicitly
   * from the UI so the value reflects the user's intent.
   */
  clearance_level: ClearanceLevel;
  interaction_memory?: boolean;
}

/** Request shape for agent update — same fields as create; id comes from the URL. */
export type AgentUpdateRequest = AgentCreateRequest;

export async function createAgent(data: AgentCreateRequest): Promise<Agent> {
  const res = await request<{ success: boolean; agent: Agent } | Agent>("/agents", {
    method: "POST",
    body: JSON.stringify(data),
  });
  return "agent" in res ? res.agent : res;
}

export async function updateAgent(
  agentId: string,
  data: AgentUpdateRequest
): Promise<Agent> {
  const res = await request<{ success: boolean; agent: Agent } | Agent>(
    `/agents/${agentId}`,
    { method: "PUT", body: JSON.stringify(data) }
  );
  return "agent" in res ? res.agent : res;
}

export async function deleteAgentInteractions(
  agentId: string
): Promise<{ success: boolean; agent_id: string; deleted_count: number }> {
  return request(`/agents/${agentId}/interactions`, { method: "DELETE" });
}

/**
 * Stream-import a .ndjson file produced by exportAgentGraph().
 *
 * Uses a direct fetch() to the backend (bypassing the Next.js proxy) because
 * large file uploads for 100k+ node graphs can exceed the proxy's body-size
 * and timeout limits.
 *
 * @param file           The .ndjson file selected by the user.
 * @param targetAgentId  If provided, ignore the agent_id in the file's metadata
 *                       and append the graph into this existing agent. Always
 *                       MERGE — never destroys existing data. Use this when
 *                       importing into an agent you already created.
 * @param merge          Only relevant when `targetAgentId` is omitted. If true,
 *                       merge into the file's metadata agent. If false, 409
 *                       when that agent already exists.
 */
export async function importAgentGraph(
  file: File,
  targetAgentId?: string,
  merge: boolean = false
): Promise<AgentImportResponse> {
  const backendUrl =
    typeof window !== "undefined"
      ? (process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000/api/v1")
      : "http://localhost:8000/api/v1";

  const params = new URLSearchParams({ merge: String(merge) });
  if (targetAgentId) {
    params.set("target_agent_id", targetAgentId);
  }
  const formData = new FormData();
  formData.append("file", file);

  const res = await fetch(`${backendUrl}/agents/import?${params}`, {
    method: "POST",
    body: formData,
  });

  if (!res.ok) {
    let message = `HTTP ${res.status}: ${res.statusText}`;
    try {
      const err = await res.json();
      message = err.detail ?? err.message ?? message;
    } catch { /* ignore parse errors */ }
    throw new Error(message);
  }

  return res.json() as Promise<AgentImportResponse>;
}

export async function deleteAgent(
  agentId: string
): Promise<{ deleted: boolean }> {
  return request<{ deleted: boolean }>(`/agents/${agentId}`, {
    method: "DELETE",
    headers: {
      "X-Agent-Permission": "admin"
    }
  });
}

export interface RotateKeyResponse {
  success: boolean;
  agent_id: string;
  api_key: string;
  message: string;
}

export async function rotateAgentKey(
  agentId: string
): Promise<RotateKeyResponse> {
  return request<RotateKeyResponse>(`/agents/${agentId}/rotate-key`, {
    method: "POST",
  });
}

export async function connectAgentBridge(
  sourceAgentId: string,
  targetAgentId: string
): Promise<{ success: boolean; source_agent_id: string; target_agent_id: string; link_type: string }> {
  return request("/agents/connect", {
    method: "POST",
    body: JSON.stringify({
      source_agent_id: sourceAgentId,
      target_agent_id: targetAgentId,
    }),
  });
}

export interface SwarmLink {
  source_id: string;
  source_name: string;
  target_id: string;
  target_name: string;
}

export async function listSwarmLinks(): Promise<SwarmLink[]> {
  return request<SwarmLink[]>("/agents/links");
}

export async function deleteSwarmLink(
  sourceAgentId: string,
  targetAgentId: string
): Promise<{ success: boolean; deleted_count: number }> {
  const params = new URLSearchParams({
    source_agent_id: sourceAgentId,
    target_agent_id: targetAgentId,
  });
  return request(`/agents/link?${params}`, { method: "DELETE" });
}

// ---- Synthesizer -----------------------------------------------------------

export async function synthesize(config: {
  agent_id: string;
  strategy: string;
  max_samples?: number;
  min_path_length?: number;
  min_confidence?: number;
  output_format?: string;
}): Promise<SynthesizeResult> {
  return request<SynthesizeResult>("/synthesize", {
    method: "POST",
    body: JSON.stringify(config),
  });
}

export async function downloadDataset(datasetId: string): Promise<Blob> {
  const res = await fetch(`${BASE_URL}/synthesize/download/${datasetId}`);
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${res.statusText}`);
  }
  return res.blob();
}

export async function generateAgenticDataset(opts: {
  agentId?: string;
  numSamples?: number;
  concurrency?: number;
}): Promise<{ blob: Blob; filename: string }> {
  const params = new URLSearchParams();
  if (opts.agentId) params.set("agent_id", opts.agentId);
  if (opts.numSamples != null) params.set("num_samples", String(opts.numSamples));
  if (opts.concurrency != null) params.set("concurrency", String(opts.concurrency));
  const query = params.toString();
  const res = await fetch(`${BASE_URL}/synthesize/generate_dataset${query ? `?${query}` : ""}`);
  if (!res.ok) {
    let message = `HTTP ${res.status}: ${res.statusText}`;
    try {
      const err = await res.json();
      message = err.detail ?? message;
    } catch { /* ignore */ }
    throw new Error(message);
  }
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const match = disposition.match(/filename="([^"]+)"/);
  const filename = match?.[1] ?? "spaider_toolcall_training.jsonl";
  const blob = await res.blob();
  return { blob, filename };
}

export async function exportChatML(agentId?: string): Promise<{ blob: Blob; filename: string }> {
  const params = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : "";
  const res = await fetch(`${BASE_URL}/synthesize/export${params}`);
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${res.statusText}`);
  }
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const match = disposition.match(/filename="([^"]+)"/);
  const filename = match?.[1] ?? (agentId ? `spaider_${agentId}_training.jsonl` : "spaider_multiverse_training.jsonl");
  const blob = await res.blob();
  return { blob, filename };
}

export async function exportDpo(agentId: string): Promise<{ blob: Blob; filename: string }> {
  const res = await fetch(`${BASE_URL}/synthesize/dpo?agent_id=${encodeURIComponent(agentId)}`);
  if (!res.ok) {
    // The 422 guardrail carries an actionable explanation (e.g. "graph has no
    // usage signal yet") — surface it instead of a bare status code.
    let detail = `HTTP ${res.status}: ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch {
      // non-JSON error body — keep the status line
    }
    throw new Error(detail);
  }
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const match = disposition.match(/filename="([^"]+)"/);
  const filename = match?.[1] ?? `spaider_${agentId}_dpo.jsonl`;
  const blob = await res.blob();
  return { blob, filename };
}

// ---- Swarm -----------------------------------------------------------------

export async function createSwarmConnection(data: {
  source_agent_id: string;
  target_agent_id: string;
  permission: string;
  scope: string;
  allowed_node_types?: string[];
  allowed_relation_types?: string[];
  expires_at?: string;
}): Promise<SwarmConnection> {
  return request<SwarmConnection>("/swarm/connections", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function listSwarmConnections(
  agentId?: string
): Promise<SwarmConnection[]> {
  const params = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : "";
  return request<SwarmConnection[]>(`/swarm/connections${params}`);
}

export async function revokeSwarmConnection(
  connectionId: string
): Promise<{ revoked: boolean }> {
  return request<{ revoked: boolean }>(`/swarm/connections/${connectionId}`, {
    method: "DELETE",
  });
}

export async function swarmQuery(
  question: string,
  sourceAgentId: string,
  targetAgentId: string
): Promise<QueryResponse> {
  return request<QueryResponse>("/swarm/query", {
    method: "POST",
    body: JSON.stringify({
      question,
      source_agent_id: sourceAgentId,
      target_agent_id: targetAgentId,
    }),
  });
}

export interface SwarmIntelligenceResponse {
  answer: string;
  source_node_ids: string[];
  agents_involved: string[];
}

export async function swarmIntelligenceQuery(
  query: string,
  agentIds?: string[]
): Promise<SwarmIntelligenceResponse> {
  return request<SwarmIntelligenceResponse>("/swarm/query", {
    method: "POST",
    body: JSON.stringify({ query, agent_ids: agentIds ?? null }),
  });
}

// ---- Audit log (workflow events) -------------------------------------------------------

type WorkflowSummaryResponse = {
  workflow_id: string;
  agent_id: string;
  first_event: string;
  last_event: string;
  event_count: number;
};

type WorkflowListResponse = {
  workflows: WorkflowSummaryResponse[];
  total: number;
};

type WorkflowEventResponse = {
  event_id: string;
  workflow_id: string;
  agent_id: string;
  event_type: string;
  payload?: Record<string, unknown>;
  timestamp: string;
};

type WorkflowEventsResponse = {
  workflow_id: string;
  events: WorkflowEventResponse[];
  total: number;
};

// The backend's actual event types pass through verbatim; anything the UI
// doesn't know yet (e.g. manually recorded events) renders as "other" instead
// of being heuristically mis-bucketed (the old mapper turned "query_failed"
// into a plain "query").
const KNOWN_EVENT_TYPES: ReadonlySet<string> = new Set([
  "ingest_received", "ingest_queued", "graph_mutation",
  "ingest_completed", "ingest_failed",
  "query_received", "query_answered", "query_failed",
]);

function mapReplayEventType(eventType: string): ReplayEvent["type"] {
  const t = (eventType || "").toLowerCase();
  return (KNOWN_EVENT_TYPES.has(t) ? t : "other") as ReplayEvent["type"];
}

export async function getWorkflowRuns(
  agentId?: string,
  workflowId?: string
): Promise<WorkflowRun[]> {
  const params = new URLSearchParams();
  if (agentId) params.set("agent_id", agentId);
  // Backend supports filtering by agent_id + limit; workflow_id filter is applied client-side.
  const query = params.toString();
  const res = await request<WorkflowListResponse>(
    `/replay/workflows${query ? `?${query}` : ""}`
  );

  const runs = res.workflows.map((wf) => ({
    id: wf.workflow_id,
    workflow_id: wf.workflow_id,
    agent_id: wf.agent_id,
    status: "completed" as const,
    started_at: wf.first_event,
    finished_at: wf.last_event,
    event_count: wf.event_count,
  }));

  if (!workflowId) return runs;
  const q = workflowId.toLowerCase();
  return runs.filter((r) => r.workflow_id.toLowerCase().includes(q));
}

export async function getReplayEvents(runId: string): Promise<ReplayEvent[]> {
  const res = await request<WorkflowEventsResponse>(
    `/replay/workflows/${encodeURIComponent(runId)}/events`
  );

  return res.events.map((ev) => ({
    id: ev.event_id,
    timestamp: ev.timestamp,
    type: mapReplayEventType(ev.event_type),
    agent_id: ev.agent_id,
    metadata: ev.payload ?? {},
  }));
}

// ---- Service Health --------------------------------------------------------

export interface ServiceHealthResponse {
  app: string;
  version: string;
  environment: string;
  // Fix 3 (type): explicit union so callers can derive aggregate status correctly
  services: Record<string, "ok" | "unavailable">;
  healthy: boolean;
}

/**
 * Swarm Pulse — live worker presence discovered via Redis heartbeat keys.
 * Mirrors the backend response of GET /api/v1/swarm/health.
 *
 * active_agents: IDs of workers that refreshed their presence key within
 *                the last 15 s (TTL-based ephemeral discovery).
 * total:         convenience count — equals active_agents.length.
 */
export interface SwarmHealthResponse {
  active_agents: string[];
  total: number;
}

// Fix 1: /health lives at the backend root, not /api/v1.
// Reached via the Next.js Route Handler at /api/health (app/api/health/route.ts).
// Fix 2: caller passes AbortSignal so in-flight requests can be cancelled.
// Fix 5: non-ok responses throw "Health check failed: <reason>", not "Backend unreachable".
export async function getServiceHealth(
  signal?: AbortSignal
): Promise<ServiceHealthResponse> {
  const res = await fetch("/api/health", { signal, cache: "no-store" });
  if (!res.ok) {
    let message = res.statusText;
    try {
      const body = await res.json();
      message = body.detail ?? body.error ?? res.statusText;
    } catch {
      // ignore parse error — keep statusText
    }
    throw new Error(`Health check failed: ${message}`);
  }
  return res.json() as Promise<ServiceHealthResponse>;
}

/**
 * Swarm Pulse health — discovers live swarm workers via Redis heartbeat keys.
 * Calls GET /api/v1/swarm/health (proxied through Next.js rewrite).
 *
 * Accepts an AbortSignal so the caller can cancel in-flight requests
 * on component unmount or when a new poll cycle starts.
 *
 * Returns { active_agents: [], total: 0 } on a non-ok response rather
 * than throwing, so a Redis hiccup never takes down the whole panel.
 */
export async function getSwarmHealth(
  signal?: AbortSignal
): Promise<SwarmHealthResponse> {
  const res = await fetch(`${BASE_URL}/swarm/health`, {
    signal,
    cache: "no-store",
  });
  if (!res.ok) {
    // Non-fatal: return an empty state so the panel shows "standby"
    // rather than an unhandled error banner.
    return { active_agents: [], total: 0 };
  }
  return res.json() as Promise<SwarmHealthResponse>;
}

// ---- System Settings -------------------------------------------------------

export interface SystemSettings {
  auto_reflection: boolean;
}

export async function getSystemSettings(): Promise<SystemSettings> {
  return request<SystemSettings>("/system/settings");
}

export async function setReflectionEnabled(
  enabled: boolean
): Promise<SystemSettings> {
  return request<SystemSettings>("/system/settings/reflection", {
    method: "POST",
    body: JSON.stringify({ enabled }),
  });
}

// ---- Per-agent memory mode (off | on) --------------------------------------

export async function getMemoryMode(agentId: string): Promise<"off" | "on"> {
  const res = await request<{ data?: { memory_mode?: string } }>(
    `/agents/${encodeURIComponent(agentId)}/memory-mode`
  );
  return (res.data?.memory_mode as "off" | "on") ?? "on";
}

export async function setMemoryMode(
  agentId: string,
  mode: "off" | "on"
): Promise<"off" | "on"> {
  const res = await request<{ data?: { memory_mode?: string } }>(
    `/agents/${encodeURIComponent(agentId)}/memory-mode`,
    { method: "POST", body: JSON.stringify({ memory_mode: mode }) }
  );
  return (res.data?.memory_mode as "off" | "on") ?? mode;
}

// ---- Per-agent hibernation cadence (autonomous consolidation) --------------

export interface ConsolidationConfig {
  interval_hours: number; // 0 = off
  last_consolidated_at: string | null;
}

export async function getConsolidationConfig(
  agentId: string
): Promise<ConsolidationConfig> {
  const res = await request<{ data?: ConsolidationConfig }>(
    `/agents/${encodeURIComponent(agentId)}/consolidation`
  );
  return {
    interval_hours: res.data?.interval_hours ?? 0,
    last_consolidated_at: res.data?.last_consolidated_at ?? null,
  };
}

export async function setConsolidationConfig(
  agentId: string,
  intervalHours: number
): Promise<number> {
  const res = await request<{ data?: { interval_hours?: number } }>(
    `/agents/${encodeURIComponent(agentId)}/consolidation`,
    { method: "POST", body: JSON.stringify({ interval_hours: intervalHours }) }
  );
  return res.data?.interval_hours ?? intervalHours;
}

export interface ConsolidationReport {
  pruned: number;
  fused: number;
  decayed: number;
  proposed: number;
}

export async function consolidateNow(
  agentId: string
): Promise<ConsolidationReport> {
  const res = await request<{ data?: ConsolidationReport }>(
    `/agents/${encodeURIComponent(agentId)}/consolidate-now`,
    { method: "POST" }
  );
  return (
    res.data ?? { pruned: 0, fused: 0, decayed: 0, proposed: 0 }
  );
}
