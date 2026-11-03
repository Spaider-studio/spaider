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

## Leerlauf

Ohne Live-Sitzung findet keine Aufnahme statt (keine Timer). In einer
ruhenden Live-Sitzung gibt es keine Dauerschleife: Snapshots nur bei Frage,
Vorschau-Anforderung oder Agentenschritt; Frames nur im RAM (die letzten zwei).
Messung mit Overlay und gepacktem Backend: *(Desktop-Teil, wird eingetragen)*.

## Tradeoffs und offene Hebel

- Zweiter Server-Slot (Chat vs. Agent) nicht aktiviert: ein Slot bleibt die
  kontrollierte Basis; Nutzerfragen haben Vorrang, Agentenschritte warten an
  der Schrittgrenze.
- Bild-Token-Budget (`--image-max-tokens`) steht auf dem Server-Standard; ein
  kleineres Budget beschleunigt einfache Fragen, verliert aber kleine Schrift.
- Speculative Decoding, DXGI-Aufnahme und zweites Planungsmodell wurden nicht
  umgesetzt (kein nachgewiesener Engpass gegenüber Prefill/Beobachtung).
