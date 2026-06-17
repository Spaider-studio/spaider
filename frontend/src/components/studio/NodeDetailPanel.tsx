"use client";

import { useState } from "react";
import { Trash2, Loader2, Copy, ChevronDown, ChevronRight } from "lucide-react";
import Badge from "@/components/shared/Badge";
import ConfirmDialog from "@/components/shared/ConfirmDialog";
import { deleteNode } from "@/lib/api";
import { formatDate, truncate } from "@/lib/utils";
import { useGraph } from "@/hooks/useGraph";
import { useToast } from "@/components/shared/Toast";
import type { GraphNode } from "@/lib/types";

interface Props {
  node: GraphNode;
}

export default function NodeDetailPanel({ node }: Props) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [copied, setCopied] = useState(false);
  const [propsExpanded, setPropsExpanded] = useState(true);
  const { removeNode, setSelectedNode, edges } = useGraph();
  const { toast } = useToast();

  const connectedEdges = edges.filter(
    (e) => e.source === node.id || e.target === node.id
  );

  async function handleDelete() {
    setDeleting(true);
    try {
      const result = await deleteNode(node.id);
      removeNode(node.id);
      setSelectedNode(null);
      setConfirmOpen(false);
      toast(
        `Deleted "${node.label}" and ${result.deleted_edges} edge(s)`,
        "success"
      );
    } catch (e) {
      toast(
        e instanceof Error ? e.message : "Delete failed",
        "error"
      );
    } finally {
      setDeleting(false);
    }
  }

  function copyId() {
    navigator.clipboard.writeText(node.id);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  const displayProps = Object.entries(node.properties ?? {}).filter(
    ([k]) => k !== "embedding"
  );

  const allNodes = useGraph.getState().nodes;

  return (
    <>
      <div className="flex flex-col gap-4 animate-fade-in">
        {/* Header */}
        <div>
          <h2
            className="text-lg font-semibold text-[#E4E4E7] leading-tight break-words line-clamp-4"
            title={node.label}
          >
            {node.label}
          </h2>
          <div className="mt-1.5">
            <Badge type={node.type} />
          </div>
        </div>

        {/* ID */}
        <button
          className="flex items-center gap-2 bg-[#0A0A0F] rounded-lg px-3 py-2 group w-full text-left hover:bg-[#0F0F18] transition-colors"
          onClick={copyId}
          title="Click to copy ID"
        >
          <span className="text-xs font-mono text-[#A1A1AA] flex-1 truncate">
            {truncate(node.id, 30)}
          </span>
          {copied ? (
            <span className="text-xs text-[#10B981] flex-shrink-0">Copied!</span>
          ) : (
            <Copy className="w-3 h-3 text-[#6B7280] group-hover:text-[#A1A1AA] transition-colors flex-shrink-0" />
          )}
        </button>

        {/* Metadata */}
        {(node.agent_id || node.created_at || node.updated_at) && (
          <div className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
            {node.agent_id && (
              <>
                <span className="text-[#6B7280]">Agent</span>
                <span className="text-[#A1A1AA] font-mono truncate">{node.agent_id}</span>
              </>
            )}
            {node.created_at && (
              <>
                <span className="text-[#6B7280]">Created</span>
                <span className="text-[#A1A1AA]">{formatDate(node.created_at)}</span>
              </>
            )}
            {node.updated_at && (
              <>
                <span className="text-[#6B7280]">Updated</span>
                <span className="text-[#A1A1AA]">{formatDate(node.updated_at)}</span>
              </>
            )}
          </div>
        )}

        {/* Properties */}
        {displayProps.length > 0 && (
          <div>
            <button
              className="flex items-center gap-1.5 text-xs font-semibold text-[#A1A1AA] uppercase tracking-wider mb-2 hover:text-[#E4E4E7] transition-colors"
              onClick={() => setPropsExpanded((v) => !v)}
            >
              {propsExpanded ? (
                <ChevronDown className="w-3.5 h-3.5" />
              ) : (
                <ChevronRight className="w-3.5 h-3.5" />
              )}
              Properties ({displayProps.length})
            </button>
            {propsExpanded && (
              <div className="flex flex-col gap-1.5 pl-1">
                {displayProps.map(([key, value]) => (
                  <div key={key} className="grid grid-cols-[auto_1fr] gap-x-3 text-xs items-start">
                    <span className="text-[#6B7280] font-medium min-w-[4rem] max-w-[7rem] truncate">
                      {key}
                    </span>
                    <span className="text-[#E4E4E7] break-words">
                      {typeof value === "object"
                        ? JSON.stringify(value, null, 2)
                        : String(value)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Connected Edges */}
        {connectedEdges.length > 0 && (
          <div>
            <p className="text-xs font-semibold text-[#A1A1AA] uppercase tracking-wider mb-2">
              Connections ({connectedEdges.length})
            </p>
            <div className="flex flex-col gap-0.5 max-h-44 overflow-y-auto pr-1">
              {connectedEdges.slice(0, 25).map((e) => {
                const isSource = e.source === node.id;
                const otherId = isSource ? e.target : e.source;
                const other = allNodes.find((n) => n.id === otherId);
                return (
                  <div
                    key={e.id}
                    className="flex items-center gap-2 text-xs text-[#A1A1AA] py-1 border-b border-[#1A1A25] last:border-0"
                  >
                    <span className="text-[#6B7280] w-3 flex-shrink-0 text-center font-mono">
                      {isSource ? "→" : "←"}
                    </span>
                    <span
                      className="font-mono text-[10px] px-1.5 py-0.5 rounded flex-shrink-0"
                      style={{
                        color: "#3B82F6",
                        background: "rgba(59,130,246,0.1)",
                        border: "1px solid rgba(59,130,246,0.2)",
                      }}
                    >
                      {e.relation}
                    </span>
                    <button
                      className="text-[#E4E4E7] truncate hover:text-[#3B82F6] transition-colors text-left flex-1"
                      onClick={() => {
                        const otherNode = allNodes.find((n) => n.id === otherId);
                        if (otherNode) setSelectedNode(otherNode);
                      }}
                    >
                      {other?.label ?? otherId.slice(0, 10) + "…"}
                    </button>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* GDPR Delete */}
        <div className="pt-2 border-t border-[#2A2A35]">
          <button
            onClick={() => setConfirmOpen(true)}
            className="w-full flex items-center justify-center gap-2 text-sm font-medium px-3 py-2.5 rounded-lg bg-[#EF4444]/10 hover:bg-[#EF4444]/20 text-[#EF4444] hover:text-[#FCA5A5] border border-[#EF4444]/20 transition-all"
          >
            <Trash2 className="w-4 h-4" />
            GDPR Killswitch — Permanent Delete
          </button>
          <p className="text-[10px] text-[#6B7280] text-center mt-1.5">
            Cascading hard delete of node + {connectedEdges.length} edge(s) · audit log created
          </p>
        </div>
      </div>

      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title={`Permanently delete "${node.label}"?`}
        description={`This will permanently erase this node and cascade-delete ${connectedEdges.length} connected edge(s). An audit log entry will be created. This action cannot be undone.`}
        confirmLabel="Permanently Delete"
        onConfirm={handleDelete}
        destructive
        loading={deleting}
      />
    </>
  );
}
