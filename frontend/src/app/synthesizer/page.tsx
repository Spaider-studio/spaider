"use client";

import { useState, useEffect } from "react";
import { synthesize, downloadDataset, exportChatML, exportDpo, generateAgenticDataset, getAgents } from "@/lib/api";
import type { SynthesizeResult, Agent } from "@/lib/types";
import {
  Database,
  Download,
  Play,
  ChevronDown,
  ChevronUp,
  FileText,
  Sparkles,
  Layers,
  GitBranch,
  Zap,
  Globe,
  Bot,
  Sliders,
} from "lucide-react";
import { useToast } from "@/components/shared/Toast";
import LoadingSpinner from "@/components/shared/LoadingSpinner";
import { SYNTHESIS_STRATEGIES } from "@/lib/constants";
import Sidebar from "@/components/layout/Sidebar";
import Header from "@/components/layout/Header";

const STRATEGY_ICONS: Record<string, React.ReactNode> = {
  factual_qa: <Sparkles className="w-5 h-5 text-[#3B82F6]" />,
  reasoning_chains: <GitBranch className="w-5 h-5 text-[#10B981]" />,
  relation_extraction: <Layers className="w-5 h-5 text-[#8B5CF6]" />,
};

export default function SynthesizerPage() {
  const { toast } = useToast();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [result, setResult] = useState<SynthesizeResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);

  // Training-data export state (ChatML = SFT, DPO = preference pairs)
  const [exportAgentId, setExportAgentId] = useState<string>("");
  const [exportFormat, setExportFormat] = useState<"chatml" | "dpo">("chatml");
  const [exporting, setExporting] = useState(false);

  // Agentic dataset state
  const [agenticAgentId, setAgenticAgentId] = useState<string>("");
  const [agenticSamples, setAgenticSamples] = useState(50);
  const [agenticConcurrency, setAgenticConcurrency] = useState(5);
  const [agenticLoading, setAgenticLoading] = useState(false);

  const [config, setConfig] = useState({
    agent_id: "",
    strategy: "factual_qa",
    max_samples: 100,
    output_format: "jsonl",
    min_confidence: 0.5,
    min_path_length: 1,
  });

  useEffect(() => {
    getAgents().then(setAgents).catch(() => {});
  }, []);

  async function handleSynthesize(e: React.FormEvent) {
    e.preventDefault();
    if (!config.agent_id) {
      toast("Please select an agent", "error");
      return;
    }
    setLoading(true);
    setResult(null);
    try {
      const data = await synthesize({
        agent_id: config.agent_id,
        strategy: config.strategy,
        max_samples: config.max_samples,
        output_format: config.output_format,
        min_confidence: config.min_confidence,
        min_path_length: config.min_path_length,
      });
      setResult(data);
      toast(
        `Generated ${data.example_count ?? data.stats?.total_samples ?? 0} examples`,
        "success"
      );
    } catch (err: unknown) {
      toast((err as Error).message || "Synthesis failed", "error");
    } finally {
      setLoading(false);
    }
  }

  async function handleDownload() {
    if (!result?.dataset_id) return;
    try {
      const blob = await downloadDataset(result.dataset_id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `dataset_${result.dataset_id}.jsonl`;
      a.click();
      URL.revokeObjectURL(url);
      toast("Download started", "success");
    } catch {
      toast("Download failed", "error");
    }
  }

  async function handleGenerateAgentic() {
    setAgenticLoading(true);
    try {
      const { blob, filename } = await generateAgenticDataset({
        agentId: agenticAgentId || undefined,
        numSamples: agenticSamples,
        concurrency: agenticConcurrency,
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
      toast(`Agentic dataset downloaded: ${filename}`, "success");
    } catch (err: unknown) {
      toast((err as Error).message || "Generation failed", "error");
    } finally {
      setAgenticLoading(false);
    }
  }

  async function handleExportTrainingData() {
    setExporting(true);
    try {
      const { blob, filename } =
        exportFormat === "dpo"
          ? await exportDpo(exportAgentId) // requires a specific agent
          : await exportChatML(exportAgentId || undefined);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
      toast(`Training data downloaded: ${filename}`, "success");
    } catch (err: unknown) {
      // The DPO 422 guardrail returns an actionable message (no usage signal
      // yet) — exportDpo surfaces it here verbatim.
      toast((err as Error).message || "Export failed", "error");
    } finally {
      setExporting(false);
    }
  }

  const exampleCount = result
    ? (result.example_count ?? result.stats?.total_samples ?? 0)
    : 0;

  return (
    <div className="flex h-screen overflow-hidden bg-[#0A0A0F]">
      <Sidebar />
      <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
        <Header />

        <div className="flex-1 overflow-y-auto p-6">
          <div className="max-w-3xl mx-auto">
            {/* Page header */}
            <div className="mb-8">
              <h1 className="text-2xl font-bold text-[#E4E4E7] mb-1">Model Synthesizer</h1>
              <p className="text-sm text-[#A1A1AA]">
                Generate JSONL training datasets from your knowledge graph using multiple synthesis
                strategies.
              </p>
            </div>

            {/* Strategy selection cards */}
            <div className="grid grid-cols-3 gap-4 mb-8">
              {SYNTHESIS_STRATEGIES.map((s) => (
                <button
                  key={s.id}
                  onClick={() => setConfig((c) => ({ ...c, strategy: s.id }))}
                  className={`p-4 rounded-xl border text-left transition-all ${
                    config.strategy === s.id
                      ? "bg-[#3B82F6]/10 border-[#3B82F6]/30 text-[#E4E4E7]"
                      : "bg-[#12121A] border-[#2A2A35] text-[#A1A1AA] hover:border-[#3A3A45]"
                  }`}
                >
                  <div className="mb-2">{STRATEGY_ICONS[s.id]}</div>
                  <div className="text-sm font-medium mb-1">{s.label}</div>
                  <div className="text-xs text-[#6B7280] leading-relaxed">{s.description}</div>
                </button>
              ))}
            </div>

            {/* Config form */}
            <form
              onSubmit={handleSynthesize}
              className="bg-[#12121A] border border-[#2A2A35] rounded-xl p-6 mb-6"
            >
              <h2 className="text-sm font-semibold text-[#E4E4E7] mb-5">Configuration</h2>

              <div className="grid grid-cols-2 gap-4 mb-4">
                <div className="flex flex-col gap-1.5">
                  <label className="text-xs text-[#A1A1AA] font-medium">
                    Agent <span className="text-[#EF4444]">*</span>
                  </label>
                  <select
                    value={config.agent_id}
                    onChange={(e) => setConfig((c) => ({ ...c, agent_id: e.target.value }))}
                    className="bg-[#0A0A0F] border border-[#2A2A35] rounded-lg px-3 py-2 text-sm text-[#E4E4E7] focus:outline-none focus:border-[#3B82F6]/50 transition-colors"
                    required
                  >
                    <option value="">Select agent…</option>
                    {agents.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.name}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="flex flex-col gap-1.5">
                  <label className="text-xs text-[#A1A1AA] font-medium">Output Format</label>
                  <select
                    value={config.output_format}
                    onChange={(e) => setConfig((c) => ({ ...c, output_format: e.target.value }))}
                    className="bg-[#0A0A0F] border border-[#2A2A35] rounded-lg px-3 py-2 text-sm text-[#E4E4E7] focus:outline-none focus:border-[#3B82F6]/50 transition-colors"
                  >
                    <option value="jsonl">JSONL (raw triplets)</option>
                    <option value="alpaca">Alpaca (instruction format)</option>
                    <option value="sharegpt">ShareGPT (chat format)</option>
                  </select>
                </div>
              </div>

              <div className="flex flex-col gap-1.5 mb-4">
                <label className="text-xs text-[#A1A1AA] font-medium">
                  Max Samples: {config.max_samples}
                </label>
                <input
                  type="range"
                  min={10}
                  max={1000}
                  step={10}
                  value={config.max_samples}
                  onChange={(e) =>
                    setConfig((c) => ({ ...c, max_samples: Number(e.target.value) }))
                  }
                  className="accent-[#3B82F6]"
                />
                <div className="flex justify-between text-[10px] text-[#6B7280]">
                  <span>10</span>
                  <span>1,000</span>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4 mb-6">
                <div className="flex flex-col gap-1.5">
                  <label className="text-xs text-[#A1A1AA] font-medium">
                    Min Confidence: {config.min_confidence.toFixed(1)}
                  </label>
                  <input
                    type="range"
                    min={0}
                    max={1}
                    step={0.1}
                    value={config.min_confidence}
                    onChange={(e) =>
                      setConfig((c) => ({ ...c, min_confidence: Number(e.target.value) }))
                    }
                    className="accent-[#3B82F6]"
                  />
                </div>
                <div className="flex flex-col gap-1.5">
                  <label className="text-xs text-[#A1A1AA] font-medium">
                    Min Path Length: {config.min_path_length}
                  </label>
                  <input
                    type="range"
                    min={1}
                    max={5}
                    step={1}
                    value={config.min_path_length}
                    onChange={(e) =>
                      setConfig((c) => ({ ...c, min_path_length: Number(e.target.value) }))
                    }
                    className="accent-[#3B82F6]"
                  />
                </div>
              </div>

              <button
                type="submit"
                disabled={loading}
                className="w-full flex items-center justify-center gap-2 py-2.5 bg-[#3B82F6] hover:bg-[#2563EB] text-white text-sm font-medium rounded-lg transition-colors disabled:opacity-50"
              >
                {loading ? (
                  <>
                    <LoadingSpinner size="sm" />
                    Synthesizing...
                  </>
                ) : (
                  <>
                    <Play className="w-4 h-4" />
                    Generate Dataset
                  </>
                )}
              </button>
            </form>

            {/* Agentic Tool-Call Dataset */}
            <div className="bg-[#12121A] border border-violet-500/20 rounded-xl p-6 mb-6">
              <div className="flex items-center gap-2 mb-1">
                <Bot className="w-4 h-4 text-violet-400" />
                <h2 className="text-sm font-semibold text-[#E4E4E7]">Agentic Tool-Call Dataset</h2>
                <span className="ml-auto text-[10px] px-2 py-0.5 bg-violet-500/10 text-violet-400 border border-violet-500/20 rounded-full font-medium">
                  Teacher LLM · SLM Fine-Tuning
                </span>
              </div>
              <p className="text-xs text-[#6B7280] mb-5 leading-relaxed">
                Uses a strong Teacher LLM to synthesise realistic 5-turn{" "}
                <code className="text-violet-400/80 bg-[#0A0A0F] px-1 py-0.5 rounded text-[11px]">tool_call</code>{" "}
                trajectories. Samples random sub-graphs across{" "}
                <code className="text-violet-400/80 bg-[#0A0A0F] px-1 py-0.5 rounded text-[11px]">SHARES_KNOWLEDGE_WITH</code>{" "}
                bridges. Trains small models to autonomously call{" "}
                <code className="text-violet-400/80 bg-[#0A0A0F] px-1 py-0.5 rounded text-[11px]">query_spaider</code>.
              </p>

              <div className="grid grid-cols-2 gap-3 mb-4">
                <div className="flex flex-col gap-1.5">
                  <label className="text-xs text-[#A1A1AA] font-medium flex items-center gap-1.5">
                    <Globe className="w-3 h-3" />
                    Scope
                  </label>
                  <select
                    value={agenticAgentId}
                    onChange={(e) => setAgenticAgentId(e.target.value)}
                    className="bg-[#0A0A0F] border border-[#2A2A35] rounded-lg px-3 py-2 text-sm text-[#E4E4E7] focus:outline-none focus:border-violet-500/40 transition-colors"
                  >
                    <option value="">🌌 All Agents — Multiverse</option>
                    {agents.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.name}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="flex flex-col gap-1.5">
                  <label className="text-xs text-[#A1A1AA] font-medium flex items-center gap-1.5">
                    <Sliders className="w-3 h-3" />
                    Concurrency: {agenticConcurrency} parallel LLM calls
                  </label>
                  <input
                    type="range"
                    min={1}
                    max={20}
                    step={1}
                    value={agenticConcurrency}
                    onChange={(e) => setAgenticConcurrency(Number(e.target.value))}
                    className="accent-violet-500 mt-1"
                  />
                </div>
              </div>

              <div className="flex flex-col gap-1.5 mb-5">
                <label className="text-xs text-[#A1A1AA] font-medium">
                  Trajectories: {agenticSamples}
                </label>
                <input
                  type="range"
                  min={5}
                  max={500}
                  step={5}
                  value={agenticSamples}
                  onChange={(e) => setAgenticSamples(Number(e.target.value))}
                  className="accent-violet-500"
                />
                <div className="flex justify-between text-[10px] text-[#6B7280]">
                  <span>5</span>
                  <span>500</span>
                </div>
              </div>

              <button
                onClick={handleGenerateAgentic}
                disabled={agenticLoading}
                className="w-full flex items-center justify-center gap-2 py-2.5 bg-violet-600/20 hover:bg-violet-600/30 border border-violet-500/30 hover:border-violet-500/50 text-violet-300 hover:text-violet-100 text-sm font-medium rounded-lg transition-all disabled:opacity-50"
              >
                {agenticLoading ? (
                  <>
                    <span className="w-3.5 h-3.5 rounded-full border-2 border-violet-400/30 border-t-violet-400 animate-spin flex-shrink-0" />
                    Generating {agenticSamples} trajectories…
                  </>
                ) : (
                  <>
                    <Bot className="w-4 h-4" />
                    Generate Agentic Dataset ({agenticSamples} samples)
                  </>
                )}
              </button>

              {/* Format preview */}
              <div className="mt-4 bg-[#0A0A0F] border border-[#2A2A35] rounded-lg p-3">
                <div className="text-[10px] text-[#6B7280] mb-2 font-medium uppercase tracking-wider">Output format per line</div>
                <pre className="text-[10px] text-violet-400/70 font-mono leading-relaxed overflow-x-auto">{`{ "messages": [
  { "role": "system",    "content": "You are a SpAIder agent..." },
  { "role": "user",      "content": "<user question>" },
  { "role": "assistant", "tool_calls": [{ "function": { "name": "query_spaider", ... } }] },
  { "role": "tool",      "content": "<graph result>" },
  { "role": "assistant", "content": "<final answer>" }
], "tools": [<query_spaider definition>] }`}</pre>
              </div>
            </div>

            {/* Training Data Export (ChatML / DPO) */}
            <div className="bg-[#12121A] border border-[#2A2A35] rounded-xl p-6 mb-6">
              <div className="flex items-center gap-2 mb-1">
                <Zap className="w-4 h-4 text-cyan-400" />
                <h2 className="text-sm font-semibold text-[#E4E4E7]">Training Data Export</h2>
                <span className="ml-auto text-[10px] px-2 py-0.5 bg-cyan-400/10 text-cyan-400 border border-cyan-400/20 rounded-full font-medium">
                  {exportFormat === "dpo" ? "TRL · Unsloth" : "OpenAI · LM-Studio"}
                </span>
              </div>
              <p className="text-xs text-[#6B7280] mb-5 leading-relaxed">
                {exportFormat === "dpo" ? (
                  <>
                    Stream <code className="text-cyan-400/80 bg-[#0A0A0F] px-1 py-0.5 rounded text-[11px]">{`{prompt, chosen, rejected}`}</code> preference
                    pairs labelled by the graph&apos;s own usage signal (RLHG) — high-energy paths become <em>chosen</em>, dead ends become <em>rejected</em>.
                    Needs an agent that has actually been queried; a fresh graph has no signal yet.
                  </>
                ) : (
                  <>
                    Stream the knowledge graph as a <code className="text-cyan-400/80 bg-[#0A0A0F] px-1 py-0.5 rounded text-[11px]">.jsonl</code> fine-tuning file — one ChatML record per node, direct from Neo4j.
                    Memory footprint O(1); safe for arbitrarily large graphs.
                  </>
                )}
              </p>

              <div className="flex gap-3 items-end">
                <div className="flex flex-col gap-1.5 w-44">
                  <label className="text-xs text-[#A1A1AA] font-medium">Format</label>
                  <select
                    value={exportFormat}
                    onChange={(e) => setExportFormat(e.target.value as "chatml" | "dpo")}
                    className="bg-[#0A0A0F] border border-[#2A2A35] rounded-lg px-3 py-2 text-sm text-[#E4E4E7] focus:outline-none focus:border-cyan-400/40 transition-colors"
                  >
                    <option value="chatml">ChatML — SFT</option>
                    <option value="dpo">DPO — preference pairs</option>
                  </select>
                </div>

                <div className="flex flex-col gap-1.5 flex-1">
                  <label className="text-xs text-[#A1A1AA] font-medium flex items-center gap-1.5">
                    <Globe className="w-3 h-3" />
                    Scope
                  </label>
                  <select
                    value={exportAgentId}
                    onChange={(e) => setExportAgentId(e.target.value)}
                    className="bg-[#0A0A0F] border border-[#2A2A35] rounded-lg px-3 py-2 text-sm text-[#E4E4E7] focus:outline-none focus:border-cyan-400/40 transition-colors"
                  >
                    {/* DPO traverses one agent's usage signal — no multiverse export */}
                    {exportFormat === "chatml" && (
                      <option value="">🌌 All Agents — Multiverse Export</option>
                    )}
                    {exportFormat === "dpo" && !exportAgentId && (
                      <option value="">Select an agent…</option>
                    )}
                    {agents.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.name}
                      </option>
                    ))}
                  </select>
                </div>

                <button
                  onClick={handleExportTrainingData}
                  disabled={exporting || (exportFormat === "dpo" && !exportAgentId)}
                  className="flex items-center gap-2 px-5 py-2 bg-cyan-500/15 hover:bg-cyan-500/25 border border-cyan-400/30 hover:border-cyan-400/50 text-cyan-300 hover:text-cyan-100 text-sm font-medium rounded-lg transition-all disabled:opacity-50 whitespace-nowrap"
                >
                  {exporting ? (
                    <>
                      <span className="w-3.5 h-3.5 rounded-full border-2 border-cyan-400/30 border-t-cyan-400 animate-spin flex-shrink-0" />
                      Streaming…
                    </>
                  ) : (
                    <>
                      <Download className="w-3.5 h-3.5" />
                      Download .jsonl
                    </>
                  )}
                </button>
              </div>

              {/* Info row */}
              <div className="mt-4 flex flex-wrap gap-3">
                {(exportFormat === "dpo"
                  ? [
                      { label: "Format", value: "DPO — prompt/chosen/rejected" },
                      { label: "Signal", value: "RLHG — graph usage, no human labels" },
                      { label: "Trainer", value: "TRL DPOTrainer, Unsloth, Axolotl" },
                    ]
                  : [
                      { label: "Format", value: "ChatML / OpenAI" },
                      { label: "Streaming", value: "Yes — O(1) RAM" },
                      { label: "Skips", value: "Empty nodes, SystemAgents" },
                    ]
                ).map((item) => (
                  <div
                    key={item.label}
                    className="flex items-center gap-1.5 text-[10px] text-[#6B7280]"
                  >
                    <span className="text-white/20">·</span>
                    <span>{item.label}:</span>
                    <span className="text-[#A1A1AA]">{item.value}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* Result */}
            {result && (
              <div className="bg-[#12121A] border border-[#2A2A35] rounded-xl overflow-hidden">
                <div className="p-5 flex items-center justify-between border-b border-[#2A2A35]">
                  <div className="flex items-center gap-3">
                    <div className="w-8 h-8 rounded-lg bg-[#10B981]/10 border border-[#10B981]/20 flex items-center justify-center">
                      <Database className="w-4 h-4 text-[#10B981]" />
                    </div>
                    <div>
                      <div className="text-sm font-semibold text-[#E4E4E7]">
                        {exampleCount} examples generated
                      </div>
                      <div className="text-xs text-[#6B7280]">
                        Strategy: {result.strategy ?? config.strategy} · Format:{" "}
                        {result.output_format ?? config.output_format}
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    {result.preview && result.preview.length > 0 && (
                      <button
                        onClick={() => setPreviewOpen((v) => !v)}
                        className="flex items-center gap-1.5 px-3 py-1.5 bg-[#1A1A25] border border-[#2A2A35] rounded-lg text-xs text-[#A1A1AA] hover:text-[#E4E4E7] transition-colors"
                      >
                        <FileText className="w-3.5 h-3.5" />
                        Preview
                        {previewOpen ? (
                          <ChevronUp className="w-3 h-3" />
                        ) : (
                          <ChevronDown className="w-3 h-3" />
                        )}
                      </button>
                    )}
                    <button
                      onClick={handleDownload}
                      className="flex items-center gap-1.5 px-3 py-1.5 bg-[#3B82F6] hover:bg-[#2563EB] text-white rounded-lg text-xs font-medium transition-colors"
                    >
                      <Download className="w-3.5 h-3.5" />
                      Download JSONL
                    </button>
                  </div>
                </div>

                {/* Stats row */}
                <div className="grid grid-cols-3 divide-x divide-[#2A2A35] border-b border-[#2A2A35]">
                  {[
                    { label: "Total Examples", value: exampleCount },
                    { label: "Dataset ID", value: result.dataset_id.slice(0, 8) + "…" },
                    { label: "Strategy", value: result.strategy ?? config.strategy },
                  ].map((s) => (
                    <div key={s.label} className="px-5 py-3">
                      <div className="text-lg font-bold text-[#E4E4E7] font-mono">{s.value}</div>
                      <div className="text-xs text-[#6B7280]">{s.label}</div>
                    </div>
                  ))}
                </div>

                {/* Preview */}
                {previewOpen && result.preview && result.preview.length > 0 && (
                  <div className="p-4">
                    <div className="text-xs text-[#6B7280] mb-2 font-medium">
                      Preview (first 3 examples)
                    </div>
                    <div className="flex flex-col gap-2">
                      {result.preview.slice(0, 3).map((ex, i) => (
                        <pre
                          key={i}
                          className="bg-[#0A0A0F] border border-[#2A2A35] rounded-lg p-3 text-[11px] text-[#A1A1AA] font-mono overflow-x-auto whitespace-pre-wrap"
                        >
                          {JSON.stringify(ex, null, 2)}
                        </pre>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
