# SpAIder Foldable – Einstieg für die Review

Stand: 2026-11-04, Branch `feature/spaider-foldable-review` (Basis: gemeinsamer
`main` bei `73762b3`, Tag `foldable-base-73762b3`). Diese Seite ist der kurze
Einstieg; die Details stehen in den verlinkten Dokumenten unter `docs/portable/`.

**Review-Status:** Draft zur gemeinsamen Prüfung, kein Merge-Auftrag. Der
Review-Build ist als Vorschau gekennzeichnet; offene Prüfungen sind unten und in
`AGENT_LIVE_TEST_REPORT.md` („NICHT VERIFIZIERT“) aufgeführt.

## 1. Welches Problem löst Foldable?

Ein lokal nutzbarer, mitnehmbarer SpAIder-Assistent: App, Modelle, gespeicherte
Gespräche, Dokumente und Wissensbestand liegen gemeinsam in **einem Ordner**
(z. B. auf einer USB-SSD). Keine Installation von Python, Node, Docker, Neo4j,
Redis oder Kafka beim Nutzer; nach dem einmaligen Modell-Download keine
Internetverbindung nötig.

## 2. Was ist jetzt vorhanden? (geprüft am Code und im Paket)

| Bereich | Stand | Beleg |
|---|---|---|
| Lokaler Chat mit Streaming, Stopp, Teilantworten | vorhanden | `TEST_REPORT.md`, `package-check-0.3.0.json` |
| Persistentes Gedächtnis (Fakten/Entitäten/Beziehungen, Dokumente, Korrekturen) | vorhanden | `TEST_REPORT.md`, `ARCHITECTURE.md` §2–3 |
| Modellverwaltung: Katalog mit gepinnten Revisionen und SHA-256, Import lokaler GGUF, Empfehlung nach Hardware | vorhanden | `MODEL_COMPATIBILITY.md`, `MODEL_RECOMMENDATIONS.md` |
| Ausführung über gebündeltes llama.cpp (b12032) auf CPU oder Vulkan mit automatischem Rückfall | vorhanden | `PERFORMANCE_REPORT.md`, `package-check-0.3.0.json` |
| Live-Fenster per globalem Hotkey: Vordergrundfenster vorgeschlagen, Quelle bestätigen, Frage mit UI-Struktur und Fensterbild (bei Bildmodell) | vorhanden | `AGENT_LIVE_TEST_REPORT.md` („Live und Oberfläche“) |
| Agent für freigegebene Aufgaben: Dateien (lesen/suchen/schreiben in freigegebenen Ordnern), eigener Browser (Chromium for Testing, separat geladen), Windows-Fenster über UI Automation, Bildwerkzeuge (beschreiben, Ziel finden) | vorhanden, mit Grenzen; zusammenhängender Lauf der 20 Testaufgaben × 3 mit dem Review-Code: 55/60 | `AGENT_LIVE_TEST_REPORT.md` (Aufgabenmatrix), `KNOWN_LIMITATIONS.md` |
| Gespeicherte Abläufe (Rezepte) aus belegten Läufen, Wiedergabe ohne Modellaufruf | vorhanden | A/B im Prüfbericht |
| Portabler Datenordner `data/` neben `SpAIder.exe`, Migration älterer Stände (Schema 1 → 3) | vorhanden; Zwei-PC-Test offen | `PORTABLE_AGENT_STATE.md`, `packaged-update-check-0.3.json` |

Nicht enthalten: Mikrofon/Sprache, MCP-Server, freie Shell, native Tool-Calls
der Modelle (der Planer nutzt Grammatik-JSON), macOS/Linux.

## 3. Was bedeutet „lernt“?

Foldable speichert Kontext: Gesprächsverlauf, aus Gesprächen und Dokumenten
extrahierte Fakten und Beziehungen (SQLite + Volltext + Vektoren), explizite
Korrekturen („Korrektur: …“ ersetzt den alten Fakt deterministisch) und auf
Wunsch bestätigte Abläufe des Agenten. **Die Modellgewichte ändern sich nicht.**
Der Abruf ist eine Suche (Volltext + Vektor, fusioniert) plus Kontexttext für
das Modell; es gibt keine Garantie, dass jedes Detail immer richtig abgerufen
oder formuliert wird. Was gespeichert wurde, ist in der Oberfläche unter
„Gedächtnis“ einsehbar und löschbar.

## 4. Wie hängt das mit SpAIder zusammen?

Foldable ist ein **zusätzlicher Client-/Laufzeitpfad** im Monorepo unter
`foldable/`; die Serveranwendung (`backend/`, `frontend/`, `cli/`, `sdk/`)
bleibt unverändert. Wiederverwendet wird `backend/app` als Bibliothek – nicht
der laufende Server:

| Aus SpAIder (`backend/app`) | Genutzt für |
|---|---|
| `services.parser_service` | Textextraktion beim Dokumentimport |
| `models.schemas` (Node, Edge, GraphPayload) | Datentypen des Gedächtnisses |
| `services.compressor.SemanticCompressor` (+ Ontologie, Prompt, Chunking) | Extraktion von Fakten/Beziehungen |
| `services.entity_resolver` | Deduplizierung von Entitäten |
| `services.supersession` (Gate + Judge) | Erkennen ersetzter Fakten |
| `services.query_service` (`_rrf_fuse`, Kontextaufbau) | Hybride Suche |
| `lib.litellm_retry`, `config.Settings` | LLM-Aufrufe der Services gegen den lokalen Server |

Neu und portabel sind die Adapter: SQLite-Gedächtnisspeicher statt Neo4j,
lokaler llama.cpp-Server statt Cloud-LLM, kein Redis/Kafka, FastAPI auf Loopback
mit Token statt Mehrbenutzer-API. **Nicht** alle SpAIder-Funktionen laufen
damit ohne Infrastruktur: Mehrbenutzer, Namespaces, Konnektoren und die
Next.js-Oberfläche sind nicht Teil von Foldable. Abgrenzung und zwei kleine
Eingriffe in wiederverwendete Services (privates Cache-Attribut des Kompressors,
nachgebildetes `_attach_fact_node`) stehen in `ARCHITECTURE.md` §2.

## 5. Wie ist es aufgebaut?

| Teil | Technik | Einstieg für die Code-Review |
|---|---|---|
| Launcher und Oberfläche | Electron 45, React, Vite | `foldable/desktop/src/main/index.ts` (Prozessstart, Token), `core.ts`, `hotkeys.ts`, `overlay.ts`, `tray.ts`; Renderer `foldable/desktop/src/renderer/App.tsx`, `OverlayApp.tsx` |
| Lokaler Kern | Python 3.12, FastAPI auf 127.0.0.1, PyInstaller-Bundle | `foldable/core/spaider_foldable/__main__.py`, `context.py` (Verdrahtung), `api/` (Router), `chat/service.py` |
| Inferenz | llama.cpp `llama-server` b12032, CPU + Vulkan, OpenAI-kompatibel | `foldable/core/spaider_foldable/inference/` (`manager.py`, `runtime.py`, `client.py`), `foldable/build/fetch_runtime.py` |
| Gedächtnis | SQLite + FTS5 + numpy, Migrationen | `foldable/core/spaider_foldable/memory/`, `db/` (`schema_v3.sql`, `migrations.py`) |
| Modelle und Projektoren | Katalog, Downloader, Registry, Fähigkeiten | `foldable/core/spaider_foldable/models/` (`catalog.json`, `registry.py`, `capabilities.py`) |
| Beobachtung | Win32/DWM, UI Automation (pywinauto/comtypes), Screenshots | `foldable/core/spaider_foldable/observe/` (`windows.py`, `uia.py`, `capture.py`, `snapshot.py`), `live/sessions.py` |
| Agent | Zustandsmaschine, Broker (Scopes, Validierung, Journal, Abbruch), Werkzeuge, Rezepte | `foldable/core/spaider_foldable/agent/` (`loop.py`, `broker.py`, `prompts.py`, `tools/{files,browser,windows,vision,memory}.py`) |
| Browsersteuerung | Playwright + gepinntes Chrome for Testing (Download im Datenordner) | `agent/tools/browser.py`, `agent/browserpkg.py` |
| Build und Paket | PyInstaller-Spec, Electron-Packager, ZIP + Prüfsummen | `foldable/build/spaider_foldable_core.spec`, `assemble_portable.py`, `verify_package.py` |
| Tests | pytest (Kern, 73 Tests + 6 Echtlauf-Tests), vitest (Desktop, 88 Tests), Aufgabenmatrix | `foldable/core/tests/`, `foldable/desktop/tests/`, `foldable/core/tests/matrix/` |

Vertrag zwischen Oberfläche und Kern: `API_CONTRACT.md`. Zustandsmodell und
Portabilität: `ARCHITECTURE.md` §1, `PORTABLE_AGENT_STATE.md`. Agent/Live im
Detail: `AGENT_LIVE_ARCHITECTURE.md`.

## 6. Was bleibt eingeschränkt?

- **Windows 11 x64** ist die einzige geprüfte Plattform; Hotkey, UI Automation
  und Aufnahme sind Windows-spezifisch.
- **Hardware**: Alle Messwerte stammen von einem Laptop (Intel Core Ultra 7 268V,
  32 GB, Intel Arc 140V über Vulkan). Andere GPUs sind nicht verifiziert; CPU-
  Betrieb ist deutlich langsamer (4B-Modell: erstes Token ≈ 9 s statt 0,3 s).
- **Modelle**: Geprüft sind Qwen3-VL-4B (Text, Bild, Grounding, Werkzeuge),
  Qwen3-4B (Text, Werkzeuge) und Gemma 4 E2B (Text, Bild; Werkzeuge schwächer).
  Andere Katalogeinträge sind nur nach Metadaten geprüft; beliebige Open-Source-
  Modelle werden nicht versprochen (`MODEL_COMPATIBILITY.md`).
- **Agent**: Kein Autopilot. Zuverlässig bei kleinen, klar umrissenen Aufgaben
  in freigegebenen Ordnern, Seiten und Fenstern; 4B-Modelle brauchen 5–10 s pro
  Planungsschritt und machen Fehler (Zahlen im Prüfbericht). Folgenreiche
  Aktionen werden über Wirkungsklasse und Namensfilter erkannt – konservativ,
  aber keine Garantie (`KNOWN_LIMITATIONS.md`).
- **Offene Prüfungen**: Zwei-PC-/USB-SSD-Umzug, gesperrter Desktop, andere GPUs,
  echte Netztrennung (bisher nur Netstat-Stichproben), erzwungener Absturz
  während einer Aktion, Overlay-Messung gegen das gepackte Paket, Migration
  echter 0.2-Daten (geprüft wurde eine 0.1-Kopie).
- **Standard-Hotkey** `Strg+Alt+Leertaste` kann belegt sein; die App meldet das,
  die Kombination ist in den Einstellungen änderbar.

## 7. Was sollst du beurteilen?

1. Verständlichkeit der Bedienung (Einrichtung, Modellwahl, Live-Fenster, Aufgaben).
2. Zuverlässigkeit des Gedächtnisses (Speichern, Korrektur, Abruf nach Neustart).
3. Qualität der Live-Hilfe (Antworten zum freigegebenen Fenster, Tempo, Bild vs. Struktur).
4. Grenzen der Computersteuerung (was der Agent sicher kann, wo er stoppt, Freigaben).
5. Einbindung in SpAIder (Wiederverwendung, Abgrenzung, Risiko für den Server-Code: keines, da `backend/` unverändert).
6. Wartbarkeit (Struktur, Tests, Vertrag, Build-Pipeline).

## 8. Testanleitung (ca. 45 Minuten, synthetische Daten)

Voraussetzungen: Windows 11 x64, ≈ 6 GB freier Platz (Paket + Modell), beim
ersten Start Internet für den Modell-Download.

1. **Review-ZIP laden**: Link und SHA-256 stehen in der PR-Beschreibung und im
   Abschnitt „Build-Zuordnung“ unten. Prüfsumme optional in PowerShell:
   `Get-FileHash .\SpAIder-Foldable-0.3.0-review-cd50a04-win-x64.zip -Algorithm SHA256`.
2. **Entpacken** in einen neuen, kurzen Pfad, z. B. `C:\SpAIderReview`
   (lange Pfade scheitern ohne Long-Path-Unterstützung). Vorhandene Foldable-
   Installationen oder `data/`-Ordner nicht überschreiben.
3. **Starten**: `C:\SpAIderReview\SpAIder\SpAIder.exe`. Die Einrichtungsseite
   zeigt Hardware, freien Platz und Empfehlungen.
4. **Modell wählen**: Für Bild- und Agentenfunktionen unter „Weitere Modelle“
   den Eintrag „Qwen3-VL 4B Instruct (Q4_K_M + Bild-Projektor Q8_0)“ laden und
   aktivieren (Katalog: 2 497 281 664 + 453 974 304 Bytes ≈ 2,95 GB; Gewichte und
   Projektor werden zusammen installiert). Reicht Grafikspeicher oder RAM nicht,
   nennt die Einrichtungsseite die Einschränkung; dann das dort empfohlene
   Textmodell (z. B. Qwen3 4B, ≈ 2,5 GB) nehmen – Live-Fragen laufen dann nur
   über die Fensterstruktur, Bildwerkzeuge fehlen. Andere Modelle nur gemäß
   `MODEL_COMPATIBILITY.md`.
5. **Gedächtnis**: Im Chat „Merke dir: Der Projektleiter von Atlas ist Max
   Weber.“ Dann „Korrektur: Der Projektleiter von Atlas ist jetzt Priya Nair.“
   Neues Gespräch öffnen und fragen „Wer leitet Atlas?“ (erwartet: Priya Nair,
   nicht Max Weber). App beenden, neu starten, erneut fragen.
6. **Live-Fenster**: Ein eigenes Testfenster öffnen (z. B. Editor mit ein paar
   Zeilen). Einstellungen → Live & Agent → Hotkey „Prüfen“; ist
   `Strg+Alt+Leertaste` belegt, z. B. `Strg+Alt+F9` speichern (so lief es auf
   dem Entwicklungsrechner). Testfenster in den Vordergrund, Hotkey drücken, nur
   diese Quelle bestätigen, fragen „Was steht hier?“. Mit `Esc` schließen.
7. **Datei-Aufgabe**: Einen eigenen Testordner anlegen (z. B.
   `C:\SpAIderReview\test\notizen.txt` mit einem Datum und einem Namen) und einen
   leeren Ausgabeordner. Unter „Aufgaben“: Ordner (lesen) und Ausgabeordner
   (schreiben) freigeben, Ziel „Lies notizen.txt und schreibe nach
   zusammenfassung.md nur Datum und Namen“. Fortschritt beobachten, Ergebnis
   öffnen und kontrollieren. Eine zweite Aufgabe starten und mit „Stopp“ bzw.
   `Strg+Alt+Rücktaste` abbrechen; danach darf keine Aktion mehr folgen.
8. **Optional Browser**: Unter „Aufgaben“ den eigenen Browser laden (Chrome for
   Testing 156.0.8078.4, 207 073 013 Bytes, landet in `data/browser/`). Eine
   lokale Testseite (z. B. ein HTML-Formular im Testordner) öffnen und ausfüllen
   lassen; der normale Browser bleibt unberührt.
9. **Portabilität** (vom Reviewer auszuführen, kein bestandener Nachweis): App
   über „Sicher beenden“ schließen, den gesamten Ordner `SpAIder` (inkl.
   `data/`) auf einen zweiten Windows-PC kopieren, dort `SpAIder.exe` starten
   und „Wer leitet Atlas?“ fragen.

**Feedback** bitte mit: erwartetes und tatsächliches Verhalten, Schritte, Modell,
Hardware/Backend (Einrichtungsseite), Review-Commit (BUILD_INFO.txt im Paket).
Keine privaten Inhalte in Screenshots oder Logs für den PR; die Logs liegen unter
`data/logs/`.

## 9. Build-Zuordnung des Review-Pakets

| Feld | Wert |
|---|---|
| Datei | `SpAIder-Foldable-0.3.0-review-cd50a04-win-x64.zip` |
| Größe / SHA-256 | 395 922 585 Bytes / `20b4096516796956b76b4b2503b29a258bbaf71de46ade6c2a04e1f28cd6b5ff` |
| Quell-Commit | `cd50a042046acee034e74383087851b58acd04f0` auf `feature/spaider-foldable-review` (Code identisch mit `f281d78`; Kern-Code zuletzt geändert in `371a503`, Katalogtext `34948d5`, Launcher `a85dca6`) |
| Gebaut am | 2026-11-04, Kern 12:48 (PyInstaller, Python 3.12), Launcher 12:41 (Electron 45.2.1), ZIP 12:52 |
| Runtime | llama.cpp b12032, CPU x64 + Vulkan x64 (SHA-256 gepinnt in `foldable/build/fetch_runtime.py`) |
| Browserpaket | nicht im ZIP; Chrome for Testing 156.0.8078.4 (207 073 013 Bytes) wird bei Bedarf in `data/browser/` geladen |
| Fundort | Release `foldable-review-0.3.0-cd50a04` im privaten Repository (Prerelease) – **erst nach dem Push**; bis dahin lokal unter `C:/Users/mucbay/spaider-foldable/repo-review/foldable/build/out/` |
| Manifest / Prüfsummen | `foldable/build/measurements/review-build-cd50a04.md`, `review-build-cd50a04-SHA256SUMS.txt` |

Kennzeichnung: **Review-Build**, Vorschau, nicht für den Produktivbetrieb. Das
frühere Paket `SpAIder-Foldable-0.3.0-win-x64.zip` (SHA-256 `87058242…73fa`,
Code `371a503`/`a85dca6`) ist nicht dieser Build.

Prüfung des frisch entpackten Review-Builds am 2026-11-04 (synthetische Daten,
PATH nur Windows, Modell aus lokalem Bestand, `review-package-check-cd50a04.json`;
parallel lief die Aufgabenmatrix auf derselben GPU, Zeiten daher pessimistisch):

| Prüfung | Ergebnis |
|---|---|
| ZIP-Inhalt | keine Modelle, kein Datenordner, Lizenzen vorhanden, `BUILD_INFO.txt` = `cd50a04` |
| Kern-Start / Modell (Vulkan) | 19.8 s bis bereit, Modell `ready` auf Vulkan |
| Chat (3 kurze Fragen) | erstes Token Median 0.64 s |
| Gedächtnis: „Merke dir … Max Weber“, „Korrektur … Priya Nair“, neues Gespräch | Antwort „Priya Nair“ (Fakten 3, Nachverarbeitung je ≈ 35 s) |
| Neustart des Kerns, neues Gespräch, Nachfrage | Antwort „Priya Nair“ (13.1 s bis bereit, Exit 0 vor dem Neustart) |
| Live (Bildschirmquelle): Snapshot, Strukturfrage, Bildfrage | Snapshot ok mit Vorschau; Strukturfrage erstes Token 0.56 s; Bildfrage 9.55 s; Sitzung beendet |
| Agent: Datei lesen → kickoff.md schreiben | `completed`, belegt, 3 Modellaufrufe, 45.2 s, Inhalt korrekt |
| Abbruch direkt nach Start | `cancelled` nach 3.01 s (Planungsaufruf lief), keine Aktion, keine Datei, keine laufende Aufgabe |
| Beenden | Exit 0, keine verbliebenen Kindprozesse, Lock entfernt |
| Netz | 65 Netstat-Stichproben, 0 Verbindungen zu Nicht-Loopback-Zielen (keine echte Netztrennung) |

## 10. Weiterführend

- Bedienung: `QUICKSTART_DE.md`
- Architektur: `ARCHITECTURE.md`, `AGENT_LIVE_ARCHITECTURE.md`, `API_CONTRACT.md`
- Prüfberichte: `TEST_REPORT.md` (0.1/0.2), `AGENT_LIVE_TEST_REPORT.md` (0.3),
  `PERFORMANCE_REPORT.md`, `AGENT_LIVE_PERFORMANCE.md`
- Modelle: `MODEL_COMPATIBILITY.md`, `MODEL_RECOMMENDATIONS.md`
- Grenzen: `KNOWN_LIMITATIONS.md`
- Integration und Herkunft: `INTEGRATION_HANDOFF.md`, `WORKLOG.md`, `PR_DRAFT.md`
- Rohdaten der Messungen: `foldable/build/measurements/` (synthetische Inhalte)
