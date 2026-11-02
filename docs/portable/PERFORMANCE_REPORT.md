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

Quelle: `foldable/build/measurements/baseline-qwen3-4b-cpu-partial.json`.
Der Messlauf (Worktree `repo-baseline` auf acc3ce1, `measure.py`, 3 Wiederholungen)
wurde nach 78 Minuten abgebrochen: In der Warm-Phase wartete jede Antwort rund
10 Minuten hinter den Extraktionsanfragen des Hintergrunds (5 Abschnitte ×
120 s Timeout × 3 Versuche im einzigen Server-Slot; 140 protokollierte
LLM-Timeouts), und keine der fünf Wartephasen „Warteschlange leer“ endete
innerhalb von 900 s. Die Zahlen stammen deshalb aus den Nachrichten-Datensätzen
des Messlaufs (serverseitige Zeit bis erstes Token ab Anfragebeginn, Token/s
aus den Server-Timings) und aus dem llama-server-Log der Direktphase; die
clientseitigen Marken des Messskripts gingen mit dem Abbruch verloren.

**Direkt (llama-server allein, Qwen3-4B Q4_K_M, 8 Threads, ohne Parallellast):**

| Größe | Wert |
|---|---|
| Generierung | Median **10,9 Token/s** (31 Stichproben, 3,9–17,7; Kurzantworten bis 17, lange Kontexte 6–9) |
| Prompt-Verarbeitung (volle Prompts) | Median **72,8 Token/s**; 468 Tokens ≈ 5,0–6,7 s, 1 400 Tokens ≈ 10,9–18,7 s |
| Prompt-Cache | 23 von 31 Stichproben mit Cache-Treffer (nur 1 Token neu zu verarbeiten) |

Diese Direktwerte sind unabhängig vom App-Code (gleiche Binärdatei, gleiches
Modell) und gelten daher auch als Direkt-Referenz für die Nachher-Messung.

**App-Pfad (Kern 0.1.0 + API, gleiche Prompts):**

| Schritt | Zeit bis erstes Token (Server) | Token/s | Bedingung |
|---|---|---|---|
| Kalt, kurze Frage (468 Prompt-Tokens) | **5,7 s** | 17,1 | leeres Gedächtnis, keine Hintergrundarbeit |
| Kalt, lange Eingabe (1 489 Tokens) | **17,9 s** | 9,2 | dito |
| Fakt 1 (1 525 Tokens) | **86,5 s** | 8,2 | Extraktion der vorigen Nachricht belegt den Slot |
| Fakt 2 (1 578 Tokens) | **47,1 s** | 10,7 | dito |
| Fakt 3 (1 637 Tokens) | **65,5 s** | 4,7 | dito |
| Warm, kurze Frage (568 Tokens) | **63,6 s** | 6,9 | dito; die Antwort danach kam in 12 min nicht |
| Warteschlange leer (Drain) | **nie** (> 900 s in allen 5 Phasen) | | Extraktion mit Thinking + 4 000-Token-Budget läuft in 120-s-Timeouts |

Median der Zeit bis zum ersten Token unter Hintergrundlast: **64,6 s**
(Spanne 47–86 s). Das entspricht dem Nutzerbefund (TTFT 18–85 s im
0.1.0-Paket mit demselben Modell).

Parallellast während der App-Phase: GPU-Testläufe mit 0.6B/1B/3B-Modellen
(Vulkan) und ab 13:19 ein PyInstaller-Build. Sie erklären Schwankungen der
Token/s, nicht die Wartezeiten (die entstehen im belegten Server-Slot).

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
7. Ein HTTP-Client je llama-server-Prozess statt eines neuen `httpx.AsyncClient`
   pro Aufruf: Die Client-Erzeugung kostete auf diesem Rechner ≈ 400 ms
   (TLS-Kontext, gemessen: 388–449 ms pro Aufruf gegenüber 2–5 ms mit
   wiederverwendeter Verbindung) und fiel bei jeder Antwort mehrfach an
   (Tokenisierung des Systemprompts, Verbindungsaufbau der Generierung,
   Embedding-Aufruf der Gedächtnissuche, Bereitschaftsprobe).
8. Hintergrund-Worker wartet, solange das Modell noch lädt (vorher wurden
   offene Items nach einem Neustart ohne LLM „verarbeitet“).
9. Deterministische Korrekturschicht und Fragefilter (keine Leistungs-, eine
   Konsistenzänderung; sie erspart dem Richter Aufrufe bei expliziten
   Korrekturen).

## Ergebnisse nach den Änderungen (0.2.0)

### CPU (Commit ccfbf9d, `after-qwen3-4b-cpu`, 13:26–14:24, 3 Wiederholungen)

Quelle: `C:/Users/mucbay/spaider-foldable/measure/results/after-qwen3-4b-cpu.json`
(Kopie der Zusammenfassung in `foldable/build/measurements/after-qwen3-4b-cpu.md`).
Backend per `SPAIDER_FOLDABLE_BACKEND=cpu` erzwungen, sonst Standardeinstellungen.

**Direkt (llama-server allein, Laden 4,7 s):**

| Art | kalt | n | Erstes Token (Median) | Token/s | Prompt-Token/s | CPU-Zeit/Antwort | Peak RSS |
|---|---|---|---|---|---|---|---|
| kurz (≈494 Tokens) | ja | 1 | 6,11 s | 11,1 | 73,9 | 43,9 s | 5,36 GB |
| kurz | nein (Cache) | 3 | **0,094 s** (0,093–0,109) | 10,4 | – | 14,0 s | 5,36 GB |
| lang (≈1 488 Tokens) | ja | 1 | 17,14 s | 7,2 | 50,6 | 113,9 s | 5,37 GB |
| lang | nein (Cache) | 3 | **0,125 s** (0,093–0,125) | 9,7 | – | 42,2 s | 5,37 GB |

Die Direktwerte entsprechen der Baseline (gleiche Binärdatei, gleiches
Modell): Auf dieser CPU verarbeitet das 4B-Modell 50–75 Prompt-Tokens/s und
erzeugt 7–11 Token/s; ein Cache-Treffer bringt das erste Token in 0,1 s.

**App-Pfad (Kern 0.2 + API; Kernstart 6,3 s, Modell bereit nach 4,0 s):**

| Schritt | Gedächtnis | n | Erstes Token (Median, Client) | min/max | Token/s | Prompt-Tokens (davon aus Cache) | Gesamt |
|---|---|---|---|---|---|---|---|
| Kalt, kurze Frage | leer | 1 | **6,7 s** | | 10,9 | 494 (0) | 11,8 s |
| Kalt, lange Eingabe | leer | 1 | **17,4 s** | | 11,1 | 1 488 (424) | 26,4 s |
| Drei Fakten nacheinander | füllend | 3 | **5,9 s** | 5,2–8,2 | 10,1 | 1 694–2 093 (1 370–1 540) | 8,9 s |
| Warm, kurze Frage (neues Gespräch) | gefüllt | 3 | **10,3 s** | 8,4–10,8 | 9,8 | 921–1 202 (1–527) | 18,0 s |
| Warm, lange Eingabe | gefüllt | 3 | 36,1 s | 28,1–37,6 | 7,8 | 2 235–4 675 (630–2 603) | 53,9 s |
| Abruf-Frage (Projektleitung/Budget) | gefüllt | 3 | 29,4 s | 29,0–30,0 | 7,3 | 5 222–5 340 (3 610–3 813) | 33,7 s |
| Stopp nach 5 Token | gefüllt | 1 | 3,0 s | | | | |

| Größe | Wert |
|---|---|
| Stopp → `done`-Ereignis | **0,109 s**; llama-server-CPU ruhig nach 0,36 s |
| Gedächtnissuche (FTS, ohne Embedding-Modell) | ≤ 16 ms |
| Warteschlange leer nach kalter Kurz-/Langantwort | 17 s / 758 s |
| Warteschlange leer nach den drei Fakten | 34 s, 62 s, 36 s |
| Shutdown | 1,9 s, keine Restprozesse |
| Peak RSS llama-server | 5,4 GB (kalt) bis 9,0 GB (8 192-Token-Kontext voll) |

**Vergleich mit der Baseline (CPU, gleiches Modell):**

| | 0.1.0 | 0.2.0 |
|---|---|---|
| Erstes Token, kalt kurz / lang | 5,7 s / 17,9 s | 6,7 s / 17,4 s (gleich: Prompt-Verarbeitung der CPU) |
| Erstes Token bei Fakten mit laufender Nachverarbeitung | **47–86 s** | **5,2–8,2 s** (Chat verdrängt Hintergrund) |
| Erstes Token warm, kurze Frage | **63,6 s** | **8,4–10,8 s** |
| Warteschlange leer | nie (> 900 s) | 17–62 s je Nachricht; 758 s nach der 1 500-Token-Eingabe |
| Stopp → done | n. v. (Baseline abgebrochen; Harness-Wert 0,4 s = Client-Erzeugung) | 0,11 s |

Die großen Wartezeiten der Baseline sind weg; was bleibt, ist die reine
Prompt-Verarbeitung der CPU (≈ 60 Token/s): jede Nachricht bringt 300–600
neue Tokens (Gedächtnisblock + Nutzertext + die vorige Antwort, die im Cache
anders liegt), also 5–10 s bis zum ersten Token. Der Zielwert 1–2 s wird auf
der CPU mit einem 4B-Modell **nicht erreicht**; mit Cache-Treffer 0,1 s.

Auffällig: Die Abruf-Frage trug 5 200 Prompt-Tokens, weil die als Fakten
gespeicherten langen Eingaben (≈ 500 Tokens je Nachricht) vollständig in den
Gedächtnisblock gelangten. Seit Commit *(siehe unten)* werden Fakten im
Kontextblock auf 480 Zeichen gekürzt (Quellenanzeige unverändert); diese
Änderung lag nach dieser Messung und ist in der Vulkan-Zusatzmessung
enthalten.

### Vulkan / Intel Arc 140V (Commit ccfbf9d, `after-qwen3-4b-vulkan`, 14:25–15:00, 3 Wiederholungen)

Quelle: `foldable/build/measurements/after-qwen3-4b-vulkan.{json,md}`. Backend
automatisch gewählt („Alle Schichten auf Intel Arc 140V, geschätzt 5,6 GB von
17,2 GB frei“), `-ngl 999`, 8 Threads.

**Direkt (llama-server allein, Laden 4,2 s):**

| Art | kalt | n | Erstes Token (Median) | Token/s | Prompt-Token/s | CPU-Zeit/Antwort | Peak RSS |
|---|---|---|---|---|---|---|---|
| kurz (≈494 Tokens) | ja | 1 | **0,45 s** | 31,1 | 1 082 | 0,5 s | 4,1 GB |
| kurz | nein (Cache) | 3 | 0,032 s | 28,8 | – | 0,5 s | 4,1 GB |
| lang (≈1 476 Tokens) | ja | 1 | **1,30 s** | 29,1 | 674 | 1,4 s | 4,1 GB |
| lang | nein (Cache) | 3 | 0,031 s | 29,2 | – | 1,2 s | 4,1 GB |

**App-Pfad (Kernstart 4,6 s, Modell bereit nach 4,3 s):**

| Schritt | Gedächtnis | n | Erstes Token (Median, Client) | min/max | Token/s | Prompt-Tokens (aus Cache) | Gesamt |
|---|---|---|---|---|---|---|---|
| Kalt, kurze Frage | leer | 1 | **0,45 s** | | 29,5 | 494 (0) | 1,9 s |
| Kalt, lange Eingabe | leer | 1 | **2,14 s** | | 27,6 | 1 476 (424) | 5,7 s |
| Drei Fakten nacheinander | füllend | 3 | **1,75 s** | 0,95–1,89 | 27,4 | 1 741–2 077 (1 358–1 528) | 3,0 s |
| Warm, kurze Frage (neues Gespräch) | gefüllt | 3 | **1,25 s** | 1,20–1,31 | 26,8 | 891–1 125 (1–516) | 4,5 s |
| Warm, lange Eingabe | gefüllt | 3 | 7,1 s | 4,25–8,02 | 21,4 | 2 442–4 807 (629–2 574) | 11,8 s |
| Abruf-Frage | gefüllt | 3 | 7,5 s | 7,39–7,64 | 19,7 | 5 332–5 448 (3 543–3 707) | 9,1 s |
| Stopp nach 5 Token | gefüllt | 1 | 0,67 s | | | | |

| Größe | Wert |
|---|---|
| Stopp → `done`-Ereignis | **0,063 s**; llama-server ruhig nach 0,31 s |
| Warteschlange leer nach kalter Kurz-/Langantwort | 7 s / 484 s |
| Warteschlange leer nach den drei Fakten | 18 s, 32 s, 22 s |
| CPU-Zeit je Antwort (Kern + Server) | 0,6–3,1 s (CPU-Variante: 41–302 s) |
| Shutdown | 2,7 s, keine Restprozesse |
| Peak RSS llama-server | 4,1 GB (Gewichte im Grafikspeicher, Kopie im RAM) bis 8,9 GB bei vollem Kontext |

Prompt-Verarbeitung auf der Arc 140V fällt mit der Kontextlänge: ≈ 1 100
Token/s bei 500 Tokens, ≈ 650 bei 1 500, ≈ 240 bei 5 300 (Aufmerksamkeit
über den langen Kontext). Das 1–2-s-Ziel wird daher mit kurzen Prompts und
Verlauf unter ≈ 2 000 Tokens erreicht, bei langem Verlauf nicht.

### Zielwerte: erreicht / nicht erreicht (dieser Rechner, Qwen3-4B Q4_K_M)

| Ziel | CPU | Vulkan (Arc 140V) |
|---|---|---|
| Erstes Token 1–2 s (kurze Frage, Modell geladen) | **nein**: 6,7 s kalt, 8–11 s warm mit Gedächtnis (Cache-Treffer 0,1 s) | **ja** bei ≤ 1 000 Prompt-Tokens (0,45–1,3 s); **nein** bei langem Verlauf/Eingabe (4–8 s) |
| ≥ 20–30 Token/s | **nein**: 7–11 | **ja**: 20–31 (19,7 bei 5 400 Tokens Kontext) |
| Stopp ≤ 500 ms | **ja**: 0,11 s | **ja**: 0,06 s |
| Keine Leerlauflast | **ja**: 0,42 % eines Kerns (Kern + Server, Kanal offen) | **ja** (gleiche Messung) |
| UI-Reaktion ≤ 100 ms | Renderer: Token-Batching 40 ms, max. Frame-Lücke 34 ms im Mock-Stream mit Tippen; mit echtem Kern nicht separat gemessen | dito |

Kleine Modelle auf der GPU (Aktivitäts-/Familienläufe, synthetische Prompts):
0.6B 64–66 Token/s, erstes Token 0,14–0,22 s; Llama 3.2 1B 71 Token/s, 0,45–0,66 s;
Ministral 3B 28,5 Token/s, 0,59–0,89 s.

## Verbleibende Engpässe und nicht erreichte Zielwerte

1. **Prompt-Verarbeitung dominiert die Zeit bis zum ersten Token.** Jede
   Nachricht bringt 300–600 neue Tokens, obwohl der Präfix gecacht ist: Der
   Gedächtnisblock sitzt in der letzten Nutzernachricht und ändert sich je
   Anfrage, und die vorige Nutzernachricht liegt im Verlauf ohne ihren
   damaligen Gedächtnisblock, so dass der Cache ab dort nicht mehr passt
   (`cached` 1 358 von 1 741 usw.). Auf der CPU (≈ 60 Token/s) sind das 5–10 s,
   auf der Arc 140V 1–2 s. Nächster Schritt (nicht umgesetzt): den Verlauf
   exakt so wiederverwenden, wie er gesendet wurde (Gedächtnisblock je Nachricht
   speichern), dann kostet eine Folgefrage nur noch neuen Block + neue Nachricht.
2. **Hintergrundarbeit verdrängt den Prompt-Cache.** Der llama-server hat
   einen Slot; eine Extraktion dazwischen überschreibt den Chat-Präfix (die
   erste Frage eines neuen Gesprächs nach einer Extraktion: `cached` 1). Zwei
   Slots (`--parallel 2`, doppelter KV-Speicher) würden den Chat-Cache halten;
   nicht umgesetzt, weil der KV-Bedarf auf 8-GB-Rechnern knapp wird.
3. **Lange Kontexte auf der iGPU**: Prompt-Durchsatz sinkt von 1 100 auf
   240 Token/s bei 5 000+ Tokens; die Abruf-Frage trug 5 300 Tokens, weil lange
   Nutzereingaben als Fakten vollständig in den Gedächtnisblock gelangten.
   Seit 353bda0 werden Fakten im Kontextblock auf 480 Zeichen gekürzt;
   Zusatzmessung siehe unten.
4. **Extraktion langer Eingaben** braucht auch mit 1 500-Token-Budget und
   Thinking aus 8 min (GPU) bzw. 12,6 min (CPU) für eine 1 500-Token-Nachricht
   (Abschnitte × JSON-Generierung × Richter). Sie läuft unterbrechbar im
   Hintergrund und blockiert keine Antwort mehr, kostet aber Strom/Zeit.
   Nächster Schritt: Extraktion für sehr lange Eingaben auf die ersten
   Abschnitte begrenzen oder Dokumentimport empfehlen.
5. **CPU-Rechner ohne GPU**: Das 4B-Modell verfehlt dort alle Zeitziele außer
   Stopp und Leerlauf. Die Empfehlung schlägt auf solchen Rechnern kleinere
   Modelle vor (1–2B), die Geschwindigkeit ist aber erst nach „Kurz messen“
   belegt.
6. **Nicht gemessen**: andere GPUs (NVIDIA/AMD über Vulkan), Teil-Offload,
   Energie, UI-Latenz mit echtem Kern im Electron-Fenster, p95 (zu wenige
   Wiederholungen).
