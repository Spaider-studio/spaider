import * as Dialog from "@radix-ui/react-dialog";
import { Brain, Cpu, FileText, HardDrive, Info, SlidersHorizontal, X } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { useAppStore, type SettingsTab } from "../../store/useAppStore";
import DocumentsTab from "./DocumentsTab";
import InfoTab from "./InfoTab";
import MemoryTab from "./MemoryTab";
import ModelsTab from "./ModelsTab";
import PreferencesTab from "./PreferencesTab";
import StorageTab from "./StorageTab";

const TABS: { id: SettingsTab; label: string; icon: ReactNode }[] = [
  { id: "models", label: "Modelle", icon: <Cpu className="w-4 h-4" /> },
  { id: "storage", label: "Speicher", icon: <HardDrive className="w-4 h-4" /> },
  { id: "memory", label: "Erinnerungen", icon: <Brain className="w-4 h-4" /> },
  { id: "documents", label: "Dokumente", icon: <FileText className="w-4 h-4" /> },
  { id: "preferences", label: "Einstellungen", icon: <SlidersHorizontal className="w-4 h-4" /> },
  { id: "info", label: "Info", icon: <Info className="w-4 h-4" /> },
];

export default function SettingsDialog() {
  const open = useAppStore((s) => s.settingsOpen);
  const tab = useAppStore((s) => s.settingsTab);
  const openSettings = useAppStore((s) => s.openSettings);
  const closeSettings = useAppStore((s) => s.closeSettings);

  return (
    <Dialog.Root open={open} onOpenChange={(o) => (o ? openSettings() : closeSettings())}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 data-[state=open]:animate-fade-in" />
        <Dialog.Content
          className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-50 w-[min(960px,calc(100vw-2rem))] h-[min(720px,calc(100vh-2rem))] bg-bg-secondary border border-border rounded-xl shadow-2xl flex overflow-hidden data-[state=open]:animate-fade-in focus:outline-none"
          aria-describedby={undefined}
        >
          <nav className="w-48 flex-shrink-0 border-r border-border bg-bg-primary/40 p-3 flex flex-col gap-1" aria-label="Einstellungsbereiche">
            <Dialog.Title className="px-3 py-2 text-xs font-semibold uppercase tracking-wider text-text-secondary">
              Einstellungen
            </Dialog.Title>
            {TABS.map((t) => (
              <button
                key={t.id}
                type="button"
                onClick={() => openSettings(t.id)}
                aria-current={tab === t.id ? "page" : undefined}
                className={cn(
                  "flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-left transition-colors border",
                  tab === t.id
                    ? "bg-white/5 border-white/15 text-text-primary"
                    : "border-transparent text-text-secondary hover:text-text-primary hover:bg-white/5",
                )}
              >
                <span className={tab === t.id ? "text-accent-blue" : ""}>{t.icon}</span>
                {t.label}
              </button>
            ))}
          </nav>
          <div className="flex-1 min-w-0 flex flex-col">
            <div className="flex items-center justify-between px-6 py-3 border-b border-border">
              <h2 className="text-sm font-semibold">{TABS.find((t) => t.id === tab)?.label}</h2>
              <Dialog.Close asChild>
                <button type="button" aria-label="Schließen" className="text-text-secondary hover:text-text-primary transition-colors">
                  <X className="w-4 h-4" />
                </button>
              </Dialog.Close>
            </div>
            <div className="flex-1 min-h-0 overflow-y-auto px-6 py-5">
              {tab === "models" && <ModelsTab />}
              {tab === "storage" && <StorageTab />}
              {tab === "memory" && <MemoryTab />}
              {tab === "documents" && <DocumentsTab />}
              {tab === "preferences" && <PreferencesTab />}
              {tab === "info" && <InfoTab />}
            </div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
