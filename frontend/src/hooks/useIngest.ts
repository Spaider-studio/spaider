"use client";

import { useState, useCallback } from "react";
import { useGraph } from "./useGraph";
import type { GraphNode, GraphEdge } from "@/lib/types";

// Direct URL for ingest — bypasses Next.js proxy which times out on long LLM calls.
// CORS is configured on the backend to allow localhost:3000.
const BACKEND_URL =
  typeof window !== "undefined"
    ? (process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000/api/v1")
    : "http://localhost:8000/api/v1";

export type IngestStatus = "idle" | "processing" | "animating" | "done" | "error";

export function useIngest() {
  const [status, setStatus] = useState<IngestStatus>("idle");
  const [statusMessage, setStatusMessage] = useState("");
  const [nodesAdded, setNodesAdded] = useState(0);
  const [edgesAdded, setEdgesAdded] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const { addNodes, addEdges, highlightNodes } = useGraph();

  const _reset = useCallback(() => {
    setStatus("processing");
    setStatusMessage("Extracting entities…");
    setError(null);
    setNodesAdded(0);
    setEdgesAdded(0);
  }, []);

  /** Animate nodes + edges into the graph one-by-one for visual pop-in effect */
  async function _animate(nodes: GraphNode[], edges: GraphEdge[]) {
    setStatus("animating");
    setStatusMessage(`Adding ${nodes.length} nodes to graph…`);

    for (let i = 0; i < nodes.length; i++) {
      addNodes([nodes[i]]);
      highlightNodes([nodes[i].id]);
      setNodesAdded(i + 1);
      await sleep(80);
    }

    setStatusMessage(`Linking ${edges.length} edges…`);
    for (let i = 0; i < edges.length; i++) {
      addEdges([edges[i]]);
      setEdgesAdded(i + 1);
      await sleep(25);
    }

    setStatus("done");
    setStatusMessage(`Done — ${nodes.length} entities, ${edges.length} relationships`);
    setTimeout(() => useGraph.getState().clearHighlights(), 5000);
  }

  const ingestText = useCallback(
    async (text: string, source?: string): Promise<void> => {
      const agentId = useGraph.getState().agentId;
      if (!agentId) {
        setStatus("error");
        setError("Select an agent before ingesting — Multiverse is read-only.");
        return;
      }
      _reset();
      try {
        const res = await fetch(`${BACKEND_URL}/ingest/sync`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text, agent_id: useGraph.getState().agentId, source }),
        });

        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.detail ?? `HTTP ${res.status}`);
        }

        const data = await res.json();

        const nodes: GraphNode[] = (data.nodes ?? []).map((n: any) => ({
          id: n.id,
          label: n.label,
          type: n.type as GraphNode["type"],
          properties: n.properties ?? {},
          agent_id: n.agent_id,
        }));

        const edges: GraphEdge[] = (data.edges ?? []).map((e: any) => ({
          id: e.id,
          source: e.source,
          target: e.target,
          relation: e.relation,
          properties: {},
        }));

        if (nodes.length === 0) {
          setStatus("done");
          setStatusMessage("No entities found — try a longer or more descriptive text.");
          return;
        }

        await _animate(nodes, edges);
      } catch (e) {
        setStatus("error");
        setError(e instanceof Error ? e.message : "Ingest failed");
      }
    },
    [_reset, addNodes, addEdges, highlightNodes]
  );

  const ingestFile = useCallback(
    async (file: File): Promise<void> => {
      _reset();
      const formData = new FormData();
      formData.append("file", file);
      const agentId = useGraph.getState().agentId;

      if (!agentId) {
        setStatus("error");
        setError("Select an agent before ingesting — Multiverse is read-only.");
        return;
      }
      formData.append("agent_id", agentId);
      try {
        const res = await fetch(`${BACKEND_URL}/ingest/file`, {
          method: "POST",
          body: formData,
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.detail ?? `HTTP ${res.status}`);
        }
        const data = await res.json();

        const nodes: GraphNode[] = (data.nodes ?? []).map((n: any) => ({
          id: n.id,
          label: n.label,
          type: n.type as GraphNode["type"],
          properties: n.properties ?? {},
          agent_id: n.agent_id,
        }));
        const edges: GraphEdge[] = (data.edges ?? []).map((e: any) => ({
          id: e.id,
          source: e.source,
          target: e.target,
          relation: e.relation,
          properties: {},
        }));

        if (nodes.length === 0) {
          setStatus("done");
          setStatusMessage("No entities found in file.");
          return;
        }

        await _animate(nodes, edges);
      } catch (e) {
        setStatus("error");
        setError(e instanceof Error ? e.message : "File ingest failed");
      }
    },
    [_reset, addNodes, addEdges, highlightNodes]
  );

  /** Upload multiple files (PDF, DOCX, PPTX, HTML, MD, TXT). */
  const ingestFiles = useCallback(
    async (files: File[]): Promise<void> => {
      const agentId = useGraph.getState().agentId;
      if (!agentId) {
        setStatus("error");
        setError("Select an agent before ingesting — Multiverse is read-only.");
        return;
      }
      _reset();
      const formData = new FormData();
      files.forEach((f) => formData.append("files", f));
      formData.append("agent_id", agentId);
      try {
        const res = await fetch(`${BACKEND_URL}/ingest/files`, {
          method: "POST",
          body: formData,
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.detail ?? `HTTP ${res.status}`);
        }
        const data = await res.json();
        const nodes: GraphNode[] = (data.nodes ?? []).map((n: any) => ({
          id: n.id,
          label: n.label,
          type: n.type as GraphNode["type"],
          properties: n.properties ?? {},
          agent_id: n.agent_id,
        }));
        const edges: GraphEdge[] = (data.edges ?? []).map((e: any) => ({
          id: e.id,
          source: e.source,
          target: e.target,
          relation: e.relation,
          properties: {},
        }));
        if (nodes.length === 0) {
          setStatus("done");
          setStatusMessage("No entities found in the uploaded files.");
          return;
        }
        await _animate(nodes, edges);
      } catch (e) {
        setStatus("error");
        setError(e instanceof Error ? e.message : "File upload failed");
      }
    },
    [_reset, addNodes, addEdges, highlightNodes]
  );

  /**
   * Fetch one or more URLs and ingest their content.
   * @param urlInput Raw user input — newline or comma-separated URLs.
   */
  const ingestUrl = useCallback(
    async (urlInput: string): Promise<void> => {
      const agentId = useGraph.getState().agentId;
      if (!agentId) {
        setStatus("error");
        setError("Select an agent before ingesting — Multiverse is read-only.");
        return;
      }
      const urls = urlInput
        .split(/[\n,]+/)
        .map((u) => u.trim())
        .filter((u) => /^https?:\/\//i.test(u));
      if (urls.length === 0) {
        setStatus("error");
        setError("Enter at least one valid HTTP or HTTPS URL.");
        return;
      }
      _reset();
      try {
        const res = await fetch(`${BACKEND_URL}/ingest/url`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ urls, agent_id: agentId }),
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.detail ?? `HTTP ${res.status}`);
        }
        const data = await res.json();
        const nodes: GraphNode[] = (data.nodes ?? []).map((n: any) => ({
          id: n.id,
          label: n.label,
          type: n.type as GraphNode["type"],
          properties: n.properties ?? {},
          agent_id: n.agent_id,
        }));
        const edges: GraphEdge[] = (data.edges ?? []).map((e: any) => ({
          id: e.id,
          source: e.source,
          target: e.target,
          relation: e.relation,
          properties: {},
        }));
        if (nodes.length === 0) {
          setStatus("done");
          setStatusMessage("No new content — URLs may be cached (304 Not Modified).");
          return;
        }
        await _animate(nodes, edges);
      } catch (e) {
        setStatus("error");
        setError(e instanceof Error ? e.message : "URL ingest failed");
      }
    },
    [_reset, addNodes, addEdges, highlightNodes]
  );

  const loading = status === "processing" || status === "animating";
  const result =
    status === "done"
      ? { nodes_created: nodesAdded, nodes_merged: 0, edges_created: edgesAdded, edges_merged: 0, latency_ms: 0 }
      : null;

  return {
    ingestText,
    ingestFile,
    ingestFiles,
    ingestUrl,
    loading,
    error,
    result,
    status,
    statusMessage,
    nodesAdded,
    edgesAdded,
  };
}

function sleep(ms: number) {
  return new Promise<void>((r) => setTimeout(r, ms));
}
