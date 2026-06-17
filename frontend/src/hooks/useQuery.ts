"use client";

import { useState } from "react";
import { queryNL as queryNLApi, queryCypher as queryCypherApi } from "@/lib/api";
import { useGraph } from "./useGraph";
import type { QueryResponse, CypherResponse } from "@/lib/types";

export function useQuery() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<QueryResponse | null>(null);
  const [cypherResult, setCypherResult] = useState<CypherResponse | null>(null);
  const { highlightNodes, agentId } = useGraph();

  async function queryNL(question: string): Promise<QueryResponse | null> {
    if (!agentId) {
      setError("Select an agent before querying — Multiverse queries are not supported.");
      return null;
    }
    setLoading(true);
    setError(null);
    setResult(null);

    try {
      const res = await queryNLApi(question, agentId);
      setResult(res);

      const nodeIds = res.subgraph?.nodes?.map((n) => n.id) ?? [];
      if (nodeIds.length > 0) {
        highlightNodes(nodeIds);
      }

      return res;
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Query failed";
      setError(msg);
      return null;
    } finally {
      setLoading(false);
    }
  }

  async function queryCypher(cypher: string): Promise<CypherResponse | null> {
    if (!agentId) {
      setError("Select an agent before querying — Multiverse queries are not supported.");
      return null;
    }
    setLoading(true);
    setError(null);
    setCypherResult(null);

    try {
      const res = await queryCypherApi(cypher, agentId);
      setCypherResult(res);

      if (res.node_ids?.length > 0) {
        highlightNodes(res.node_ids);
      }

      return res;
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Cypher query failed";
      setError(msg);
      return null;
    } finally {
      setLoading(false);
    }
  }

  return { queryNL, queryCypher, loading, error, result, cypherResult };
}
