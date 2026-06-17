"use client";

import { useEffect, useRef, useState } from "react";
import { Terminal, Wifi, WifiOff } from "lucide-react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type EventType =
  | "error"
  | "actр"        // ACT-R activation boost
  | "boost"
  | "pheromone"
  | "lease"
  | "ack"
  | "dispatch"
  | string;       // fallback for unknown types

interface SwarmEvent {
  id:        string;  // client-generated uuid (for React key)
  type:      EventType;
  agent?:    string;
  message?:  string;
  timestamp: string;
  raw:       Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const MAX_EVENTS = 50;
const SSE_URL    = "/api/v1/swarm/events/stream";

/** Tailwind text-color class per event type. */
function colorFor(type: string): string {
  const t = type.toLowerCase();
  if (t === "error")                        return "text-red-500";
  if (t === "actр" || t === "boost")        return "text-[#39FF14]";   // neon green
  if (t === "pheromone")                    return "text-cyan-400";
  if (t === "lease")                        return "text-amber-400";
  if (t === "ack")                          return "text-emerald-400";
  if (t === "dispatch")                     return "text-violet-400";
  return "text-white/50";
}

/** Short prefix badge per event type. */
function prefixFor(type: string): string {
  const t = type.toLowerCase();
  if (t === "error")                        return "[ERR]";
  if (t === "actр" || t === "boost")        return "[ACT]";
  if (t === "pheromone")                    return "[PHR]";
  if (t === "lease")                        return "[LSE]";
  if (t === "ack")                          return "[ACK]";
  if (t === "dispatch")                     return "[DSP]";
  return `[${type.slice(0, 3).toUpperCase()}]`;
}

// ---------------------------------------------------------------------------
// LivePheromoneStream
// ---------------------------------------------------------------------------

export default function LivePheromoneStream() {
  const [events,    setEvents]    = useState<SwarmEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const idxRef    = useRef(0);   // monotonic id counter — avoids uuid dependency

  // ── EventSource lifecycle ─────────────────────────────────────────────────
  useEffect(() => {
    const es = new EventSource(SSE_URL);

    es.onopen = () => setConnected(true);

    es.onmessage = (ev) => {
      let raw: Record<string, unknown> = {};
      try { raw = JSON.parse(ev.data); } catch { raw = { message: ev.data }; }

      // Heartbeats keep the connection (and proxy) alive on a quiet channel —
      // they are not real activity, so confirm "connected" but don't log them.
      if (raw.type === "heartbeat") {
        setConnected(true);
        return;
      }

      const event: SwarmEvent = {
        id:        String(++idxRef.current),
        type:      (raw.type as string) ?? "unknown",
        agent:     raw.agent as string | undefined,
        message:   raw.message as string | undefined,
        timestamp: (raw.timestamp as string) ?? new Date().toISOString(),
        raw,
      };

      setEvents((prev) => {
        const next = [...prev, event];
        // Keep only the last MAX_EVENTS entries to prevent memory leak.
        return next.length > MAX_EVENTS ? next.slice(next.length - MAX_EVENTS) : next;
      });
    };

    es.onerror = () => {
      setConnected(false);
      // EventSource auto-reconnects — we just reflect the disconnected state
      // until the next successful `onopen`.
    };

    return () => {
      es.close();
      setConnected(false);
    };
  }, []);

  // ── Flicker-free auto-scroll ──────────────────────────────────────────────
  // Runs after every render where `events` changes.  Direct DOM mutation
  // is intentional — setState would cause an extra render cycle.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [events]);

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="flex flex-col gap-2">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <Terminal className="w-3 h-3 text-[#39FF14]/70" />
          <span className="text-[10px] font-semibold uppercase tracking-widest text-[#39FF14]/70">
            Pheromone Stream
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          {connected ? (
            <>
              <div className="w-1.5 h-1.5 rounded-full bg-[#39FF14] animate-pulse shadow-[0_0_6px_rgba(57,255,20,0.8)]" />
              <Wifi className="w-3 h-3 text-[#39FF14]/60" />
            </>
          ) : (
            <>
              <div className="w-1.5 h-1.5 rounded-full bg-red-500/60" />
              <WifiOff className="w-3 h-3 text-red-500/60" />
            </>
          )}
        </div>
      </div>

      {/* Terminal window */}
      <div
        ref={scrollRef}
        className="
          h-48 overflow-y-auto
          bg-black border border-[#39FF14]/10
          rounded-lg p-2
          font-mono text-xs
          scrollbar-thin scrollbar-thumb-white/10 scrollbar-track-transparent
        "
      >
        {events.length === 0 ? (
          <div className="flex items-center justify-center h-full">
            <span className="text-white/20 text-[10px]">
              {connected ? "Awaiting swarm events…" : "Connecting…"}
            </span>
          </div>
        ) : (
          events.map((ev) => {
            const color = colorFor(ev.type);
            const prefix = prefixFor(ev.type);
            const ts = ev.timestamp.slice(11, 19); // HH:MM:SS
            const agent = ev.agent ? `${ev.agent.slice(0, 16)} ` : "";
            const msg = ev.message ?? JSON.stringify(ev.raw);

            return (
              <div key={ev.id} className="leading-5 whitespace-pre-wrap break-all">
                <span className="text-white/20">{ts} </span>
                <span className={`${color} font-semibold`}>{prefix} </span>
                {agent && (
                  <span className="text-white/30">{agent}</span>
                )}
                <span className={color}>{msg}</span>
              </div>
            );
          })
        )}
      </div>

      {/* Footer: event count */}
      <div className="text-[9px] text-white/20 text-right font-mono">
        {events.length}/{MAX_EVENTS} events
      </div>
    </div>
  );
}
