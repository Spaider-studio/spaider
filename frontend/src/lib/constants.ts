export const NODE_TYPE_COLORS: Record<string, string> = {
  PERSON: "#3B82F6",
  ORGANIZATION: "#10B981",
  LOCATION: "#F59E0B",
  CONCEPT: "#8B5CF6",
  EVENT: "#EF4444",
  PRODUCT: "#EC4899",
  TECHNOLOGY: "#06B6D4",
  DATE: "#F97316",
  METRIC: "#84CC16",
  DOCUMENT: "#A78BFA",
  TEAM: "#2DD4BF",
  ROLE: "#FB923C",
  PROJECT: "#34D399",
  OTHER: "#6B7280",
};

export const NODE_TYPE_LABELS: Record<string, string> = {
  PERSON: "Person",
  ORGANIZATION: "Organization",
  LOCATION: "Location",
  CONCEPT: "Concept",
  EVENT: "Event",
  PRODUCT: "Product",
  TECHNOLOGY: "Technology",
  DATE: "Date",
  METRIC: "Metric",
  DOCUMENT: "Document",
  TEAM: "Team",
  ROLE: "Role",
  PROJECT: "Project",
  OTHER: "Other",
};

export const ALL_NODE_TYPES = Object.keys(NODE_TYPE_COLORS);


export const GRAPH_MAX_NODES = 500;

/**
 * Where the browser reaches the backend API directly. Most calls go through the
 * Studio's own proxy, but uploads, exports and long LLM calls do not (the proxy
 * cuts them off), so they need an address.
 *
 * Set NEXT_PUBLIC_BACKEND_URL when the Studio is built if the API is somewhere
 * else. Without it the address follows the host the Studio was opened on, so
 * opening it as 127.0.0.1 or from another machine keeps working. The API must
 * allow the Studio's origin (CORS_ORIGINS).
 */
export function backendUrl(
  configured: string | undefined = process.env.NEXT_PUBLIC_BACKEND_URL,
  where: Pick<Location, "protocol" | "hostname"> | undefined = typeof window !== "undefined"
    ? window.location
    : undefined
): string {
  if (configured) return configured;
  if (where) return `${where.protocol}//${where.hostname}:8000/api/v1`;
  return "http://localhost:8000/api/v1";
}

export const PERMISSIONS = ["read", "write", "query", "admin"] as const;
export type Permission = (typeof PERMISSIONS)[number];

export const SYNTHESIS_STRATEGIES = [
  {
    id: "factual_qa",
    label: "Factual Q&A",
    description:
      "Generate question-answer pairs grounded in factual knowledge nodes.",
  },
  {
    id: "reasoning_chains",
    label: "Reasoning Chains",
    description:
      "Multi-hop reasoning paths through graph relationships for chain-of-thought training.",
  },
  {
    id: "relation_extraction",
    label: "Relation Extraction",
    description:
      "Extract and format entity-relation-entity triples for structured learning.",
  },
] as const;

export const OUTPUT_FORMATS = ["jsonl", "alpaca", "sharegpt"] as const;
