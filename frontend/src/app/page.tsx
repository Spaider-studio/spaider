"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  Network,
  Brain,
  Users,
  Database,
  ArrowRight,
  Zap,
  Activity,
  TrendingUp,
  Sparkles,
} from "lucide-react";
import { getGraphStats, getAgents } from "@/lib/api";
import type { GraphStats, Agent } from "@/lib/types";
import { formatDate, formatNumber } from "@/lib/utils";

interface StatCard {
  label: string;
  value: string | number;
  icon: React.ReactNode;
  color: string;
  href: string;
}

export default function HomePage() {
  const [stats, setStats] = useState<GraphStats | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function load() {
      try {
        const [s, a] = await Promise.allSettled([
          getGraphStats(),
          getAgents(),
        ]);
        if (s.status === "fulfilled") setStats(s.value);
        if (a.status === "fulfilled") setAgents(a.value);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  const statCards: StatCard[] = [
    {
      label: "Total Nodes",
      value: stats ? formatNumber(stats.node_count) : "—",
      icon: <Network className="w-5 h-5" />,
      color: "text-[#3B82F6]",
      href: "/studio",
    },
    {
      label: "Total Edges",
      value: stats ? formatNumber(stats.edge_count) : "—",
      icon: <Activity className="w-5 h-5" />,
      color: "text-[#10B981]",
      href: "/studio",
    },
    {
      label: "Active Agents",
      value: loading ? "—" : agents.length,
      icon: <Users className="w-5 h-5" />,
      color: "text-[#8B5CF6]",
      href: "/agents",
    },
    {
      label: "Graph Density",
      value: stats ? `${(stats.density * 100).toFixed(2)}%` : "—",
      icon: <TrendingUp className="w-5 h-5" />,
      color: "text-[#F59E0B]",
      href: "/studio",
    },
  ];

  return (
    <div className="min-h-screen bg-[#0A0A0F] grid-bg relative overflow-x-hidden">
      {/* Ambient glow */}
      <div className="absolute top-0 left-1/4 w-[600px] h-[400px] bg-[#3B82F6]/5 rounded-full blur-[120px] pointer-events-none" />
      <div className="absolute top-1/4 right-0 w-[400px] h-[300px] bg-[#8B5CF6]/5 rounded-full blur-[100px] pointer-events-none" />

      <div className="relative z-10 max-w-7xl mx-auto px-6 py-16">
        {/* Header */}
        <nav className="flex items-center justify-between mb-20">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-[#12121A] border border-[#2A2A35] flex items-center justify-center">
              <svg
                viewBox="0 0 24 24"
                className="w-6 h-6 text-[#3B82F6]"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
              >
                <circle cx="12" cy="12" r="2" fill="currentColor" />
                <path d="M12 2 L12 6 M12 18 L12 22 M2 12 L6 12 M18 12 L22 12" />
                <path d="M5.636 5.636 L8.464 8.464 M15.536 15.536 L18.364 18.364 M18.364 5.636 L15.536 8.464 M8.464 15.536 L5.636 18.364" />
                <circle cx="12" cy="2" r="1.5" fill="currentColor" />
                <circle cx="12" cy="22" r="1.5" fill="currentColor" />
                <circle cx="2" cy="12" r="1.5" fill="currentColor" />
                <circle cx="22" cy="12" r="1.5" fill="currentColor" />
                <circle cx="5.636" cy="5.636" r="1.2" fill="currentColor" />
                <circle cx="18.364" cy="5.636" r="1.2" fill="currentColor" />
                <circle cx="5.636" cy="18.364" r="1.2" fill="currentColor" />
                <circle cx="18.364" cy="18.364" r="1.2" fill="currentColor" />
              </svg>
            </div>
            <span className="text-lg font-semibold text-[#E4E4E7] tracking-wide">
              SpAIder
            </span>
          </div>
          <div className="flex items-center gap-4">
            <Link
              href="/studio"
              className="text-sm text-[#A1A1AA] hover:text-[#E4E4E7] transition-colors"
            >
              Studio
            </Link>
            <Link
              href="/agents"
              className="text-sm text-[#A1A1AA] hover:text-[#E4E4E7] transition-colors"
            >
              Agents
            </Link>
            <Link
              href="/synthesizer"
              className="text-sm text-[#A1A1AA] hover:text-[#E4E4E7] transition-colors"
            >
              Synthesizer
            </Link>
            <Link
              href="/studio"
              className="flex items-center gap-2 bg-[#3B82F6] hover:bg-[#2563EB] text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors"
            >
              Open Studio
              <ArrowRight className="w-4 h-4" />
            </Link>
          </div>
        </nav>

        {/* Hero */}
        <div className="text-center mb-20">
          <div className="inline-flex items-center gap-2 bg-[#12121A] border border-[#2A2A35] rounded-full px-4 py-1.5 text-sm text-[#A1A1AA] mb-6">
            <Sparkles className="w-3.5 h-3.5 text-[#3B82F6]" />
            Memory Infrastructure for AI Agents
          </div>
          <h1 className="text-6xl font-bold text-[#E4E4E7] mb-6 leading-tight">
            SpAIder{" "}
            <span className="bg-gradient-to-r from-[#3B82F6] to-[#8B5CF6] bg-clip-text text-transparent">
              Neural
            </span>{" "}
            Studio
          </h1>
          <p className="text-xl text-[#A1A1AA] max-w-2xl mx-auto mb-10 leading-relaxed">
            Build, visualize, and query persistent knowledge graphs for your AI
            agents. Extract structured knowledge from unstructured text and
            synthesize training datasets.
          </p>
          <div className="flex items-center justify-center gap-4">
            <Link
              href="/studio"
              className="flex items-center gap-2 bg-[#3B82F6] hover:bg-[#2563EB] text-white font-semibold px-6 py-3 rounded-xl transition-all hover:shadow-[0_0_30px_rgba(59,130,246,0.4)]"
            >
              <Network className="w-5 h-5" />
              Open Neural Studio
            </Link>
            <Link
              href="/synthesizer"
              className="flex items-center gap-2 bg-[#12121A] hover:bg-[#1A1A25] border border-[#2A2A35] text-[#E4E4E7] font-semibold px-6 py-3 rounded-xl transition-colors"
            >
              <Database className="w-5 h-5 text-[#8B5CF6]" />
              Generate Dataset
            </Link>
          </div>
        </div>

        {/* Stats */}
        <div className="grid grid-cols-4 gap-4 mb-16">
          {statCards.map((card) => (
            <Link
              key={card.label}
              href={card.href}
              className="bg-[#12121A] border border-[#2A2A35] rounded-xl p-6 hover:border-[#3A3A45] transition-all group"
            >
              <div
                className={`${card.color} mb-3 group-hover:scale-110 transition-transform inline-block`}
              >
                {card.icon}
              </div>
              <div className="text-3xl font-bold text-[#E4E4E7] mb-1">
                {card.value}
              </div>
              <div className="text-sm text-[#A1A1AA]">{card.label}</div>
            </Link>
          ))}
        </div>

        {/* Feature Grid */}
        <div className="grid grid-cols-3 gap-6 mb-16">
          <FeatureCard
            icon={<Network className="w-6 h-6 text-[#3B82F6]" />}
            title="3D Knowledge Graph"
            description="Visualize entity relationships in an interactive 3D force-directed graph with real-time updates."
            href="/studio"
            color="blue"
          />
          <FeatureCard
            icon={<Brain className="w-6 h-6 text-[#8B5CF6]" />}
            title="Natural Language Queries"
            description="Ask questions in plain English and get answers backed by your knowledge graph with highlighted context."
            href="/studio"
            color="purple"
          />
          <FeatureCard
            icon={<Zap className="w-6 h-6 text-[#10B981]" />}
            title="Agent Swarm Memory"
            description="Connect multiple AI agents with configurable permissions and shared knowledge scopes."
            href="/agents"
            color="green"
          />
          <FeatureCard
            icon={<Database className="w-6 h-6 text-[#F59E0B]" />}
            title="Dataset Synthesis"
            description="Generate JSONL training datasets from your knowledge graph using multiple synthesis strategies."
            href="/synthesizer"
            color="yellow"
          />
          <FeatureCard
            icon={<Users className="w-6 h-6 text-[#EC4899]" />}
            title="Multi-Agent Support"
            description="Manage multiple independent agents with isolated knowledge graphs and API key authentication."
            href="/agents"
            color="pink"
          />
          <FeatureCard
            icon={<Activity className="w-6 h-6 text-[#06B6D4]" />}
            title="GDPR Compliance"
            description="Right-to-erasure support with targeted node deletion and cascading edge cleanup."
            href="/settings"
            color="cyan"
          />
        </div>

        {/* Recent agents */}
        {agents.length > 0 && (
          <div className="bg-[#12121A] border border-[#2A2A35] rounded-xl p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-base font-semibold text-[#E4E4E7]">
                Recent Agents
              </h2>
              <Link
                href="/agents"
                className="text-sm text-[#3B82F6] hover:text-[#60A5FA] transition-colors flex items-center gap-1"
              >
                View all <ArrowRight className="w-3.5 h-3.5" />
              </Link>
            </div>
            <div className="flex flex-col gap-2">
              {agents.slice(0, 5).map((agent) => (
                <div
                  key={agent.id}
                  className="flex items-center justify-between py-2 border-b border-[#2A2A35] last:border-0"
                >
                  <div className="flex items-center gap-3">
                    <div className="w-8 h-8 rounded-lg bg-[#1A1A25] border border-[#2A2A35] flex items-center justify-center">
                      <Users className="w-4 h-4 text-[#8B5CF6]" />
                    </div>
                    <div>
                      <div className="text-sm font-medium text-[#E4E4E7]">
                        {agent.name}
                      </div>
                      {agent.description && (
                        <div className="text-xs text-[#A1A1AA]">
                          {agent.description}
                        </div>
                      )}
                    </div>
                  </div>
                  <div className="text-xs text-[#A1A1AA]">
                    {formatDate(agent.created_at)}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function FeatureCard({
  icon,
  title,
  description,
  href,
  color,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
  href: string;
  color: "blue" | "purple" | "green" | "yellow" | "pink" | "cyan";
}) {
  const glowColors = {
    blue: "hover:border-[#3B82F6]/40 hover:shadow-[0_0_20px_rgba(59,130,246,0.1)]",
    purple:
      "hover:border-[#8B5CF6]/40 hover:shadow-[0_0_20px_rgba(139,92,246,0.1)]",
    green:
      "hover:border-[#10B981]/40 hover:shadow-[0_0_20px_rgba(16,185,129,0.1)]",
    yellow:
      "hover:border-[#F59E0B]/40 hover:shadow-[0_0_20px_rgba(245,158,11,0.1)]",
    pink: "hover:border-[#EC4899]/40 hover:shadow-[0_0_20px_rgba(236,72,153,0.1)]",
    cyan: "hover:border-[#06B6D4]/40 hover:shadow-[0_0_20px_rgba(6,182,212,0.1)]",
  };

  return (
    <Link
      href={href}
      className={`bg-[#12121A] border border-[#2A2A35] rounded-xl p-6 transition-all group ${glowColors[color]}`}
    >
      <div className="mb-4 group-hover:scale-110 transition-transform inline-block">
        {icon}
      </div>
      <h3 className="text-base font-semibold text-[#E4E4E7] mb-2">{title}</h3>
      <p className="text-sm text-[#A1A1AA] leading-relaxed">{description}</p>
      <div className="mt-4 flex items-center gap-1 text-xs text-[#A1A1AA] group-hover:text-[#E4E4E7] transition-colors">
        Learn more <ArrowRight className="w-3 h-3" />
      </div>
    </Link>
  );
}
