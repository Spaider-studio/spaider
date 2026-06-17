"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Network,
  Users,
  Database,
  Settings,
  ChevronLeft,
  ChevronRight,
  Wifi,
  WifiOff,
  Globe2,
  History,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { API_BASE } from "@/lib/constants";

const NAV_ITEMS = [
  { href: "/studio", label: "Neural Studio", icon: Network, color: "#3B82F6" },
  { href: "/multiverse", label: "Multiverse", icon: Globe2, color: "#06B6D4" },
  { href: "/agents", label: "Agents", icon: Users, color: "#8B5CF6" },
  { href: "/synthesizer", label: "Synthesizer", icon: Database, color: "#10B981" },
  { href: "/audit", label: "Audit Log", icon: History, color: "#F59E0B" },
  { href: "/settings", label: "Settings", icon: Settings, color: "#A1A1AA" },
];

export default function Sidebar() {
  const [expanded, setExpanded] = useState(true);
  const [connected, setConnected] = useState<boolean | null>(null);
  const pathname = usePathname();

  useEffect(() => {
    async function checkHealth() {
      try {
        const base = API_BASE.replace("/api/v1", "");
        const res = await fetch(`${base}/health`, {
          signal: AbortSignal.timeout(3000),
        });
        setConnected(res.ok);
      } catch {
        setConnected(false);
      }
    }
    checkHealth();
    const interval = setInterval(checkHealth, 30000);
    return () => clearInterval(interval);
  }, []);

  return (
    <aside
      className={cn(
        "flex flex-col bg-black/50 backdrop-blur-md border-r border-white/10 h-full transition-all duration-300 relative flex-shrink-0",
        expanded ? "w-56" : "w-16"
      )}
    >
      {/* Logo */}
      <div
        className={cn(
          "flex items-center gap-3 px-4 py-5 border-b border-white/10",
          !expanded && "justify-center px-0"
        )}
      >
        {/* Spider web SVG */}
        <svg
          viewBox="0 0 24 24"
          className="w-7 h-7 flex-shrink-0 text-[#3B82F6]"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
        >
          <circle cx="12" cy="12" r="10" strokeOpacity="0.2" />
          <circle cx="12" cy="12" r="6" strokeOpacity="0.35" />
          <circle cx="12" cy="12" r="2" fill="currentColor" stroke="none" />
          <line x1="12" y1="2" x2="12" y2="22" strokeOpacity="0.5" />
          <line x1="2" y1="12" x2="22" y2="12" strokeOpacity="0.5" />
          <line x1="4.9" y1="4.9" x2="19.1" y2="19.1" strokeOpacity="0.5" />
          <line x1="19.1" y1="4.9" x2="4.9" y2="19.1" strokeOpacity="0.5" />
        </svg>
        {expanded && (
          <div>
            <div className="text-sm font-bold text-[#E4E4E7] tracking-tight leading-none">
              SpAIder
            </div>
            <div className="text-[10px] text-[#A1A1AA] mt-0.5">Neural Studio</div>
          </div>
        )}
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-2 py-3 flex flex-col gap-1">
        {NAV_ITEMS.map(({ href, label, icon: Icon, color }) => {
          const active =
            pathname === href || (href !== "/" && pathname.startsWith(href));
          return (
            <Link
              key={href}
              href={href}
              title={!expanded ? label : undefined}
              className={cn(
                "flex items-center rounded-lg text-sm font-medium transition-all relative group",
                expanded ? "gap-3 px-3 py-2.5" : "justify-center p-2.5",
                active
                  ? "bg-white/5 border border-white/15"
                  : "text-[#A1A1AA] hover:text-[#E4E4E7] hover:bg-white/5 border border-transparent"
              )}
              style={active ? { color } : undefined}
            >
              {active && (
                <span
                  className="absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-5 rounded-full"
                  style={{ background: color }}
                />
              )}
              <Icon
                className={cn("flex-shrink-0", expanded ? "w-4 h-4" : "w-5 h-5")}
              />
              {expanded && <span>{label}</span>}
              {/* Tooltip for collapsed state */}
              {!expanded && (
                <div className="absolute left-full ml-2.5 px-2 py-1 bg-black/80 border border-white/10 rounded-lg text-xs text-[#E4E4E7] whitespace-nowrap opacity-0 group-hover:opacity-100 pointer-events-none transition-opacity z-50 shadow-xl">
                  {label}
                </div>
              )}
            </Link>
          );
        })}
      </nav>

      {/* Connection status */}
      <div
        className={cn(
          "px-3 py-3 border-t border-white/10 flex items-center gap-2",
          !expanded && "justify-center"
        )}
        title={
          connected === null
            ? "Checking connection..."
            : connected
            ? "API Connected"
            : "API Offline"
        }
      >
        <div className="relative flex-shrink-0">
          <div
            className={cn(
              "w-2 h-2 rounded-full",
              connected === null
                ? "bg-[#6B7280]"
                : connected
                ? "bg-[#10B981]"
                : "bg-[#EF4444]"
            )}
          />
          {connected === true && (
            <div className="absolute inset-0 w-2 h-2 rounded-full bg-[#10B981] animate-ping opacity-60" />
          )}
        </div>
        {expanded && (
          <>
            <span className="text-xs text-[#A1A1AA] flex-1">
              {connected === null
                ? "Checking..."
                : connected
                ? "API Connected"
                : "API Offline"}
            </span>
            {connected ? (
              <Wifi className="w-3.5 h-3.5 text-[#10B981]" />
            ) : (
              <WifiOff className="w-3.5 h-3.5 text-[#EF4444]" />
            )}
          </>
        )}
      </div>

      {/* Collapse toggle */}
      <button
        onClick={() => setExpanded((v) => !v)}
        className="absolute -right-3 top-1/2 -translate-y-1/2 w-6 h-6 rounded-full bg-black/60 border border-white/15 flex items-center justify-center text-[#A1A1AA] hover:text-[#E4E4E7] hover:border-[#3A3A45] transition-all z-10 shadow-md"
      >
        {expanded ? (
          <ChevronLeft className="w-3 h-3" />
        ) : (
          <ChevronRight className="w-3 h-3" />
        )}
      </button>
    </aside>
  );
}
