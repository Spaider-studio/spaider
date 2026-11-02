# SpAIder Foldable: Prüfbericht V1

Stand: 2026-11-02. Alle Angaben sind tatsächlich ausgeführte Läufe auf dem
unten beschriebenen Rechner; nicht ausgeführte Prüfungen sind als
**NICHT VERIFIZIERT** gekennzeichnet. Zahlen sind Messwerte, keine Schätzungen.

## Umgebung

| | |
|---|---|
| Rechner | Windows 11 Enterprise 10.0.26200 x64, Intel Core Ultra 7 268V (8 Threads), 32 GB RAM, Intel Arc 140V (nicht genutzt), NTFS, 280 GB frei |
| Rechte | kein Administrator, kein WSL/Docker, kein C-Compiler |
| Runtime | llama.cpp b12032 (CPU x64, `version: 0.8.0-dev (build 12032, commit dde879bc4)`), SHA-256 des Release-ZIP `5296f5d1…d0d4` |
| Chatmodell (getestet) | `Qwen/Qwen3-0.6B-GGUF` rev `23749fef…`, `Qwen3-0.6B-Q8_0.gguf`, 639 446 688 Bytes, SHA-256 `9465e63a…b031` (verifiziert) |
| Embedding-Modell (getestet) | `Qwen/Qwen3-Embedding-0.6B-GGUF` rev `370f27d7…`, `Qwen3-Embedding-0.6B-Q8_0.gguf`, 639 150 592 Bytes, SHA-256 `06507c7b…c439` (verifiziert), 1024 Dimensionen |
| Kontext | 8192 Tokens (Standard), Thinking aus, Temperatur 0,7 |
| Commit | siehe Abschnitt „Build“ |

Alle anderen Katalogmodelle (Qwen3 1.7B/4B/8B, Qwen3.8-27B) wurden **nicht
ausgeführt** (Katalog: `support = documented`).

## Automatisierte Prüfungen

| Suite | Befehl | Ergebnis |
|---|---|---|
| Kern, Unit | `cd foldable/core && pytest` (ohne die `real_*`-Module) | **40 bestanden** (Pfade/Lock/DB, GGUF, Katalog-Fit, Downloader mit Resume/Checksumme/Hostprüfung, Memory-Store, Resolver-Wiederverwendung, Supersession inkl. Fuzzy-Markierung, Prompt-Leck-Guard, Zeitstempel-Kontext, Dokumente, Tool-Grants, API, Job-Manager mit Platzmangel/FAT32/Rollback) |
| Kern, Lint | `ruff check spaider_foldable tests` (E, F, W, I, 120 Zeichen) | sauber |
| Kern, echter E2E | `pytest tests/test_real_e2e.py` mit `SPAIDER_FOLDABLE_REAL_TESTS=1` | **bestanden**, 6:47 min (letzter Lauf; drei frühere Läufe deckten Fehler auf, siehe WORKLOG) |
| Kern, Offline | `pytest tests/test_real_offline.py` | **bestanden**, 2:44 min |
| Desktop | `npm run typecheck`, `npm run lint`, `npm test` | sauber, sauber, **34 bestanden** (5 Dateien) |
| Desktop, Build/Paket | `npm run build`, `npm run package` | ok, `out/SpAIder-win32-x64/SpAIder.exe` (rcedit-Metadaten ohne Admin gesetzt) |
| Paket | `python foldable/build/verify_package.py <zip> --screenshot …` | ok (siehe Fall 1) |
| SpAIder-Regression | `backend/` Testsuite | **NICHT AUSGEFÜHRT** auf diesem Rechner: sie benötigt den Docker-Compose-Stack (Neo4j, Redis, Kafka), der hier nicht läuft. Keine Datei unter `backend/`, `frontend/`, `cli/`, `sdk/` wurde geändert (`git diff --stat 73762b3..HEAD -- backend frontend cli sdk` ist leer); die portable Betriebsart setzt ihre Standardwerte nur im eigenen Prozess (`bootstrap.py`). |

## Abnahmefälle

Alle Fälle 1 bis 9 stammen aus `tests/test_real_e2e.py`, `tests/test_real_offline.py`
und `build/verify_package.py`; die Rohdaten liegen in den JSON-Berichten
der Läufe (`e2e-report-final.json`, `offline-report.json`, Ausgabe von
`verify_package.py`).

### 1. Frischer Start (entpacktes Paket, ohne Entwickler-Laufzeiten)

Ausgeführt mit `verify_package.py`: ZIP in einen Ordner mit Leerzeichen und
Umlauten entpackt, PATH auf `C:\Windows\System32;C:\Windows` reduziert, kein
PYTHONPATH, keine venv. Ergebnis: Layout vollständig (9 geprüfte Dateien),
Kern aus `resources/core` bereit in **8,1 s**, `/api/setup/status` =
`needs_model`, `/api/system/info` meldet die gebündelte Runtime, Anfrage ohne
Token → 401, Shutdown → Exit 0, Lock entfernt. Anschließend `SpAIder.exe`
gestartet (Screenshot-Modus): Launcher spawnt den Kern, Oberfläche zeigt den
Einrichtungsbildschirm mit echtem Katalog und echten Hostdaten
(`fresh-start-setup.png`), Launcher beendet sich sauber.

Die echte Modellantwort nach Download im Paket wurde **nicht** über die
Oberfläche geklickt (kein Bediener); der identische Pfad (Aktivierung → Probe
→ Chat) lief im E2E-Test über die API mit demselben Kern-Code. Download über
die Oberfläche: gegen den Mock-Kern geprüft, gegen Hugging Face nur über die
API/Downloader-Klasse (zwei echte Downloads, beide prüfsummenverifiziert).

### 2. Offline

Simulation (kein Admin, Adapter nicht abschaltbar): alle Proxy-Variablen auf
`http://127.0.0.1:1` (toter Proxy), `NO_PROXY=127.0.0.1,localhost`; parallel
119 `netstat -ano`-Stichproben über Kern- und llama-server-PIDs. Ergebnis:
**0 Verbindungen zu Nicht-Loopback-Adressen**, Chat beantwortet
(„Die Hauptstadt von Frankreich ist Paris.“), Markdown-Import und Frage dazu
beantwortet („Der Offline-Test lief am Dienstag in Köln.“), ein bewusst
gestarteter Download scheitert sofort (`[WinError 10061]`, Job `paused`,
fortsetzbar). Ein echter Air-Gap-Test ist **NICHT VERIFIZIERT** (siehe manuelle
Anleitung).

### 3. Neustart

Nach „Sicher beenden“ (API) und Neustart desselben Datenverzeichnisses: das
Gespräch A mit 8 Nachrichten und den Status `complete, complete, complete,
stopped`, aktive Modellauswahl (Chat + Embedding) automatisch wieder geladen,
`setup/status = ready`. Bestanden.

### 4. Umzug

Datenverzeichnis geschlossen, kompletten App-Ordner nach
`…\Neuer Ort é ß\SpAIder Foldable` verschoben und über `subst Z:` als Laufwerk
`Z:\SpAIder Foldable` gestartet. Gespräche (A, Chat B), Dokument mit Text und
Status `indexed`, Modelle: alle vorhanden; neues Gespräch C beantwortet.
Bestanden als **Simulation auf demselben Rechner**; Zwei-PC-Test
**NICHT VERIFIZIERT**.

### 5. Kontext über Gespräche

Chat A: „Mein Projekt heißt Orion. Der Projektleiter von Orion ist Idris Kane.
Das Budget beträgt 42000 Euro.“, dann „Korrektur: … nicht mehr Idris Kane,
sondern jetzt Priya Nair.“ Nach Neustart in Chat B: „Wer ist aktuell der
Projektleiter von Orion und wie hoch ist das Budget?“

Antwort (echtes Modell): **„Der aktuelle Projektleiter von Orion ist Priya
Nair. Das Budget von Orion beträgt 42000 Euro.“** Quellen: zwei Fakten mit
Herkunft „Gespräch A“ (Titel-Snapshot), beide Status `current`.

Ehrliche Einschränkungen: Der Supersession-Richter (0.6B) hat die Korrektur in
diesem Lauf **nicht** als Ersetzung markiert (`supersession_fired = false`; in
einem früheren Lauf hat er es getan). Beide Fakten blieben „aktuell“; das
Modell entschied anhand der Fakten-Zeitstempel und des Korrekturtextes richtig.
Im späteren Gespräch C (nach dem Umzug, Temperatur 0,7) nannte dasselbe
Modell auf „Wie heißt mein Projekt und wer leitet es?“ jedoch den **alten**
Projektleiter. Das ist ein echtes Qualitätslimit des 0.6B-Modells, dokumentiert
in `KNOWN_LIMITATIONS.md`. Gedächtnis-Zähler nach den zwei Nachrichten: 2
Fakten, 4 Entitäten, 4 Relationen, 6 Vektoren (1024-dim).

### 6. Dokumente

`firmen notiz.txt` außerhalb des Pakets angelegt, importiert, Original
gelöscht, Indexierung abgewartet. Frage „Wo hat die Firma Zephyr ihren
Hauptsitz?“ → **„Die Firma Zephyr hat ihren Hauptsitz in Lissabon.“**, Quellen
enthalten das Dokument (Abschnitt) und den daraus extrahierten Fakt. Kopie
liegt unter `data/imports/<id>/`. PDF ohne Textebene wird als `failed` mit
Warnung `no_text_layer` abgelehnt (Unit-Test). Bestanden.

### 7. Modellwechsel

| Teilfall | Art | Ergebnis |
|---|---|---|
| Erfolgreiche Aktivierung | echt | Chat 0.6B: Job `done` in 2,95 s (Laden 2,25 s), Probe-Antwort erhalten; Embedding 0.6B: `done`, Vektoren aktiviert |
| Fehlgeschlagener Start | echt | synthetische GGUF-Datei (gültiger Header, keine Tensoren) importiert und aktiviert → llama-server Exit 1 → Job `failed`, Phase `rollback`, Meldung „… Das vorherige Modell „Qwen3 0.6B (Q8_0)“ ist wieder aktiv.“; danach Chat weiter möglich, Gedächtnis unverändert (2 Fakten), beide Modelldateien vorhanden, aktives Modell nicht löschbar (409) |
| Platzmangel | Mock (`host_info`) | `409 insufficient_space` mit `required_bytes`/`free_bytes`; FAT32 mit > 4 GB Datei → `file_too_large_for_filesystem` |
| Unterbrochener Download | lokaler HTTP-Server | Abbruch → `paused` mit `.part`, Fortsetzen per `Range` → `done`, Prüfsumme bestätigt; Verbindungsabbruch mitten im Stream → automatischer Resume (Unit-Test) |
| Echter Download | echt (Hugging Face) | zwei Dateien über `resolve/<revision>`, Weiterleitungen auf `*.hf.co` geprüft, SHA-256 bestätigt (je ~6 min bei ~1,5 MiB/s) |

### 8. Lebenszyklus

Antwort nach 8 Token gestoppt → Nachricht gespeichert mit Status `stopped`
(37 Zeichen). Shutdown über API: Streams gestoppt, Worker beendet, beide
llama-server beendet, DB mit WAL-Checkpoint geschlossen, Lock entfernt, Exit 0
in **2,3 s / 2,5 s / 2,1 s** (drei Shutdowns). Nach jedem Shutdown: keine
`llama-server.exe` mehr in der Prozessliste. Launcher-Pfad: `POST
/api/system/shutdown`, Warten bis 20 s, sonst `taskkill /T /F` (im
Screenshot-Modus einmal benötigt, weil der Kern noch nicht bereit war).
Wiederanlauf nach simuliertem Absturz (Lock mit fremder PID/Host) im Unit-Test.

### 9. Sicherheit

- Chatinhalte führen keinen Code aus: Markdown ohne Roh-HTML (Vitest: `<script>`
  wird als Text gezeigt), Links nur über Allowlist; im E2E-Lauf 200 001 Zeichen
  → 422.
- Pfad-Traversal: `is_inside_data` (Unit-Tests), Dokumentnamen werden auf den
  Dateinamen reduziert (`../../evil.txt` → `evil.txt` unter `imports/`),
  `GET /documents/../../etc` → 404/422, Modell-Import ohne absoluten Pfad → 409.
- Keine Geheimnisse/Modelle im Git: `*.gguf`, `*.gguf.part`, Build-Ausgaben,
  `dev-data/` ignoriert; Sitzungstoken nur in der Umgebung.
- Token-Pflicht und Loopback: ohne Header 401 (Unit + Paket-Test); llama-server
  mit zufälligem API-Key nur auf 127.0.0.1.

### 10. Regression

Keine bestehende SpAIder-Datei geändert (siehe Tabelle oben). Die Serversuite
unter `backend/tests` wurde hier **NICHT AUSGEFÜHRT** (Docker-Stack fehlt).

## Messwerte (echt, Qwen3-0.6B Q8_0, CPU, 8 Threads)

| Größe | Wert | Bedingung |
|---|---|---|
| Kernstart (Dev, `python -m`) | 7,2 s | finaler Lauf; 4,3 bis 18 s in anderen Läufen je nach Parallellast |
| Kernstart (eingefroren, aus ZIP) | 8,1 s | `verify_package.py` |
| Modellaktivierung inkl. Probe | 2,95 s (Laden 2,25 s) | 639 MB von NTFS-SSD, warm |
| Zeit bis erstes Token | 2 327 ms (Server) / 3,23 s (inkl. Retrieval, Client) | Prompt 489 Tokens |
| Generierung | 24,0 Token/s (final) · 34,9 Token/s (ungestörter Lauf) · 51,5 Token/s (Kurzantwort) | Prompt-Verarbeitung 243 Token/s |
| RSS llama-server | 1 573 MB (Chat) · 1 412 MB (Embedding) | `tasklist` |
| RSS Kern | 4,3 MB (Elternprozess-Anzeige) | `tasklist`, nur Hauptprozess |
| Shutdown | 2,1 bis 2,5 s | drei Messungen |
| Paket | ZIP 307 MB, entpackt 697 MB | ohne Modelle |

Der finale Lauf lief parallel zu Desktop-Tests (Vitest/jsdom), daher die
niedrigere Token-Rate als im ungestörten Lauf (34,9 Token/s, 1 906 ms TTFT).

## Build

| | |
|---|---|
| Paket | `foldable/build/out/SpAIder-Foldable-0.1.0-win-x64.zip` (321 949 175 Bytes) |
| SHA-256 | `5c5b06b45ba6603fc769e9730411299ae165811cc3938b4d458cc4da942dc36d` |
| Gebauter Commit | `424377c90eef8cac9b74a814c25e17ab1720c92d` (`feat(foldable): chronology-aware memory context …`); die Dokumentations-Commits danach ändern keinen Code |
| Verifikation | `verify_package.py` auf genau diesem ZIP: Layout ok, Kern bereit in 8,1 s, `needs_model`, 401 ohne Token, Shutdown Exit 0, Launcher-Screenshot des Einrichtungsbildschirms (`fresh-start-setup.png`, Launcher-Laufzeit 7,8 s) |
| Reproduktion | `python foldable/build/fetch_runtime.py` → `pyinstaller foldable/build/spaider_foldable_core.spec` → `cd foldable/desktop && npm ci && npm run package` → `python foldable/build/assemble_portable.py --version 0.1.0` |

Das ZIP liegt nicht in Git (zu groß); es liegt auf dem Entwicklungsrechner
unter dem genannten Pfad.

## Nicht verifiziert (manuelle Anleitung für Musa)

**Zwei-PC-Test mit USB-SSD** (10 Minuten):

1. ZIP auf die USB-SSD entpacken (NTFS/exFAT), `SpAIder.exe` starten, im
   Einrichtungsbildschirm „Qwen3 1.7B (Q8_0)“ oder das getestete 0.6B wählen,
   herunterladen (Internet nötig), optional Embedding-Modell dazu. Warten bis
   der Chat erscheint.
2. Im Chat drei Fakten nennen (z. B. „Mein Projekt heißt X, Leiter ist Y,
   Budget Z“), eine Datei importieren, „Sicher beenden“, Datenträger abmelden.
3. Am zweiten PC (Windows 11 x64) anstecken, `SpAIder.exe` starten: ohne
   Download muss der Chat sofort verfügbar sein, Gespräch und Datei sichtbar.
   Neues Gespräch: nach dem Projekt fragen und auf „Quellen“ klicken.
4. Am zweiten PC WLAN/LAN ausschalten und erneut eine Frage stellen (echter
   Air-Gap-Test). Danach „Sicher beenden“.
5. Erwartung: gleicher Inhalt, andere Geschwindigkeit. Bei Problemen
   `data/logs/core.log`, `llama-chat.log`, `launcher.log` mitschicken.

---

# Prüfbericht Update 0.2 (Performance, Aktivität, Modelle)

Stand: 2026-11-02 (Nachmittag). Branch `feature/spaider-foldable-performance-models`.
Gleicher Rechner wie oben; zusätzlich genutzt: Intel Arc 140V über die
Vulkan-Variante von llama.cpp b12032. Alle Zahlen sind Messwerte dieses
Rechners; „NICHT VERIFIZIERT“ markiert, was nicht ausgeführt wurde.

## Automatisierte Prüfungen (0.2)

| Suite | Befehl | Ergebnis |
|---|---|---|
| Kern, Unit | `cd foldable/core && pytest` (ohne `real_*`) | **52 bestanden**, 4 übersprungen (Echtläufe ohne Umgebung). Neu: Planer (Verdrängung, Cooldown, Pause), Aktivitäts-Tracker/Bus, Rohfakt sofort sichtbar und Anreicherung, Worker wartet bei ladendem Modell, deterministische Korrekturen und Fragefilter, `plan()`-Entscheidungen, Fähigkeiten/Typen, Empfehlungen mit simulierten Profilen, Remote-GGUF-Header über lokalen Range-Server, Split-Erkennung |
| Kern, Aktivität (echt) | `pytest tests/test_real_activity.py` (0.6B, Vulkan) | **bestanden** (siehe A/C unten; Rohdaten `foldable/build/measurements/activity-report.json`) |
| Kern, Familien (echt) | `pytest tests/test_real_families.py` (Llama 3.2 1B, Ministral 3 3B, Vulkan) | **2 bestanden**, 2:42 min (`families-report.json`) |
| Kern, GPU-Rückfall (echt) | `pytest tests/test_real_gpu_fallback.py` (Vulkan-Ordner durch einen sofort scheiternden Stellvertreter ersetzt, Backend erzwungen) | **bestanden**, 19 s: GPU-Start scheitert → CPU-Start in derselben Sitzung, `gpu_failures = ["vulkan"]`, Antwort auf CPU |
| Kern, E2E (echt) | `pytest tests/test_real_e2e.py` | *(wird eingetragen)* |
| Kern, Offline | `pytest tests/test_real_offline.py` | *(wird eingetragen)* |
| Desktop | `npm run typecheck`, `npm run lint`, `npm test` | sauber, sauber, **59 bestanden** (9 Dateien; neu: Aktivitäts-Reducer, Token-Batcher, Empfehlungen, Modell-Fehlertexte) |
| Desktop, Build/Paket | `npm run build`, `npm run package` | ok, 0.2.0 (`VersionInfo` 0.2.0), gegen den Mock mit Screenshot gestartet |
| Paket 0.2.0 | `verify_package.py` | *(wird eingetragen)* |

## Testgruppen A–E

### A. Modellunabhängigkeit

- Ausgeführt mit echten Gewichten: **Qwen3 0.6B** (Qwen3), **Llama 3.2 1B
  Instruct** (Llama), **Ministral 3 3B Instruct** (Mistral), dazu Qwen3-4B
  (Baseline/Nachher-Messung, CPU) und Qwen3-Embedding 0.6B. Katalog: 10
  Familien, 17 Einträge; nur Metadaten geprüft: Qwen3.5/3.8, SmolLM3, Phi,
  Gemma 3/4, EmbeddingGemma, nomic.
- Für jede Familie derselbe Weg: Header lesen → Fähigkeiten (Vorlage,
  Denkmodus-Umschalter, Systemrolle, Kontext, Typ) → `llama-server --jinja`
  mit der Vorlage des Modells. Llama/Ministral: 0 `thinking`-Ereignisse,
  `reasoning_toggle = null`; Qwen3: `enable_thinking`, Thinking aus als
  Vorgabe. Kein globaler Qwen-Prompt, keine `/think`-Annahme.
- Abgelehnt werden Basis-/Vision-/Audio-/Embedding-Dateien als Chatmodell
  (Unit-Test `test_capabilities_and_type_classification`); Split-GGUF wird
  erkannt und vollständig verlangt (`test_split_detection`). Ein echter
  Split-Download wurde **NICHT VERIFIZIERT** (kein Split-Modell im 3-GB-Budget).
- Details und Zahlen: `MODEL_COMPATIBILITY.md`, Abschnitt „Ausgeführte Familien“.

### B. Empfehlungen

- `test_recommendations_with_simulated_profiles`: 8 GB ohne GPU, 16 GB
  ohne GPU, 32 GB mit 12 GB GPU-Speicher, FAT32-Datenträger, nicht passende
  27B-Datei → harte Ausschlüsse mit Begründung, höchstens 3 Picks, Vulkan nur
  mit erkannter GPU, geschätzte und gemessene Werte getrennt.
- Echter Rechner: Empfehlungen über die UI gegen den Mock geprüft (Screenshot
  `05-recommendations.png`), gegen den echten Kern über `GET /api/models/recommendations`
  im E2E-Lauf *(wird eingetragen)*.
- „Kurz messen“ (Kalibrierung) echt ausgeführt für Llama 1B (71,2 Token/s)
  und Ministral 3B (28,5 Token/s); die Werte landen in `model_profiles` je
  Hardware-Fingerabdruck und erscheinen als „gemessen“.

### C. Gedächtnis

Alle Fälle mit synthetischen Namen (Lena Beispiel/Mara Muster, Polarstern);
keine Sonderbehandlung von Namen im Code (Unit-Tests verwenden andere Namen
als die Echtläufe).

| Fall | Ergebnis |
|---|---|
| Alter Korrektur-Fehler reproduziert | Ja: Mit 0.6B und Llama 1B lieferte die Extraktion keine Kanten → der LLM-Richter lief nie → beide Fakten blieben „aktuell“; 0.6B nannte nach Neustart „Lena“, Llama 1B bei der Sofortfrage „Lena“ (Rohdaten in den Berichten vor den Fixes, WORKLOG 12:49) |
| Korrektur sofort sichtbar (Hintergrund offen) | **bestanden** (0.6B, Vulkan): Korrektur mit 2 offenen Nachverarbeitungen, Sofortfrage in neuem Gespräch → Quellen enthalten den neuen Fakt, der alte ist „ersetzt“, Antwort „Mara Muster“ |
| Neustart mit offener Warteschlange | **bestanden**: 4 offene Items beim Beenden, nach Neustart 0 offen, 0 Rohfakten übrig, Antwort „Mara Muster“ |
| Worker ohne Modell | Items warten, solange das Modell lädt (`starting`); ohne konfiguriertes Modell läuft die Volltext-Indizierung ohne Extraktion (Unit-Test) |
| Dokumente/Notizen beim Modellwechsel | unverändert aus V1 (Vektoren je Embedding-Modell und Dimension, Reindex bei Wechsel), nicht neu gemessen |
| Zustände in der UI | „Gespräch gespeichert“ (`saved`), „Gedächtnis aktualisiert“ (`postprocess_done`), „Nachverarbeitung ausstehend“ (`postprocess_pending`, Badge je Gespräch) – im Mock und über die Ereignisse des echten Kerns geprüft |

### D. UI und Lebenszyklus

- Aktivitätszeile: Reihenfolge `memory_search → prompt_build → prompt_processing → generating → answer_done → saved → postprocess_pending`, eine `request_id` je Antwort, monotone `seq` (Echtlauf-Assertion). Prozent nur bei `prompt_processing` (bekannte Größe aus `return_progress`).
- Stopp: `cancelled`-Ereignis nach dem Serverstopp; Stopp → `done` **0,03 s** (0.6B, Vulkan; Kernmessung mit vorab erzeugtem Client).
- Renderer: Token-Batching 40 ms; während eines 9-s-Mock-Streams mit 12-ms-Token-Takt und gleichzeitigem Tippen: 535 Frames, max. Lücke 33,6 ms, keine Lücke > 50 ms (Dev-Helfer `window.__spaiderPerf`).
- Lebenszyklus (Kern): Start, Modell bereit, Shutdown, keine Restprozesse – in jedem Echtlauf (`core exited with code 0`, Shutdown 2,4–2,7 s).
- Leerlauf: 30 s nach einer Antwort bei leerer Warteschlange, globaler Ereigniskanal offen, zwei `setup/status`-Abfragen: Kern 0,21 %, llama-server 0,21 % eines logischen Kerns, zusammen 0,42 % (`foldable/build/measurements/idle-load.json`). Keine anhaltende Hintergrundlast.

### E. Paket

*(wird eingetragen: neues ZIP, frischer Start, Offline, Vorher/Nachher)*

**CPU-Rückfall** (echt): siehe `test_real_gpu_fallback.py` oben – der Rückfall läuft in derselben Sitzung ohne Neustart; ein Modell, das auch auf der CPU nicht startet (kaputte Datei), markiert die GPU nicht als fehlerhaft (E2E-Fall „kaputt“).

Bereits ausgeführt – **Update an Bestandsdaten (Kopie)**: Der Nutzer-Datenordner
(5 Gespräche, 32 Nachrichten, 25 Knoten, 22 Kanten, 1 Modell, 13 Warteschlangen-
Einträge, Schema 1) wurde nach `measure/update-test` kopiert, mit dem 0.2-Kern
gestartet (Migration auf Schema 2), `PRAGMA quick_check = ok`, alle Zähler
unverändert; Original unberührt (`foldable/build/measurements/update-test.json`).
Das Original unter `foldable/build/out/SpAIder-Foldable-0.1.0-win-x64/SpAIder/data`
wurde nicht angefasst.
