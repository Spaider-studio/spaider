"use client";

import { useEffect, useState, useCallback } from "react";
import { listSwarmLinks, deleteSwarmLink } from "@/lib/api";
import type { SwarmLink } from "@/lib/api";
import { Trash2, Zap, RefreshCw, Unlink } from "lucide-react";
import { useToast } from "@/components/shared/Toast";
import { useGraph } from "@/hooks/useGraph";

interface Props {
  /** Bump this number after creating a new connection to trigger a re-fetch. */
  refreshTrigger?: number;
}

export default function SwarmConnectionManager({ refreshTrigger = 0 }: Props) {
  const { toast } = useToast();
  const fetchAll = useGraph((s) => s.fetchAll);

  const [links, setLinks] = useState<SwarmLink[]>([]);
  const [loading, setLoading] = useState(true);
  const [deletingKey, setDeletingKey] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setLinks(await listSwarmLinks());
    } catch {
      toast("Failed to load synaptic bridges", "error");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    load();
  }, [load, refreshTrigger]);

  async function handleDisconnect(link: SwarmLink) {
    const key = `${link.source_id}->${link.target_id}`;
    setDeletingKey(key);
    try {
      await deleteSwarmLink(link.source_id, link.target_id);
      setLinks((prev) =>
        prev.filter(
          (l) => !(l.source_id === link.source_id && l.target_id === link.target_id)
        )
      );
      fetchAll();
      toast(`Bridge "${link.source_name} → ${link.target_name}" removed`, "success");
    } catch (err) {
      toast(
        err instanceof Error ? err.message : "Failed to disconnect agents",
        "error"
      );
    } finally {
      setDeletingKey(null);
    }
  }

  return (
    <div className="bg-white/5 backdrop-blur-md border border-white/10 rounded-xl p-5 flex flex-col gap-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Zap className="w-4 h-4 text-cyan-400" />
          <h3 className="text-sm font-semibold text-[#E4E4E7]">
            Active Synaptic Bridges
          </h3>
          {!loading && (
            <span className="text-[10px] px-1.5 py-0.5 bg-cyan-500/10 border border-cyan-500/20 text-cyan-400 rounded-full">
              {links.length}
            </span>
          )}
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="p-1.5 text-[#6B7280] hover:text-[#E4E4E7] hover:bg-white/5 rounded-lg transition-all disabled:opacity-40"
          title="Refresh"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
        </button>
      </div>

      {/* Body */}
      {loading ? (
        <div className="flex items-center justify-center py-8">
          <RefreshCw className="w-5 h-5 text-[#6B7280] animate-spin" />
        </div>
      ) : links.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-8 gap-2">
          <Unlink className="w-8 h-8 text-[#3A3A45]" />
          <p className="text-xs text-[#6B7280]">No active bridges</p>
          <p className="text-[10px] text-[#3A3A45]">
            Create a connection above to link two agents
          </p>
        </div>
      ) : (
        <ul className="flex flex-col gap-2">
          {links.map((link) => {
            const key = `${link.source_id}->${link.target_id}`;
            const isDeleting = deletingKey === key;

            return (
              <li
                key={key}
                className="flex items-center justify-between bg-white/[0.03] border border-white/[0.06] rounded-lg px-4 py-3 group hover:border-white/[0.12] transition-colors"
              >
                <div className="flex items-center gap-2 text-sm text-[#E4E4E7] min-w-0">
                  <span className="shrink-0">🤖</span>
                  <span className="font-medium truncate">{link.source_name}</span>
                  <span className="shrink-0 text-cyan-400 text-xs font-bold mx-1">
                    ➔
                  </span>
                  <span className="shrink-0">🤖</span>
                  <span className="font-medium truncate">{link.target_name}</span>
                </div>

                <button
                  onClick={() => handleDisconnect(link)}
                  disabled={isDeleting}
                  className="ml-3 shrink-0 flex items-center gap-1.5 px-2.5 py-1.5 text-xs text-[#6B7280] hover:text-[#EF4444] hover:bg-[#EF4444]/10 border border-transparent hover:border-[#EF4444]/20 rounded-lg transition-all opacity-0 group-hover:opacity-100 disabled:opacity-50 disabled:cursor-not-allowed"
                  title="Disconnect"
                >
                  {isDeleting ? (
                    <RefreshCw className="w-3 h-3 animate-spin" />
                  ) : (
                    <Trash2 className="w-3 h-3" />
                  )}
                  {isDeleting ? "Removing…" : "Disconnect"}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
