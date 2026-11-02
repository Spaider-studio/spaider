/**
 * One-line hardware summary from GET /api/system/hardware (v1.1). Falls back
 * to GET /api/system/info for a v1 core.
 */
import { Cpu } from "lucide-react";
import type { HardwareProfile, SystemInfo } from "../../api/types";
import { formatBytes } from "../../lib/format";
import { backendLabel } from "../../lib/labels";

export function hardwareSummaryParts(hw: HardwareProfile): string[] {
  const parts: string[] = [];
  const cores = hw.cpu.cores_physical > 0 ? ` (${hw.cpu.cores_physical} Kerne, ${hw.cpu.threads} Threads)` : "";
  parts.push(`CPU: ${hw.cpu.name}${cores}`);
  parts.push(`RAM: ${formatBytes(hw.ram_total_bytes, 0)} (${formatBytes(hw.ram_available_bytes)} frei)`);
  if (hw.gpus.length === 0) parts.push("GPU: keine erkannt");
  for (const gpu of hw.gpus) {
    const vram = gpu.vram_total_bytes > 0 ? `, ${formatBytes(gpu.vram_total_bytes, 0)} VRAM` : "";
    parts.push(`GPU: ${gpu.name} (${backendLabel(gpu.backend)}${vram}${gpu.usable ? "" : ", nicht nutzbar"})`);
  }
  parts.push(`Frei auf dem Datenträger: ${formatBytes(hw.disk_free_bytes)}`);
  parts.push(`Aktive Laufzeit: ${backendLabel(hw.active_backend)}`);
  if (hw.power.on_battery) parts.push(hw.power.power_saver ? "Akku, Energiesparmodus" : "Akkubetrieb");
  return parts;
}

export default function HardwareSummary({ hardware, fallback }: { hardware: HardwareProfile | null; fallback?: SystemInfo | null }) {
  let parts: string[];
  if (hardware) parts = hardwareSummaryParts(hardware);
  else if (fallback) {
    parts = [
      `CPU: ${fallback.cpu} (${fallback.threads} Threads)`,
      `RAM: ${formatBytes(fallback.ram_total_bytes, 0)}`,
      `Frei auf dem Datenträger: ${formatBytes(fallback.disk_free_bytes)}`,
      `Laufzeit: ${fallback.runtime.variant}`,
    ];
  } else return null;
  return (
    <div className="panel px-4 py-3 flex items-start gap-2.5 text-xs text-text-secondary" data-testid="hardware-summary">
      <Cpu className="w-3.5 h-3.5 mt-0.5 flex-shrink-0 text-accent-cyan" />
      <div className="flex flex-wrap gap-x-4 gap-y-1">
        {parts.map((p) => (
          <span key={p}>{p}</span>
        ))}
      </div>
    </div>
  );
}
