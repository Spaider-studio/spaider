export type NodeType =
  | "PERSON"
  | "ORGANIZATION"
  | "LOCATION"
  | "EVENT"
  | "CONCEPT"
  | "PRODUCT"
  | "TECHNOLOGY"
  | "DATE"
  | "METRIC"
  | "DOCUMENT"
  | "TEAM"
  | "ROLE"
  | "PROJECT"
  | "OTHER"
  | "agent_core"
  | "InteractionNode"  // Episodic memory record (see Interaction Memory)
  | "CLUSTER"          // LOD aggregate (see GraphCanvas3D)
  | "CLUSTER_EDGE";    // relation between two clusters

export interface GraphNode {
  id: string;
  label: string;
  type: NodeType;
  properties: Record<string, unknown>;
  embedding?: number[];
  agent_id?: string;
  created_at?: string;
  updated_at?: string;
  // Force-graph runtime fields
  x?: number;
  y?: number;
  z?: number;
  vx?: number;
  vy?: number;
  vz?: number;
  fx?: number | null;
  fy?: number | null;
  fz?: number | null;
}

export interface GraphEdge {
  id: string;
  source_id?: string;
  target_id?: string;
  source: string;
  target: string;
  relation: string;
  type?: string;   // Equals relation — used by WebGL renderer for conditional particle rendering
  properties: Record<string, unknown>;
  agent_id?: string;
  utility_weight?: number;  // V2 Cognitive Graph: synapse strength — controls edge width
}

export interface GraphPayload {
  nodes: GraphNode[];
  edges: GraphEdge[];
  /** Total nodes in this page (may be less than limit on the final page). */
  node_count?: number;
  edge_count?: number;
  /** Pagination envelope echoed from the server. */
  limit?: number;
  offset?: number;
}

/**
 * LOD overview returned by GET /api/v1/graph/clusters.
 * Clusters are aggregated groups of nodes (one per node type at zoom_level=0).
 * Rendered as larger spheres sized by node_count — see GraphCanvas3D.
 */
export interface GraphCluster {
  id: string;
  label: string;
  type: NodeType;
  node_count: number;
  sample_node_ids: string[];
}

export interface GraphClusterEdge {
  id: string;
  source_cluster_id: string;
  target_cluster_id: string;
  count: number;
}

export interface ClusterGraphPayload {
  clusters: GraphCluster[];
  cluster_edges: GraphClusterEdge[];
  total_nodes: number;
  total_edges: number;
  zoom_level: number;
  agent_id: string;
}

export interface GraphStats {
  node_count: number;
  edge_count: number;
  type_distribution: Record<string, number>;
  relation_distribution: Record<string, number>;
  density: number;
  agent_id: string;
}

/**
 * Diplomat Protocol — security clearance levels.
 * Union type (not `number`) ensures invalid values (0, 6, 99…) are rejected
 * at compile time and enables exhaustive switch checks in badge/color maps.
 */
export type ClearanceLevel = 1 | 2 | 3 | 4 | 5;

/** Human-readable label for each clearance level — single source of truth. */
export const CLEARANCE_LABELS: Record<ClearanceLevel, string> = {
  1: "Public",
  2: "Internal",
  3: "Confidential",
  4: "Secret",
  5: "Top Secret",
};

export interface Agent {
  id: string;
  name: string;
  description?: string;
  tenant_id: string;
  permissions: string[];
  clearance_level: ClearanceLevel;
  interaction_memory: boolean;
  api_key?: string;
  created_at: string;
}

/** Episodic memory record stored as an InteractionNode in Neo4j. */
export interface InteractionNode {
  id: string;
  session_id: string;
  /** Truncated to 200 chars by the backend before storage. */
  question: string;
  /** Truncated to 500 chars by the backend before storage. */
  answer_summary: string;
  timestamp: string;
  agent_id: string;
  source_node_ids: string[];
}

export interface IngestResponse {
  success: boolean;
  agent_id: string;
  nodes_created: number;
  nodes_merged: number;
  edges_created: number;
  edges_merged: number;
  nodes: Array<{ id: string; label: string; type: string; properties: Record<string, unknown>; agent_id?: string }>;
  edges: Array<{ id: string; source: string; target: string; relation: string; agent_id?: string }>;
  latency_ms: number;
}

export interface QueryResponse {
  question: string;
  answer: string;
  subgraph: GraphPayload;
  cypher_used?: string;
}

export interface SwarmConnection {
  id: string;
  source_agent_id: string;
  target_agent_id: string;
  permission: string;
  scope: string;
  allowed_node_types?: string[];
  allowed_relation_types?: string[];
  expires_at?: string;
  created_at: string;
}

export interface SynthesizeResult {
  status: string;
  dataset_id: string;
  dataset_path: string;
  example_count: number;
  strategy: string;
  output_format: string;
  preview?: Record<string, unknown>[];
  stats: {
    total_samples: number;
    avg_confidence: number;
    avg_token_length: number;
    strategy: string;
    generation_time_seconds: number;
    format: string;
  };
}

export interface TraversalResult {
  nodes: GraphNode[];
  edges: GraphEdge[];
  depth: number;
  start_node_id: string;
}

export interface CypherResponse {
  cypher: string;
  results: Record<string, unknown>[];
  node_ids: string[];
}

export interface AuditLogEntry {
  id: string;
  timestamp: string;
  deleted_node: string;
  edges_deleted: number;
  performed_by: string;
  agent_id: string;
}

// ---- Audit log (workflow events) ---------------------------------------------

// The event types the backend actually emits (ingest.py / query.py replay
// events). The previous union ("reasoning", "output", …) described a model
// that never existed and forced a lossy heuristic mapping in api.ts.
export type ReplayEventType =
  | "ingest_received"
  | "ingest_queued"
  | "graph_mutation"
  | "ingest_completed"
  | "ingest_failed"
  | "query_received"
  | "query_answered"
  | "query_failed"
  | "other";

export interface ReplayEvent {
  id: string;
  timestamp: string;
  type: ReplayEventType;
  agent_id: string;
  agent_name?: string;
  /** The full event payload as recorded — counts, latencies, answer preview. */
  metadata?: Record<string, unknown>;
}

/** Returned by POST /agents/import after a successful graph restore. */
export interface AgentImportResponse {
  success: boolean;
  agent_id: string;
  /** Freshly-generated API key — shown exactly once, not stored in the export. */
  new_api_key: string;
  nodes_restored: number;
  edges_restored: number;
  /** Count of malformed / unhandled lines (interaction, informed_by, unknown). */
  skipped: number;
}

export interface WorkflowRun {
  id: string;
  workflow_id: string;
  agent_id: string;
  agent_name?: string;
  status: "completed" | "failed" | "in_progress";
  started_at: string;
  finished_at?: string;
  event_count: number;
  topic?: string;
}
