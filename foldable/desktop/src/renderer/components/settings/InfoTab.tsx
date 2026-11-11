import { useEffect, useState } from "react";
import type { Health, SystemInfo } from "../../api/types";
import { useAppStore } from "../../store/useAppStore";
import Logo from "../Logo";
import { KeyValue, SectionTitle } from "../ui";

export default function InfoTab() {
  const client = useAppStore((s) => s.client);
  const appInfo = useAppStore((s) => s.appInfo);
  const backendInfo = useAppStore((s) => s.backendInfo);
  const [health, setHealth] = useState<Health | null>(null);
  const [system, setSystem] = useState<SystemInfo | null>(null);

  useEffect(() => {
    if (!client) return;
    client.health().then(setHealth).catch(() => setHealth(null));
    client.systemInfo().then(setSystem).catch(() => setSystem(null));
  }, [client]);

  return (
    <div className="flex flex-col gap-8 max-w-2xl">
      <div className="flex items-center gap-3">
        <Logo className="w-9 h-9" />
        <div>
          <div className="text-base font-semibold">SpAIder Foldable</div>
          <div className="text-xs text-text-secondary">Portable, vollständig lokale Chat-App mit Gedächtnis</div>
        </div>
      </div>

      <section>
        <SectionTitle>Versionen</SectionTitle>
        <div className="panel px-4">
          <KeyValue label="Desktop-Shell" value={appInfo?.version ?? "–"} />
          <KeyValue label="Kern" value={health?.version ?? "–"} />
          <KeyValue label="Laufzeit" value={system ? `${system.runtime.name} ${system.runtime.build} (${system.runtime.variant})` : "–"} />
          <KeyValue label="Plattform" value={appInfo?.platform ?? "–"} />
        </div>
      </section>

      <section>
        <SectionTitle>Pfade</SectionTitle>
        <div className="panel px-4">
          <KeyValue label="App-Verzeichnis" value={appInfo?.appRoot ?? "–"} mono />
          <KeyValue label="Datenverzeichnis" value={appInfo?.dataDir ?? "–"} mono />
          <KeyValue label="Kern-Adresse" value={backendInfo?.baseUrl ?? "–"} mono />
        </div>
      </section>

      <section>
        <SectionTitle>Lizenzen</SectionTitle>
        <div className="panel p-4 text-xs text-text-secondary leading-relaxed flex flex-col gap-2">
          <p>
            SpAIder Foldable (Kern und Oberfläche) steht unter der GNU Affero General Public License 3.0 (AGPL-3.0-only).
          </p>
          <p>Die Inferenzlaufzeit llama.cpp steht unter der MIT-Lizenz.</p>
          <p>
            Sprachmodelle unterliegen den Lizenzen ihrer Hersteller; die jeweilige Lizenz wird im Modellkatalog angezeigt und
            beim Download akzeptiert.
          </p>
          <p>Alle Daten (Modelle, Gespräche, Gedächtnis, Dokumente) bleiben auf diesem Datenträger. Es findet keine Telemetrie statt.</p>
        </div>
      </section>
    </div>
  );
}
