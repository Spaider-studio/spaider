"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { RefreshCw, CheckCircle2, XCircle, Circle, Wifi, Activity } from "lucide-react";
import { getServiceHealth, getSwarmHealth } from "@/lib/api";
import type { ServiceHealthResponse, SwarmHealthResponse } from "@/lib/api";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Reduced from 15 s → 10 s to match the swarm heartbeat TTL (15 s).
 *  A 10 s poll guarantees we see a new worker within one TTL window. */
const POLL_INTERVAL_MS = 10_000;

/** Infrastructure services — reported live by the backend /health endpoint. */
const INFRA_KEYS = ["neo4j", "redis", "kafka"] as const;
type InfraKey = (typeof INFRA_KEYS)[number];

/** External / legacy services — never reported by the backend.
 *  Always rendered as "Not configured" independently of the API response. */
const EXTERNAL_KEYS = ["flink", "schema_registry"] as const;

const SERVICE_META: Record<string, { label: string; color: string }> = {
  neo4j:           { label: "Neo4j",           color: "#10B981" },
  redis:           { label: "Redis",            color: "#F59E0B" },
  kafka:           { label: "Kafka",            color: "#3B82F6" },
  flink:           { label: "Flink",            color: "#8B5CF6" },
  schema_registry: { label: "Schema Registry",  color: "#EC4899" },
};

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface SectionService {
  key:   string;
  label: string;
  color: string;
  status: "ok" | "unavailable" | "static";
}

// ---------------------------------------------------------------------------
// ServiceConnectivityPanel
// ---------------------------------------------------------------------------

export default function ServiceConnectivityPanel() {
  // ── Infrastructure health (from /health) ────────────────────────────────
  const [health,      setHealth]      = useState<ServiceHealthResponse | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);

  // ── Swarm Pulse (from /swarm/health) ────────────────────────────────────
  const [swarmHealth, setSwarmHealth] = useState<SwarmHealthResponse>({
    active_agents: [],
    total: 0,
  });

  // ── Shared UI state ──────────────────────────────────────────────────────
  const [checking,     setChecking]     = useState(false);
  const [lastChecked,  setLastChecked]  = useState<Date | null>(null);

  // One AbortController per poll cycle, shared by both fetches.
  // A new cycle aborts the previous one before starting, preventing
  // concurrent in-flight requests and stale state updates on slow networks.
  const abortRef = useRef<AbortController | null>(null);

  // ── Dual-fetch poll ──────────────────────────────────────────────────────
  const runCheck = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setChecking(true);
    setHealthError(null);

    try {
      // Both fetches share the same AbortSignal — if the poll cycle is
      // cancelled mid-flight, both requests are aborted atomically.
      const [healthData, swarmData] = await Promise.all([
        getServiceHealth(controller.signal),
        getSwarmHealth(controller.signal),
      ]);

      // Guard against a race where the component unmounted while awaiting.
      if (controller.signal.aborted) return;

      setHealth(healthData);
      setSwarmHealth(swarmData);
      setLastChecked(new Date());
    } catch (err) {
      if ((err as Error).name === "AbortError") return;
      setHealthError(
        err instanceof Error ? err.message : "Health check failed"
      );
      setHealth(null);
      // swarmHealth stays at its last known value — graceful degradation.
    } finally {
      if (!controller.signal.aborted) setChecking(false);
    }
  }, []);

  useEffect(() => {
    runCheck();
    const interval = setInterval(runCheck, POLL_INTERVAL_MS);
    return () => {
      clearInterval(interval);
      abortRef.current?.abort(); // cancel any in-flight request on unmount
    };
  }, [runCheck]);

  // ── Derived state ────────────────────────────────────────────────────────

  const rawServices = health?.services ?? {};

  /** Infrastructure section: only the three known infra keys, in fixed order. */
  const infraServices: SectionService[] = INFRA_KEYS.map((key) => ({
    key,
    label:  SERVICE_META[key].label,
    color:  SERVICE_META[key].color,
    status: (rawServices[key] ?? "unavailable") as "ok" | "unavailable",
  }));

  /** External section: static keys not present in the API response. */
  const externalServices: SectionService[] = EXTERNAL_KEYS
    .filter((key) => !rawServices[key])
    .map((key) => ({
      key,
      label:  SERVICE_META[key].label,
      color:  SERVICE_META[key].color,
      status: "static" as const,
    }));

  /** Overall infrastructure health — .every() over infra keys only. */
  const allInfraOk     = infraServices.every((s) => s.status === "ok");
  const someInfraDown  = infraServices.some((s) => s.status === "unavailable");
  const infraConnected = health !== null;

  // ── Render ───────────────────────────────────────────────────────────────

  return (
    <div className="flex flex-col gap-6">

      {/* ── Global status bar ─────────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="relative">
            <div
              className={`w-2.5 h-2.5 rounded-full ${
                !infraConnected
                  ? "bg-[#6B7280]"
                  : allInfraOk
                  ? "bg-[#10B981]"
                  : "bg-[#EF4444]"
              }`}
            />
            {allInfraOk && infraConnected && (
              <div className="absolute inset-0 w-2.5 h-2.5 rounded-full bg-[#10B981] animate-ping opacity-40" />
            )}
          </div>
          <span className="text-sm text-[#E4E4E7] font-medium">
            {!infraConnected
              ? "Checking services..."
              : allInfraOk
              ? "All services healthy"
              : someInfraDown
              ? "Some services unavailable"
              : "Checking services..."}
          </span>
        </div>
        <button
          onClick={runCheck}
          disabled={checking}
          className="flex items-center gap-1.5 text-xs text-[#6B7280] hover:text-[#A1A1AA] transition-colors disabled:opacity-50"
        >
          <RefreshCw className={`w-3 h-3 ${checking ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </div>

      {/* ── Error banner ──────────────────────────────────────────────── */}
      {healthError && (
        <div className="flex items-center gap-2 py-2 px-3 bg-[#EF4444]/10 border border-[#EF4444]/20 rounded-lg">
          <XCircle className="w-3.5 h-3.5 text-[#EF4444] flex-shrink-0" />
          <span className="text-xs text-[#EF4444]">{healthError}</span>
        </div>
      )}

      {/* ── Section 1: Infrastructure ─────────────────────────────────── */}
      <SectionBlock
        title="Infrastructure"
        icon={<Wifi className="w-3 h-3" />}
      >
        {infraServices.map((svc, i) => (
          <ServiceRow
            key={svc.key}
            svc={svc}
            isLast={i === infraServices.length - 1}
          />
        ))}
      </SectionBlock>

      {/* ── Section 2: Swarm Intelligence (animated worker dots) ──────── */}
      <SwarmSection swarmHealth={swarmHealth} />

      {/* ── Section 3: External Systems ───────────────────────────────── */}
      {infraConnected && externalServices.length > 0 && (
        <SectionBlock
          title="External Systems"
          icon={<Circle className="w-3 h-3" />}
          muted
        >
          {externalServices.map((svc, i) => (
            <ServiceRow
              key={svc.key}
              svc={svc}
              isLast={i === externalServices.length - 1}
            />
          ))}
        </SectionBlock>
      )}

      {/* ── Timestamp ─────────────────────────────────────────────────── */}
      {lastChecked && (
        <div className="text-[10px] text-[#6B7280] text-right">
          Last checked: {lastChecked.toLocaleTimeString()} · auto-refresh every{" "}
          {POLL_INTERVAL_MS / 1000}s
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SectionBlock — labelled container for each panel section
// ---------------------------------------------------------------------------

interface SectionBlockProps {
  title:    string;
  icon:     React.ReactNode;
  muted?:   boolean;
  children: React.ReactNode;
}

function SectionBlock({ title, icon, muted = false, children }: SectionBlockProps) {
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-1.5 mb-1">
        <span className={`${muted ? "text-[#4B5563]" : "text-[#6B7280]"}`}>
          {icon}
        </span>
        <span
          className={`text-[10px] font-semibold uppercase tracking-widest ${
            muted ? "text-[#4B5563]" : "text-[#6B7280]"
          }`}
        >
          {title}
        </span>
        <div className="flex-1 h-px bg-[#1A1A25]" />
      </div>
      <div className="flex flex-col gap-0">{children}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ServiceRow — single infrastructure / external service entry
// ---------------------------------------------------------------------------

interface ServiceRowProps {
  svc:    SectionService;
  isLast: boolean;
}

function ServiceRow({ svc, isLast }: ServiceRowProps) {
  const isOk     = svc.status === "ok";
  const isStatic = svc.status === "static";

  return (
    <div
      className={`flex items-center justify-between py-3 px-1 ${
        isLast ? "" : "border-b border-[#1A1A25]"
      }`}
    >
      <div className="flex items-center gap-3">
        <div
          className="w-7 h-7 rounded-lg flex items-center justify-center"
          style={{ background: `${svc.color}15` }}
        >
          <Wifi className="w-3.5 h-3.5" style={{ color: isStatic ? "#6B7280" : svc.color }} />
        </div>
        <span className={`text-sm ${isStatic ? "text-[#A1A1AA]" : "text-[#E4E4E7]"}`}>
          {svc.label}
        </span>
      </div>

      <div className="flex items-center gap-2">
        {isStatic ? (
          <>
            <span className="text-xs text-[#6B7280]">Not configured</span>
            <Circle className="w-3.5 h-3.5 text-[#6B7280]" />
          </>
        ) : isOk ? (
          <>
            <span className="text-xs font-medium text-[#10B981]">Connected</span>
            <CheckCircle2 className="w-3.5 h-3.5 text-[#10B981]" />
          </>
        ) : (
          <>
            <span className="text-xs font-medium text-[#EF4444]">Unavailable</span>
            <XCircle className="w-3.5 h-3.5 text-[#EF4444]" />
          </>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// SwarmSection — animated live worker heartbeat list
// ---------------------------------------------------------------------------

interface SwarmSectionProps {
  swarmHealth: SwarmHealthResponse;
}

function SwarmSection({ swarmHealth }: SwarmSectionProps) {
  const hasWorkers = swarmHealth.total > 0;

  return (
    <SectionBlock
      title="Swarm Intelligence"
      icon={<Activity className="w-3 h-3" />}
    >
      {!hasWorkers ? (
        /* ── Standby state ─────────────────────────────────────────────── */
        <div className="flex items-center gap-2.5 py-3 px-1">
          <div className="w-7 h-7 rounded-lg bg-[#F59E0B]/5 border border-[#F59E0B]/10 flex items-center justify-center flex-shrink-0">
            <Activity className="w-3.5 h-3.5 text-[#4B5563]" />
          </div>
          <p className="text-xs text-[#4B5563] leading-relaxed">
            No active swarm workers detected.{" "}
            <span className="text-[#6B7280]">System in standby.</span>
          </p>
        </div>
      ) : (
        /* ── Live workers list ─────────────────────────────────────────── */
        <div className="flex flex-col gap-0">
          {/* Worker count header */}
          <div className="flex items-center justify-between py-2 px-1 border-b border-[#1A1A25]">
            <span className="text-xs text-[#6B7280]">
              Active workers
            </span>
            <span className="text-xs font-semibold text-[#10B981]">
              {swarmHealth.total} online
            </span>
          </div>

          {/* Animated worker entries */}
          <AnimatePresence initial={false}>
            {swarmHealth.active_agents.map((agentId, i) => (
              <motion.div
                key={agentId}
                initial={{ opacity: 0, y: -6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, x: 12 }}
                transition={{ duration: 0.2, ease: "easeOut" }}
                className={`flex items-center justify-between py-2.5 px-1 ${
                  i < swarmHealth.active_agents.length - 1
                    ? "border-b border-[#1A1A25]"
                    : ""
                }`}
              >
                <div className="flex items-center gap-3 min-w-0">
                  {/* Pulsing LED dot */}
                  <div className="relative flex-shrink-0">
                    <div className="w-2 h-2 rounded-full bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.8)] animate-pulse" />
                  </div>
                  {/* Worker ID */}
                  <code className="text-[11px] text-[#A1A1AA] font-mono truncate">
                    {agentId}
                  </code>
                </div>
                <span className="text-[10px] font-medium text-[#10B981] flex-shrink-0 ml-2">
                  ● live
                </span>
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
      )}
    </SectionBlock>
  );
}
