# SpAIder Foldable: Performance-Bericht (0.1.0 → 0.2.0)

Stand: 2026-11-02. Alle Zahlen in diesem Bericht sind Messwerte von genau
einem Rechner; Schätzungen sind als solche markiert. Abschnitt „Ergebnisse“
wird aus den JSON-Berichten des Messskripts gefüllt
(`foldable/build/measurements/*.json`, erzeugt mit `foldable/build/measure.py`).

## Hardware und Software

| | |
|---|---|
| Rechner | Windows 11 Enterprise 10.0.26200 x64, Intel Core Ultra 7 268V (8 physische Kerne, 8 Threads), 32 GB RAM, Intel Arc 140V (Vulkan, 17,6 GB nutzbar laut Runtime, Treiber 32.0.101.8860), NTFS-SSD |
| Betrieb | Netzbetrieb, Standard-Energieplan, keine weiteren Lasten außer den jeweils genannten Parallelläufen |
| Runtime | llama.cpp b12032, CPU-Variante (Baseline und Nachher-Messung CPU), Vulkan-Variante (GPU-Messung) |
| Referenzmodell | `Qwen/Qwen3-4B-GGUF`, Datei `Qwen3-4B-Q4_K_M.gguf`, 2 497 280 256 Bytes, SHA-256 `7485fe6f…fdf5` (das Modell des Nutzerlaufs; Kopie aus dem Testordner) |
| Kontext | 8192 Tokens, Antwortbudget 120 Tokens (Messung) bzw. 1024 (App-Standard), Temperatur 0 (direkt) / 0,7 (App) |
| Smoke-Modell | `Qwen/Qwen3-0.6B-GGUF` Q8_0 für GPU/CPU-Vergleich und Echtlauf-Tests |

## Messmethode

`foldable/build/measure.py` misst mit synthetischen Prompts (keine privaten
Inhalte) und identischen Einstellungen:

1. **Direkt**: llama-server allein (Laden, Zeit bis erstes sichtbares Token
   aus Client-Sicht, Token/s und Prompt-Token/s aus den Server-Timings, CPU-Zeit
   und Spitzen-RSS des Serverprozesses), kalt (erster Aufruf je Promptart) und
   warm (3 Wiederholungen), kurze (≈20 Tokens) und lange (≈500 Tokens) Eingabe.
2. **App-Pfad**: Kernprozess + API, gleiche Prompts; aus den SSE-Ereignissen:
   Gedächtnissuche fertig, erstes Token, fertig; Server-Nutzung aus der
   Nachricht; CPU-Zeit von Kern und Server, RSS-Spitzen; mit leerem und mit
   gefülltem Gedächtnis (drei gespeicherte Fakten inkl. Korrektur), dazu eine
   Abruf-Frage.
3. **Stopp**: lange Antwort nach 5 Token gestoppt; Zeit bis zum `done`-Ereignis
   und bis die CPU-Zeit des Servers nicht mehr steigt.
4. **Lebenszyklus**: Kernstart, Modell bereit, Shutdown, keine Restprozesse.
5. **Nachverarbeitung**: Wartezeit bis die Gedächtnis-Warteschlange leer ist
   (Drain) nach kalten Antworten und nach den drei Fakten.

Erste sichtbare Antwort = erstes `token`-Ereignis mit Text (keine Heartbeats,
keine Status-Ereignisse, kein Denktext). Median und Min/Max je Gruppe; p95 nur
bei ≥ 20 Wiederholungen (hier nicht erreicht, daher keine p95-Angaben).

Nicht messbar auf diesem Rechner: GPU-Beschleunigung anderer Hersteller,
Energieverbrauch (keine Leistungsmessung verfügbar), Darstellungszeit im
Renderer (nur über den Dev-Helfer `window.__spaiderPerf` im UI-Mock geprüft).

## Zielwerte (Entwicklungsziele, keine Zusagen)

| Ziel | Wert |
|---|---|
| Reaktion von Eingaben/Schaltflächen | ≤ ~100 ms |
| Erstes sichtbares Token, kurze Frage, Modell geladen | 1–2 s |
| Generierung | ≥ 20–30 Token/s |
| Sichtbare Stopp-Reaktion | ≤ 500 ms (Beendigung der Modellarbeit separat) |
| Leerlauf | keine anhaltende Hintergrundlast ohne Aufgaben |

## Baseline (0.1.0, Commit acc3ce1, CPU)

*(wird nach Abschluss des Messlaufs eingetragen; Quelle `baseline-qwen3-4b-cpu.md`)*

## Änderungen

1. Chat hat Vorrang: Hintergrund-Extraktion wird für eine Antwort unterbrochen
   (Item zurück in die Warteschlange), Cooldown und Duty-Cycle je Modus.
2. Extraktionsbudget 1500 statt 4000 Tokens; Server-Vorgabe „Thinking aus“
   für Hintergrundaufrufe (Qwen3-Vorlagen denken sonst vor dem JSON).
3. Cache-stabiler Prompt-Aufbau + `cache_prompt`/`--cache-reuse 256`.
4. Sofort sichtbare Rohfakten (keine Wartezeit auf die Nachverarbeitung).
5. GPU-Backend (Vulkan) automatisch, Threads = physische Kerne, Batch-Threads
   = logische Kerne, Leise-Modus mit halber Threadzahl und niedriger Priorität.
6. Aktivitätsereignisse (gebündelt, max. alle 250 ms Fortschritt).

## Ergebnisse nach den Änderungen (0.2.0)

*(wird eingetragen: CPU-Nachher-Messung, Vulkan-Messung, Stopp-Latenz,
Drain-Zeiten; gleiche Prompts, gleiches Modell, gleiche Einstellungen)*

## Verbleibende Engpässe und nicht erreichte Zielwerte

*(wird eingetragen)*
