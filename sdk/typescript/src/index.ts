export interface ClientOptions {
  baseUrl: string;
  apiKey: string;
  agentId?: string;
  fetch?: typeof fetch;
}

export interface Citation {
  ref: string;
  node_id: string;
  label: string;
  agent_id: string;
}

export interface QueryResult {
  question: string;
  answer: string;
  query_id?: string | null;
  citations: Citation[];
}

export interface RecallItem {
  type: "episode" | "node";
  id: string;
  text: string;
  score: number;
  kind?: string | null;
  agent_id?: string;
}

export interface EpisodeInput {
  content: string;
  kind?: string;
  idempotency_key?: string;
  session_id?: string;
  source?: string;
  occurred_at?: string;
  location?: { latitude?: number; longitude?: number; x?: number; y?: number; z?: number };
  metadata?: Record<string, unknown>;
  clearance_level?: number;
}

export interface RecallOptions {
  limit?: number;
  federated?: boolean;
  kinds?: string[];
  session_id?: string;
  since?: string;
  until?: string;
  near?: EpisodeInput["location"];
  radius?: number;
  as_of?: string;
}

export interface GrantScope {
  node_types?: string[];
  relation_types?: string[];
  max_clearance?: number;
  expires_at?: string;
}

export class SpaiderError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
  ) {
    super(`SpAIder API error ${status}: ${detail}`);
    this.name = "SpaiderError";
  }
}

export class SpaiderClient {
  private readonly base: string;
  private readonly key: string;
  private readonly doFetch: typeof fetch;
  readonly agentId?: string;

  constructor(options: ClientOptions) {
    if (!options.apiKey) throw new Error("apiKey is required");
    this.base = options.baseUrl.replace(/\/+$/, "");
    this.key = options.apiKey;
    this.agentId = options.agentId;
    this.doFetch = options.fetch ?? globalThis.fetch.bind(globalThis);
  }

  private kb(): string {
    if (!this.agentId) throw new Error("agentId is required for this call");
    return this.agentId;
  }

  private async request<T>(method: string, path: string, body?: unknown, params?: Record<string, string>): Promise<T> {
    const query = params ? `?${new URLSearchParams(params).toString()}` : "";
    const response = await this.doFetch(`${this.base}/api/v1${path}${query}`, {
      method,
      headers: { "X-Api-Key": this.key, "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const text = await response.text();
    if (!response.ok) {
      let detail = text;
      try {
        detail = JSON.parse(text).detail ?? text;
      } catch {
        // A non-JSON error body is reported as it came.
      }
      throw new SpaiderError(response.status, typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return (text ? JSON.parse(text) : undefined) as T;
  }

  /** Store an observation with no LLM call. */
  remember(content: string, fields: Omit<EpisodeInput, "content"> = {}) {
    return this.rememberMany([{ content, ...fields }]);
  }

  rememberMany(episodes: EpisodeInput[]) {
    return this.request<{ agent_id: string; episodes: { id: string; created: boolean }[] }>("POST", "/episodes", {
      agent_id: this.kb(),
      episodes,
    });
  }

  /** Search memory with no LLM call. */
  recall(query = "", options: RecallOptions = {}) {
    return this.request<{ agent_id: string; results: RecallItem[] }>("POST", "/recall", {
      agent_id: this.kb(),
      query,
      ...options,
    });
  }

  /** Ask a question and get a cited answer. */
  query(question: string, topK?: number) {
    return this.request<QueryResult>("POST", "/query", {
      question,
      agent_id: this.kb(),
      ...(topK === undefined ? {} : { top_k: topK }),
    });
  }

  ingest(text: string, source?: string) {
    return this.request("POST", "/ingest/sync", { text, agent_id: this.kb(), source });
  }

  /** Rate an answer. Reversing a verdict replaces the earlier one. */
  feedback(queryId: string, success: boolean, nodeIds: string[] = []) {
    return this.request("POST", "/system/feedback", {
      query_id: queryId,
      success,
      agent_id: this.kb(),
      used_node_ids: nodeIds,
    });
  }

  sleep() {
    return this.request("POST", `/agents/${encodeURIComponent(this.kb())}/sleep`);
  }

  sleepReport() {
    return this.request("GET", `/agents/${encodeURIComponent(this.kb())}/sleep/report`);
  }

  requestGrant(sourceKb: string, scope: GrantScope = {}, targetKb?: string) {
    return this.request("POST", "/federation/grants", {
      source_kb: sourceKb,
      target_kb: targetKb ?? this.kb(),
      ...scope,
    });
  }

  listGrants() {
    return this.request<unknown[]>("GET", "/federation/grants");
  }

  approveGrant(grantId: string, narrowed: GrantScope = {}) {
    return this.request("POST", `/federation/grants/${encodeURIComponent(grantId)}/approve`, narrowed);
  }

  denyGrant(grantId: string) {
    return this.request("POST", `/federation/grants/${encodeURIComponent(grantId)}/deny`);
  }

  revokeGrant(grantId: string) {
    return this.request("DELETE", `/federation/grants/${encodeURIComponent(grantId)}`);
  }

  createKpr(sourceKb: string, nodeIds: string[], title: string, targetKb?: string) {
    return this.request("POST", "/federation/kpr", {
      source_kb: sourceKb,
      target_kb: targetKb ?? this.kb(),
      node_ids: nodeIds,
      title,
    });
  }

  getKpr(id: string) {
    return this.request("GET", `/federation/kpr/${encodeURIComponent(id)}`);
  }

  listKprs(targetKb?: string) {
    return this.request<unknown[]>("GET", "/federation/kpr", undefined, { target_kb: targetKb ?? this.kb() });
  }

  approveKpr(id: string) {
    return this.request("POST", `/federation/kpr/${encodeURIComponent(id)}/approve`);
  }

  rejectKpr(id: string) {
    return this.request("POST", `/federation/kpr/${encodeURIComponent(id)}/reject`);
  }

  revertKpr(id: string) {
    return this.request("POST", `/federation/kpr/${encodeURIComponent(id)}/revert`);
  }
}
