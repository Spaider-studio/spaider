import { useEffect, useRef, useState } from "react";
import LoadingSpinner from "@/components/shared/LoadingSpinner";
import { useAppStore } from "../../store/useAppStore";
import { Field, SectionTitle, Select, Toggle } from "../ui";

const CONTEXT_OPTIONS = [4096, 8192, 16384, 32768].map((n) => ({ value: String(n), label: `${n / 1024}k Token (${n.toLocaleString("de-DE")})` }));

export default function PreferencesTab() {
  const settings = useAppStore((s) => s.settings);
  const setup = useAppStore((s) => s.setup);
  const loadSettings = useAppStore((s) => s.loadSettings);
  const updateSettings = useAppStore((s) => s.updateSettings);
  const [temperature, setTemperature] = useState(settings?.temperature ?? 0.7);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!settings) void loadSettings();
  }, [settings, loadSettings]);

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
