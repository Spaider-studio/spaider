"use client";

import { useEffect, useRef, useCallback } from "react";

export type WSEvent =
  | { type: "status"; job_id?: string; message: string }
  | { type: "node"; job_id?: string; node: { id: string; label: string; type: string; properties: Record<string, unknown>; agent_id?: string } }
  | { type: "edge"; job_id?: string; edge: { id: string; source: string; target: string; relation: string; properties: Record<string, unknown> } }
  | { type: "done"; job_id?: string; nodes_created: number; nodes_merged: number; edges_created: number; edges_merged: number }
  | { type: "error"; job_id?: string; message: string };

interface WSHandlers {
  onMessage?: (event: WSEvent) => void;
  onOpen?: () => void;
  onClose?: () => void;
}

const WS_BASE =
  typeof window !== "undefined"
    ? (process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000")
    : "ws://localhost:8000";

export function useWebSocket(agentId: string, handlers: WSHandlers) {
  const wsRef = useRef<WebSocket | null>(null);
  // Always use latest handlers without re-connecting
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  const connect = useCallback(() => {
    if (
      wsRef.current &&
      (wsRef.current.readyState === WebSocket.OPEN ||
        wsRef.current.readyState === WebSocket.CONNECTING)
    ) {
      return;
    }

    const ws = new WebSocket(`${WS_BASE}/ws/${encodeURIComponent(agentId)}`);
    wsRef.current = ws;

    ws.onopen = () => {
      handlersRef.current.onOpen?.();
    };
    ws.onclose = () => {
      handlersRef.current.onClose?.();
    };
    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data) as WSEvent;
        handlersRef.current.onMessage?.(msg);
      } catch {
        // ignore malformed frames
      }
    };
    ws.onerror = () => ws.close();
  }, [agentId]);

  const disconnect = useCallback(() => {
    wsRef.current?.close();
    wsRef.current = null;
  }, []);

  useEffect(() => {
    connect();
    return () => disconnect();
  }, [connect, disconnect]);

  return { wsRef, connect, disconnect };
}
