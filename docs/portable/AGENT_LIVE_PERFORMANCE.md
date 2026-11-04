# Performance 0.3: Live-Bildschirmhilfe und Agent

Stand: 2026-11-04 (wird im Lauf der Sitzung ergänzt). Alle Zahlen sind
Messwerte dieses Rechners (Intel Core Ultra 7 268V, 32 GB, Intel Arc 140V über
Vulkan, Netzbetrieb, interaktiver Desktop); Schätzungen sind markiert.
Rohdaten unter `foldable/build/measurements/` (synthetische Inhalte).

## Messpunkte (gemeinsame Trace-ID = Anfrage-/Aufgaben-ID)

| Kette | Messpunkt | Quelle |
|---|---|---|
| Hotkey → Overlay sichtbar → erster Paint | Launcher (`performance.now` am globalShortcut-Callback, `show`, erster Frame des Renderers) | Launcher-Log / `getPerfSamples()` (Desktop-Teil) |
| Live-Frage | `capture` (Fensteridentität + PrintWindow), `observe` (UIA), `image_encode`, `memory_search`, `prompt_build`, `prompt_processing` (inkl. Bildencoding im Server), `generating` | Aktivitätsereignisse der Antwort, `timings` der Nachricht |
| Agentenschritt | `observe` (UIA/Browserseite), `wait` (Warten auf Nutzerantwort/Chat-Vorrang), `planning` (Modellaufruf mit Grammatik), `executing` (Werkzeug), `verifying` | Aktivitätsereignisse der Aufgabe; Summen in `task.result.timings` |
| Stopp | Sperre neuer Aktionen (Abbruchgeneration) → Ende des laufenden Betriebssystemaufrufs | Journal (`unknown_outcome`), Tests |

## Baseline (0.2, unverändert)

Chat ohne Live/Agent: siehe `PERFORMANCE_REPORT.md` (Qwen3-4B, Vulkan: kurze
Frage 0,45 s bis zum ersten Token, 20–31 Token/s; CPU 6–10 s). Diese Werte
werden nach dem 0.3-Paket erneut geprüft (Regression bei deaktiviertem
Live/Agent) – *(wird eingetragen)*.

## Beobachtung (ohne Modell)

| Größe | Wert | Bedingung |
|---|---|---|
| Fensteraufnahme (PrintWindow, 1430×744) | 46 ms | Notepad, Dev-Kern |
| Bildvorbereitung (Verkleinern + PNG) | 63 ms (1280×665, 41 KB) | dito |
| UIA-Lesen, kalt / warm | 360 ms / 63 ms (28 Elemente) | Notepad; erster Aufruf nach Prozessstart |
| UIA-Lesen im gepackten Kern, erster Aufruf | > 10 s (Timeout) → Aufwärmen beim Start | comtypes erzeugt Wrapper beim ersten Aufruf; danach wie oben |
| Snapshot über die API (Vorschau) | 108 ms (16 Elemente, 242 Zeichen, Vorschau 20 KB) | WinForms-Testapp |
| Bildschirmaufnahme (mss, 1920×1080) | 109 ms | – |
| Browser: Seite öffnen inkl. Start | 2,5 s (kalt) / 1,1 s (warm) | Chromium for Testing, lokales Formular |
| UIA `set_value` + Rücklesen / `invoke` | 406 ms / 531 ms | WinForms-Testapp |

## Bildmodell (Qwen3-VL-4B Q4_K_M + mmproj Q8_0, synthetisches 1280×720-Bild, ≈ 900 Prompt-Tokens)

| Konfiguration | Laden | Erstes Token | Generierung | Grounding-Antwort |
|---|---|---|---|---|
| Vulkan, Projektor auf GPU | 13,1 s (erster Start, Shader) / 3,8 s (warm) | **4,7 s** | 28,2 Token/s | 5,4 s, Koordinaten korrekt (normiert 0–1000) |
| Vulkan, Projektor auf CPU | 4,1 s | 11,5 s | 28,3 Token/s | 12,8 s |
| CPU | 4,4 s | 21,4 s | 16,5 Token/s | 24,3 s |

Live-Frage über den App-Pfad (Fenster 626×413 nach Verkleinerung, UIA-Struktur
dazu, Gedächtnissuche): **erstes Token 2,77 s**, gesamt 4,0 s; Strukturfrage
ohne Bild 2,5 s. Zielwert „kurze warme Bildschirmfrage 2–5 s“ damit erreicht;
die Bildgröße ist der Haupthebel (Einstellung `live_image_max_side`).

## Agent (Qwen3-4B Text, Vulkan)

| Lauf | Schritte / Modellaufrufe | Wandzeit | davon Planung | Werkzeuge | Ergebnis |
|---|---|---|---|---|---|
| Dateiaufgabe (3 Dateien lesen, Bericht schreiben) | 6 / 6 | 46 s | 45,9 s (≈ 7,6 s je Aufruf, ≈ 1 200 Prompt-Tokens) | 45 ms gesamt | belegt (Rücklesen + SHA) |
| Browserformular (öffnen, Datei lesen, 3 Felder, Absenden, Bestätigung) | 13 / 13 | 106 s | ≈ 95 s | ≈ 1 s | Formular korrekt, Bestätigung erkannt erst nach vier Snapshots |

Planungsaufrufe dominieren (Prompt ≈ 1,2–2 k Tokens, Antwort ≈ 100–200 Tokens
mit Grammatik). Der Präfix (Systemprompt + Werkzeugliste) bleibt je Aufgabe
stabil und wird vom Prompt-Cache getragen; Beobachtungen und Aktionsverlauf
ändern sich je Schritt.

## Aufgabenmatrix

*(wird eingetragen: Erfolg/Nenner, Median-Wandzeit, Modellaufrufe, Rückfragen je
Gruppe und Modell; Qwen3-VL-4B vollständig, Qwen3-4B und Gemma 4 E2B Teilmenge)*

## Oberfläche (echtes Electron-Fenster, echte Tastatureingabe, Backend = Mock)

Desktop-Teil, gemessen im gepackten Launcher-Code (`dist/main`) mit vorgeladenem,
versteckten Overlay-Fenster; 12 Hotkey-Auslösungen je Lauf, vier Läufe:

| Größe | Median | min / max | Anmerkung |
|---|---|---|---|
| Hotkey → Overlay `show` | 12,3 ms | 10,5 / 24,0 | frühere Läufe 10,7 / 13,3 / 13,4 ms |
| Hotkey → erster Paint des Overlays | 16,7 ms | 13,6 / 54,4 | Ausreißer 44–54 ms bei einzelnen Auslösungen |
| darin `POST /api/live/foreground` (Mock) | 1,6 ms | – / 2,0 | mit echtem Kern höher (siehe unten) |
| Tastendruck → DOM-Update im Eingabefeld (n = 49) | 1,2 ms | – / 2,5 | |
| Stopp-Klick → Status „Wird gestoppt …“ | 1,0 ms | 0,7 / 1,0 | je Lauf eine Messung |
| Frame-Lücken während Streaming | Mittel 16,7 ms | max 17,3 (lang 33,4) | 0 Lücken > 50 ms |

Zielwerte „Overlay ≤ 200 ms“, „Reaktion ≤ 100 ms“ sind mit Mock-Backend erfüllt;
die Werte mit echtem Kern (Foreground-Aufruf, Snapshot, Modell) folgen unten.

## Oberfläche mit echtem Kern

Gemessen am 2026-11-04 (Desktop-Teil, Commit a85dca6) im echten Electron-Fenster
mit echter Tastatureingabe (`keybd_event`) gegen den Dev-Kern (`python -m
spaider_foldable`, Qwen3-VL-4B + Projektor über Vulkan, Hotkey im Test
Ctrl+Alt+F9). Rohdaten: `scratchpad/ui-real-timings.json` (Sitzung), Screenshots
`shots-real/01…08`. **Einschränkung:** parallel lief die Aufgabenmatrix mit
eigenem llama-server auf derselben GPU; Modellzeiten sind daher pessimistisch
(≈ 8–11 Token/s statt 28), Hotkey-/UI-Latenzen sind CPU-seitig und wenig betroffen.

| Größe | Median | min / max | n | Anmerkung |
|---|---|---|---|---|
| Hotkey → Overlay `show` (inkl. `POST /api/live/foreground`) | **57,2 ms** | 42,6 / 97,1 | 24 (2 Läufe × 12) | Mock: 12,3 ms |
| Hotkey → erster Paint | **63,4 ms** | 48,8 / 103,9 | 24 | Zielwert ≤ 200 ms erfüllt |
| `POST /api/live/foreground` (Kern) | 34,6 ms | 23,6 / 62,8 | 24 | Zielwert ≤ 50 ms im Median erfüllt, max 62,8 |
| OS-Gegenprobe: letzter Tastendruck (Wanduhr) → `show` | 60,0 ms | 46 / 100 | 24 | unabhängig vom Renderer gemessen |
| Tastendruck → DOM-Update | 1,2–1,3 ms | 0,9 / 2,7 | 50 je Frage | |
| Stopp-Hotkey (Ctrl+Alt+Backspace) → „Wird gestoppt …“ | **3–4 ms** | – | 2 | je Lauf eine Messung |
| Stopp-Hotkey → `done` mit Status `stopped` („Abgebrochen“) | 21 / 42 ms | – | 2 | Kern: `agent/stop` + Gesprächsstopp, HTTP 200 |
| Frage senden → erste Aktivität („Aufnahme fertig“) | 171 ms | 154 / 379 | 6 | |
| Frage senden → erstes sichtbares Token (mit Bild, 1 280–1 420 Prompt-Tokens) | 7,2 s | 5,4 / 9,2 | 6 | GPU geteilt; ohne Last 2,77 s (Kern-Messung oben) |
| Frage senden → „Antwort fertig“ | 12,2 s | 9,3 / 14,9 | 6 | 46 Antwort-Tokens |
| Frame-Lücken während Streaming | max 33,6 ms | – | 891 Frames / 14,9 s | 0 Lücken > 50 ms |

Phasen in der Aktivitätszeile (Lauf 2, Frage 1, ms nach Enter): 379 Aufnahme
fertig → 380 Struktur gelesen (16 Elemente, 240 Zeichen, UIA 93–125 ms) → 381
Gedächtnissuche → 384 Kontext vorbereitet → 385–7 608 Kontext wird verarbeitet
(Bild 626×413, ≈ 8,5 kB; Folgefragen 424–495 Tokens aus dem Cache) → 7 610
Antwort wird geschrieben → 13 588 Antwort fertig. Die Bildverarbeitung im Server
dominiert bei geteilter GPU. Antwort in allen sechs Fällen inhaltlich korrekt
(Feld „Name“ und Budget aus den „Notizen“ der Testanwendung).

Aufgabe über die Oberfläche (Dateien lesen → Zusammenfassung schreiben, Ordner
per echtem Ordnerdialog-IPC gewählt): abgeschlossen in 89,5 s, Schritt 3/20,
3 Modellaufrufe, Ergebnis „geprüft“ (Datei 167 Bytes zurückgelesen).

Esc → Overlay versteckt → `DELETE /api/live/sessions/{id}` (Sitzungsliste
danach leer); erneuter Hotkey (56 ms) behält Quelle und Gesprächsverlauf, die
nächste Frage erzeugt eine neue Sitzung mit demselben Gespräch.

## Leerlauf

Ohne Live-Sitzung findet keine Aufnahme statt (keine Timer). In einer
ruhenden Live-Sitzung gibt es keine Dauerschleife: Snapshots nur bei Frage,
Vorschau-Anforderung oder Agentenschritt; Frames nur im RAM (die letzten zwei).
Messung mit Overlay und echtem Kern (30 s Leerlauf mit sichtbarem Overlay und
offener Sitzung, CDP `Network.requestWillBeSent` auf beiden Renderern, zwei
Läufe identisch): Overlay **0 Anfragen**; Hauptfenster nur die bestehenden
0.2-Abfragen (`GET /api/setup/status` ×2, `GET /api/conversations` ×1); im
Kern-Log eine Zeile (serverseitige Gedächtnis-Nachverarbeitung, nicht
UI-ausgelöst). Keine Snapshot- oder Modellaufrufe im Leerlauf.

## Tradeoffs und offene Hebel

- Zweiter Server-Slot (Chat vs. Agent) nicht aktiviert: ein Slot bleibt die
  kontrollierte Basis; Nutzerfragen haben Vorrang, Agentenschritte warten an
  der Schrittgrenze.
- Bild-Token-Budget (`--image-max-tokens`) steht auf dem Server-Standard; ein
  kleineres Budget beschleunigt einfache Fragen, verliert aber kleine Schrift.
- Speculative Decoding, DXGI-Aufnahme und zweites Planungsmodell wurden nicht
  umgesetzt (kein nachgewiesener Engpass gegenüber Prefill/Beobachtung).
