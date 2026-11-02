import { useEffect, useRef, useState } from "react";
import LoadingSpinner from "@/components/shared/LoadingSpinner";
import { cn } from "@/lib/utils";
import type { GpuMode, PerformanceMode } from "../../api/types";
import { GPU_MODE_OPTIONS, PERFORMANCE_MODE_OPTIONS } from "../../lib/labels";
import { useAppStore } from "../../store/useAppStore";
import { Field, SectionTitle, Select, Toggle } from "../ui";

const CONTEXT_OPTIONS = [4096, 8192, 16384, 32768].map((n) => ({ value: String(n), label: `${n / 1024}k Token (${n.toLocaleString("de-DE")})` }));

export default function PreferencesTab() {
  const settings = useAppStore((s) => s.settings);
  const setup = useAppStore((s) => s.setup);
  const hardware = useAppStore((s) => s.hardware);
  const loadSettings = useAppStore((s) => s.loadSettings);
  const loadHardware = useAppStore((s) => s.loadHardware);
  const updateSettings = useAppStore((s) => s.updateSettings);
  const [temperature, setTemperature] = useState(settings?.temperature ?? 0.7);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!settings) void loadSettings();
  }, [settings, loadSettings]);

  useEffect(() => {
    if (!hardware) void loadHardware();
  }, [hardware, loadHardware]);

  useEffect(() => {
    if (settings) setTemperature(settings.temperature);
  }, [settings]);

  if (!settings) {
    return (
      <div className="flex justify-center py-10">
        <LoadingSpinner />
      </div>
    );
  }

  const onTemperature = (value: number) => {
    setTemperature(value);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => void updateSettings({ temperature: value }), 400);
  };

  const contextOptions = CONTEXT_OPTIONS.some((o) => o.value === String(settings.context_tokens))
    ? CONTEXT_OPTIONS
    : [...CONTEXT_OPTIONS, { value: String(settings.context_tokens), label: `${settings.context_tokens.toLocaleString("de-DE")} Token` }];

  const performanceMode: PerformanceMode = settings.performance_mode ?? "auto";
  const gpuMode: GpuMode = settings.gpu_mode ?? "auto";
  const gpus = hardware?.gpus ?? [];
  const usableGpu = gpus.find((g) => g.usable) ?? gpus[0];

  return (
    <div className="flex flex-col gap-8 max-w-2xl">
      <section>
        <SectionTitle>Antworten</SectionTitle>
        <div className="panel px-4 divide-y divide-border">
          <Toggle
            label="Nachdenken (Thinking)"
            description="Das Modell überlegt vor der Antwort. Antworten werden gründlicher, aber langsamer."
            checked={settings.thinking_enabled}
            onChange={(v) => void updateSettings({ thinking_enabled: v })}
          />
          <Field label="Kontextlänge" description="Wie viel Gesprächsverlauf und Gedächtnis das Modell gleichzeitig sieht. Größer = mehr Arbeitsspeicher.">
            <Select
              value={String(settings.context_tokens)}
              onChange={(v) => void updateSettings({ context_tokens: Number(v) })}
              options={contextOptions}
              ariaLabel="Kontextlänge"
            />
          </Field>
          <div className="py-3">
            <div className="flex items-center justify-between gap-4">
              <div>
                <div className="text-sm font-medium">Kreativität (Temperatur)</div>
                <div className="text-xs text-text-secondary mt-0.5">Niedrig = sachlich und wiederholbar, hoch = abwechslungsreicher.</div>
              </div>
              <span className="text-sm tabular-nums w-10 text-right">{temperature.toFixed(2)}</span>
            </div>
            <input
              type="range"
              min={0}
              max={2}
              step={0.05}
              value={temperature}
              onChange={(e) => onTemperature(Number(e.target.value))}
              aria-label="Temperatur"
              className="range-input mt-3"
            />
          </div>
        </div>
      </section>

      <section>
        <SectionTitle>Leistung</SectionTitle>
        <div className="panel px-4 divide-y divide-border">
          <div className="py-3" role="radiogroup" aria-label="Leistungsmodus" data-testid="performance-mode">
            <div className="text-sm font-medium">Leistungsmodus</div>
            <div className="text-xs text-text-secondary mt-0.5 mb-3">Wie viel Rechenleistung SpAIder für Antworten und Hintergrundarbeit nutzt.</div>
            <div className="grid gap-2 sm:grid-cols-3">
              {PERFORMANCE_MODE_OPTIONS.map((o) => (
                <button
                  key={o.value}
                  type="button"
                  role="radio"
                  aria-checked={performanceMode === o.value}
                  onClick={() => void updateSettings({ performance_mode: o.value })}
                  className={cn(
                    "text-left rounded-lg border px-3 py-2.5 transition-colors",
                    performanceMode === o.value
                      ? "border-accent-blue bg-accent-blue/10"
                      : "border-border bg-bg-primary/40 hover:border-[#3A3A45]",
                  )}
                >
                  <div className="text-sm font-medium text-text-primary">{o.label}</div>
                  <div className="text-[11px] text-text-secondary mt-1 leading-relaxed">{o.description}</div>
                </button>
              ))}
            </div>
          </div>
          {gpus.length > 0 && (
            <Field
              label="Grafikkarte"
              description={
                usableGpu
                  ? `${usableGpu.name}${usableGpu.usable ? "" : " (nicht nutzbar)"} · Automatisch nutzt die Grafikkarte, wenn sie erkannt und nutzbar ist.`
                  : "Automatisch nutzt die Grafikkarte, wenn sie erkannt und nutzbar ist."
              }
            >
              <Select value={gpuMode} onChange={(v) => void updateSettings({ gpu_mode: v as GpuMode })} options={GPU_MODE_OPTIONS} ariaLabel="Grafikkarte" />
            </Field>
          )}
        </div>
      </section>

      <section>
        <SectionTitle>Gedächtnis</SectionTitle>
        <div className="panel px-4 divide-y divide-border">
          <Toggle
            label="Fakten automatisch merken"
            description="Nach jeder Antwort werden wichtige Fakten aus dem Gespräch ins Gedächtnis übernommen."
            checked={settings.memory_auto_extract}
            onChange={(v) => void updateSettings({ memory_auto_extract: v })}
          />
          <Toggle
            label="Semantische Suche (Embedding)"
            description={
              setup?.active_embedding_model
                ? `Aktives Embedding-Modell: ${setup.active_embedding_model.name}`
                : "Kein Embedding-Modell aktiv – die Suche nutzt nur den Volltextindex."
            }
            checked={settings.embedding_enabled}
            onChange={(v) => void updateSettings({ embedding_enabled: v })}
          />
        </div>
      </section>
    </div>
  );
}
