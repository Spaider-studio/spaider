"use client";

import { useEffect, useState } from "react";
import { getAgents, deleteAgent } from "@/lib/api";
import type { Agent } from "@/lib/types";
import {
  Settings,
  Shield,
  Trash2,
  AlertTriangle,
  ExternalLink,
  Bot,
  RefreshCw,
  Info,
  ChevronRight,
  Brain,
  Activity,
} from "lucide-react";
import { useToast } from "@/components/shared/Toast";
import ConfirmDialog from "@/components/shared/ConfirmDialog";
import LoadingSpinner from "@/components/shared/LoadingSpinner";
import Sidebar from "@/components/layout/Sidebar";
import Header from "@/components/layout/Header";
import SystemSettingsToggle from "@/components/shared/SystemSettingsToggle";
import ServiceConnectivityPanel from "@/components/shared/ServiceConnectivityPanel";

export default function SettingsPage() {
  const { toast } = useToast();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loadingAgents, setLoadingAgents] = useState(false);
  const [agentsLoaded, setAgentsLoaded] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<Agent | null>(null);
  const [deleting, setDeleting] = useState(false);

  async function loadAgents() {
    setLoadingAgents(true);
    try {
      const data = await getAgents();
      setAgents(data);
      setAgentsLoaded(true);
    } catch {
      toast("Failed to load agents", "error");
    } finally {
      setLoadingAgents(false);
    }
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await deleteAgent(deleteTarget.id);
      setAgents((prev) => prev.filter((a) => a.id !== deleteTarget.id));
      toast(`Agent "${deleteTarget.name}" and all its data erased`, "success");
    } catch {
      toast("Erasure failed", "error");
    } finally {
      setDeleting(false);
      setDeleteTarget(null);
    }
  }

  return (
    <div className="flex h-screen overflow-hidden bg-[#0A0A0F]">
      <Sidebar />
      <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
        <Header />

        <div className="flex-1 overflow-y-auto p-6">
          <div className="max-w-2xl mx-auto">
            {/* Header */}
            <div className="mb-8">
              <h1 className="text-2xl font-bold text-[#E4E4E7] mb-1">Settings</h1>
              <p className="text-sm text-[#A1A1AA]">
                Configure your SpAIder instance and manage compliance
              </p>
            </div>

            {/* About */}
            <Section title="About" icon={<Info className="w-4 h-4" />}>
              <div className="grid grid-cols-2 gap-4">
                {[
                  { label: "Application", value: "SpAIder" },
                  { label: "Version", value: "1.0.0" },
                  { label: "Backend", value: "FastAPI + Neo4j" },
                  { label: "Streaming", value: "Apache Kafka" },
                ].map((item) => (
                  <div key={item.label} className="flex flex-col gap-0.5">
                    <span className="text-xs text-[#6B7280]">{item.label}</span>
                    <span className="text-sm text-[#E4E4E7] font-mono">{item.value}</span>
                  </div>
                ))}
              </div>
            </Section>

            {/* Service Connectivity */}
            <Section title="Service Connectivity" icon={<Activity className="w-4 h-4" />}>
              <ServiceConnectivityPanel />
            </Section>

            {/* API Configuration */}
            <Section title="API Configuration" icon={<Settings className="w-4 h-4" />}>
              <div className="flex flex-col gap-3">
                <ConfigRow
                  label="Backend URL"
                  value={process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1"}
                  hint="Set NEXT_PUBLIC_API_URL in your .env file"
                />
                <ConfigRow
                  label="Default Agent ID"
                  value={process.env.NEXT_PUBLIC_DEFAULT_AGENT_ID ?? "default"}
                  hint="Set NEXT_PUBLIC_DEFAULT_AGENT_ID in your .env file"
                />
              </div>
              <a
                href="https://github.com/your-org/spaider"
                target="_blank"
                rel="noopener noreferrer"
                className="mt-4 flex items-center gap-2 text-xs text-[#3B82F6] hover:text-[#60A5FA] transition-colors"
              >
                View documentation <ExternalLink className="w-3 h-3" />
              </a>
            </Section>

            {/* GDPR */}
            <Section
              title="GDPR Compliance"
              icon={<Shield className="w-4 h-4" />}
              badge={
                <span className="text-[10px] px-1.5 py-0.5 bg-[#10B981]/10 text-[#10B981] border border-[#10B981]/20 rounded-full">
                  Compliant
                </span>
              }
            >
              <div className="bg-[#0A0A0F] border border-[#2A2A35] rounded-lg p-4 mb-4">
                <div className="flex items-start gap-3">
                  <AlertTriangle className="w-4 h-4 text-[#F59E0B] flex-shrink-0 mt-0.5" />
                  <p className="text-xs text-[#A1A1AA] leading-relaxed">
                    The <strong className="text-[#E4E4E7]">Right-to-Erasure</strong> feature permanently
                    deletes an agent&apos;s entire knowledge graph including all nodes, edges, and
                    embeddings. This action is{" "}
                    <strong className="text-[#E4E4E7]">irreversible</strong> and logged for audit
                    purposes.
                  </p>
                </div>
              </div>

              <div className="flex flex-col gap-2">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-sm text-[#E4E4E7] font-medium">Agent Data Erasure</span>
                  <button
                    onClick={loadAgents}
                    disabled={loadingAgents}
                    className="flex items-center gap-1.5 text-xs text-[#6B7280] hover:text-[#A1A1AA] transition-colors disabled:opacity-50"
                  >
                    <RefreshCw className={`w-3 h-3 ${loadingAgents ? "animate-spin" : ""}`} />
                    Load agents
                  </button>
                </div>

                {!agentsLoaded ? (
                  <p className="text-xs text-[#6B7280] italic">
                    Click &quot;Load agents&quot; to see erasure options
                  </p>
                ) : loadingAgents ? (
                  <div className="flex justify-center py-4">
                    <LoadingSpinner size="sm" />
                  </div>
                ) : agents.length === 0 ? (
                  <div className="flex items-center gap-2 py-3 text-xs text-[#6B7280]">
                    <Bot className="w-4 h-4" />
                    No agents registered
                  </div>
                ) : (
                  <div className="flex flex-col gap-2">
                    {agents.map((agent) => (
                      <div
                        key={agent.id}
                        className="flex items-center justify-between py-3 px-4 bg-[#0A0A0F] border border-[#2A2A35] rounded-lg"
                      >
                        <div className="flex items-center gap-3">
                          <Bot className="w-4 h-4 text-[#6B7280]" />
                          <div>
                            <div className="text-sm text-[#E4E4E7]">{agent.name}</div>
                            <div className="text-[11px] text-[#6B7280] font-mono">{agent.id}</div>
                          </div>
                        </div>
                        <button
                          onClick={() => setDeleteTarget(agent)}
                          className="flex items-center gap-1.5 px-3 py-1.5 bg-[#EF4444]/10 hover:bg-[#EF4444]/20 border border-[#EF4444]/20 hover:border-[#EF4444]/40 text-[#EF4444] text-xs rounded-lg transition-all"
                        >
                          <Trash2 className="w-3 h-3" />
                          Erase All Data
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </Section>

            {/* Autonomous Systems */}
            <Section title="Autonomous Systems" icon={<Brain className="w-4 h-4" />}>
              <SystemSettingsToggle />
            </Section>

            {/* Keyboard shortcuts */}
            <Section title="Keyboard Shortcuts" icon={<ChevronRight className="w-4 h-4" />}>
              <div className="flex flex-col gap-0">
                {[
                  { key: "⌘K", description: "Open command palette" },
                  { key: "⌘/", description: "Focus search" },
                  { key: "Esc", description: "Clear selection / close panels" },
                  { key: "Enter", description: "Submit query" },
                  { key: "⌘R", description: "Refresh graph" },
                ].map((s, i, arr) => (
                  <div
                    key={s.key}
                    className={`flex items-center justify-between py-2.5 ${
                      i < arr.length - 1 ? "border-b border-[#1A1A25]" : ""
                    }`}
                  >
                    <span className="text-sm text-[#A1A1AA]">{s.description}</span>
                    <kbd className="px-2 py-0.5 bg-[#1A1A25] border border-[#2A2A35] rounded text-xs font-mono text-[#A1A1AA]">
                      {s.key}
                    </kbd>
                  </div>
                ))}
              </div>
            </Section>
          </div>
        </div>
      </div>

      <ConfirmDialog
        open={!!deleteTarget}
        onOpenChange={(open) => {
          if (!open && !deleting) setDeleteTarget(null);
        }}
        title={`Erase all data for "${deleteTarget?.name}"?`}
        description={`This will permanently delete all nodes, edges, and embeddings for agent "${deleteTarget?.id}". An audit record will be created. This cannot be undone.`}
        confirmLabel="Erase All Data"
        onConfirm={handleDelete}
        destructive
        loading={deleting}
      />
    </div>
  );
}

function Section({
  title,
  icon,
  badge,
  children,
}: {
  title: string;
  icon: React.ReactNode;
  badge?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="mb-6 bg-[#12121A] border border-[#2A2A35] rounded-xl overflow-hidden">
      <div className="flex items-center gap-2 px-5 py-3 border-b border-[#2A2A35] bg-[#0D0D15]">
        <span className="text-[#6B7280]">{icon}</span>
        <span className="text-sm font-semibold text-[#E4E4E7]">{title}</span>
        {badge}
      </div>
      <div className="p-5">{children}</div>
    </div>
  );
}

function ConfigRow({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <div className="flex items-center justify-between">
        <span className="text-xs text-[#A1A1AA]">{label}</span>
        <code className="text-xs text-[#3B82F6] font-mono bg-[#3B82F6]/10 px-2 py-0.5 rounded">
          {value}
        </code>
      </div>
      {hint && <span className="text-[10px] text-[#6B7280]">{hint}</span>}
    </div>
  );
}
