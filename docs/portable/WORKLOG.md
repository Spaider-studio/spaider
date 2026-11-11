# SpAIder Foldable: Worklog

Fortlaufendes Arbeitsprotokoll. Neueste Einträge unten. Zeiten in lokaler Zeit (Europe/Berlin).

## Ausgangslage (2026-11-02, 00:30)

- Original-Repository: `https://github.com/Spaider-studio/spaider`, Branch `main`,
  Ausgangscommit `73762b375f1b5e39768fd2002e4b3198d1e7d6f2` (v0.4.0, 2026-09-30).
  Lokal als Tag `foldable-base-73762b3` markiert.
- Privates Ziel: `https://github.com/Useebay/spaider-foldable-private`
  (angelegt 2026-11-02, privat, kein Fork, Owner Useebay, GitHub Actions auf
  Repository-Ebene deaktiviert, Dependabot-Konfiguration nur auf der Sandbox-`main`
  entfernt).
- Getrennter Checkout: `C:\Users\mucbay\spaider-foldable\repo` (frischer Clone mit
  voller Historie). `origin` = privates Repo, `upstream` = Original mit deaktivierter
  Push-URL (`DISABLED_READ_ONLY`).
- Branches: `main` = Basis + Sandbox-Commit (`SANDBOX.md`, Dependabot entfernt).
  `feature/spaider-foldable-v1` = direkt vom Basis-Commit, nur Produktänderungen.
- Maschine: Windows 11 Enterprise 10.0.26200 x64, Intel Core Ultra 7 268V (8 Threads),
  32 GB RAM, Intel Arc 140V (iGPU), 288 GB frei auf C:. Kein Admin, kein MSVC/CMake/
  Rust, Docker-Engine nicht nutzbar. Node 24.21 (fnm), pnpm 12.4, Python 3.12.10,
  git 2.55, gh 2.102.
- SpAIder-Repo hat kein CLAUDE.md/AGENTS.md. Konventionen aus CONTRIBUTING.md:
  Conventional Commits, Ruff (E,F,W,I, 120 Zeichen), Tests mit pytest, Frontend
  `npm run lint`. Lizenz: Core AGPL-3.0, CLI/SDK Apache-2.0.

## Entscheidungen

- D1 (Runtime): llama.cpp als gebündelte Inferenzlaufzeit, Release-Build `b12032`
  (2026-11-01, CPU x64, `llama-b12032-bin-win-cpu-x64.zip`,
  SHA-256 `5296f5d18bb7d422c7c26f4bb9bc796d9d4f41e1312a8c296cff475bdc60d0d4`),
  gestartet als `llama-server.exe` auf Loopback. Grund: keine Compiler auf dem
  Build-Rechner, offizielle vorgebaute Binaries, OpenAI-kompatible HTTP-API, feste
  Buildnummer. Unterstützte Architekturen laut `src/llama-arch.cpp` des Builds:
  u. a. `qwen3`, `qwen3moe`, `qwen35`, `qwen4exp`, `gemma4`, `nomic-bert`.
- D2 (Core-Sprache): Python-Kern als eigenes Paket `foldable/core` (spaider_foldable),
  importiert `backend/app` als Bibliothek, gebündelt mit PyInstaller (onedir).
  Grund: maximale Wiederverwendung der SpAIder-Services (Parser, Schemas,
  Ontologie, Kompressor-Prompt und -Parser, Supersession-Judge, Entity-Resolver,
  Kontextformatierung) ohne Neubau; keine nativen Builds nötig.
- D3 (Speicher): SQLite (WAL, FTS5, Migrationen, Journal/Lock) im portablen
  Datenordner. Neo4j/Kafka/Redis/Postgres/ClickHouse werden im portablen Modus
  nicht gestartet und nicht importiert; benötigte Funktionen stehen hinter lokalen
  Adaptern.
- D4 (Fenster): Electron mit gebündelter React/Vite-Oberfläche, Renderer-Isolation,
  minimale IPC. Grund: vom Auftrag vorgeschlagener Standard, keine WebView2-
  Abhängigkeit zum Host. Paketierung über @electron/packager + ZIP (kein
  Self-Extractor, keine Installation).
- D5 (Modelle): Hugging Face Hub als Bezugsquelle, Katalog mit festen Revisionen und
  SHA-256 aus der HF-API. Smoke-Test-Modell: `Qwen/Qwen3-0.6B-GGUF`
  (Qwen3-0.6B-Q8_0.gguf, 639 MB, Apache-2.0). Embedding-Modell (optional):
  `Qwen/Qwen3-Embedding-0.6B-GGUF` (Q8_0, 639 MB, Apache-2.0).
- D6 (Qwen3.8): `Qwen/Qwen3.8-27B` existiert (Apache-2.0, model_type `qwen3_5`,
  Architektur `qwen35` in llama.cpp), aber Qwen veröffentlicht dafür kein eigenes
  GGUF; nur Community-Konvertierungen (z. B. unsloth). Wird im Katalog nur mit
  Kennzeichnung „Community-Konvertierung, dokumentierte Unterstützung, nicht
  ausgeführt“ geführt. `Qwen/Qwen3.8-Flash-Next` hat Lizenz `other` und wird in V1
  nicht aufgenommen.

## Blocker

- B1 (2026-11-02 00:50): Push ins private Repo abgelehnt. Das gh-Token hat die Scopes
  `repo, read:org, gist`, aber keinen Scope `workflow`. GitHub verweigert jeden Push
  von Commits, die `.github/workflows/*.yml` enthalten. Die Original-Historie enthält
  diese Dateien in jedem Commit; ein Umschreiben der Historie ist nicht erlaubt.
  Lösung durch Musa: einmalig `gh auth refresh -h github.com -s workflow` ausführen
  (Browser-Login), danach `git push -u origin main feature/spaider-foldable-v1
  foldable-base-73762b3`. Bis dahin: lokale Commits und Git-Bundles unter
  `C:\Users\mucbay\spaider-foldable\backup\`. Push wird bei jedem Meilenstein erneut
  versucht.

## Schritte

- [x] A1 Vorprüfung Maschine/Tools/Repo/Account (00:30)
- [x] A2 Privates Repo angelegt, Actions deaktiviert, Remotes/Branches/Tag (00:45)
- [ ] A3 Erster Push (blockiert, siehe B1)
- [x] A4 Projektskelett, API-Vertrag, Worklog
- [x] B Portable Pfade, SQLite, Migrationen, Lock (01:00)
- [x] C Reales Modell lokal + Chat-Streaming (01:10: Qwen3-0.6B Q8_0, 41 tok/s CPU, Stop, Embeddings 1024-dim)
- [x] D Verläufe, Dateiimport (TXT/MD/PDF), FTS+Vektor-Retrieval, Erinnerungen, Supersession (01:25)
- [x] E Modellverwaltung (Katalog, Download mit Resume+SHA-256, Import, Registry) + Aktivierung mit Rollback (01:25)
- [x] F Windows-Paket (PyInstaller-Kern 315 MB, Electron-Launcher, ZIP 307 MB), E2E-Abnahmelauf und Offline-Lauf gruen (09:55)
- [x] G Doku (TEST_REPORT), finaler Test-Build, Uebergabe (10:15)

## Verlauf

- 01:25 Commit `feat(foldable): portable local SpAIder core …` auf
  `feature/spaider-foldable-v1` (nur lokal, Push blockiert, siehe B1).
  Unit-Tests: 33 bestanden. Erster E2E-Lauf: Chat, Gedächtnis, Vektoren ok;
  Import-Job scheiterte an `call_soon_threadsafe(**kwargs)` (behoben).
- 01:40 Beobachtung aus dem E2E-Lauf: 0.6B-Modell kopiert Beispieltext aus dem
  Extraktionsprompt („Electric vehicle manufacturer“). Guard eingebaut
  (`guard_against_prompt_leakage`): nicht durch den Eingabetext gestützte
  Attribute werden verworfen und als „unsicher“ markiert. Supersession
  markierte nur die Kante, nicht den FACT-Knoten (kein wörtliches Zitat);
  Adapter-Erweiterung: FACT-Knoten mit beiden Endpunkten und ≥80 % Wortüberlappung
  werden mitmarkiert. Unit-Tests: 35 bestanden.
- 01:45 PyInstaller-Build (onedir, 312 MB) läuft; erster Versuch scheiterte am
  relativen Import in `__main__` (eigener Entry `build/core_entry.py`).
- 01:50 Desktop-Agent (Electron + React) hat Renderer, Main, Preload, Mock-Core
  und Tests angelegt; Typecheck/Packaging laufen.

- 09:30-09:55 Frozen-Kern: neo4j musste im Bundle bleiben (query_service
  importiert cognitive_engine), Paketdaten explizit in der Spec. Start mit
  leerem PATH ok (7,7 s). Runtime-Stopp von 17 s auf 2 s (CTRL_BREAK kommt ohne
  Konsole nicht an; TerminateProcess ist fuer llama-server sicher).
- 09:55 E2E-Abnahmelauf bestanden (5:24): Aktivierung, Chat, Gedaechtnis ueber
  Chats, Rollback bei defektem Modell, Stopp, Shutdown, Neustart, Dokument
  nach Loeschen des Originals, Umzug auf Z: (subst) mit Leerzeichen/Umlauten.
  Offline-Lauf bestanden (2:44): 119 netstat-Stichproben ohne externe
  Verbindung, Download bricht offline sofort ab (paused, fortsetzbar).
  Beobachtung: nach dem Umzug nannte das 0.6B-Modell den alten Projektleiter
  (Judge sprang in diesem Lauf nicht an). Massnahme: Fakten tragen jetzt
  Speicherzeitpunkt, neueste zuerst, Prompt-Regel "neuere Angabe gilt".
- 10:00 Desktop-Agent fertig: Lint, Typecheck, 34 Vitest, Build, Packaging
  (rcedit ohne Admin ok). Erkenntnis: ELECTRON_RUN_AS_NODE=1 in Agent-/
  VS-Code-Terminals laesst jede Electron-Binary sofort als Node enden.
- 10:05 Paket zusammengebaut und aus dem ZIP verifiziert (verify_package.py):
  Layout, Kern-Start mit leerem PATH 8,4 s, Token-Pflicht, Shutdown, Lock weg.

- 10:08 Finaler E2E-Lauf bestanden (6:47, parallel zu Desktop-Tests). Chat B
  nennt Priya Nair korrekt; Chat C nach dem Umzug nennt trotz Zeitstempel den
  alten Namen (0.6B, Temperatur 0,7) -> in KNOWN_LIMITATIONS und TEST_REPORT.
- 10:12 Finales Paket aus Commit 424377c: SpAIder-Foldable-0.1.0-win-x64.zip,
  321 949 175 Bytes, SHA-256
  5c5b06b45ba6603fc769e9730411299ae165811cc3938b4d458cc4da942dc36d,
  verify_package.py bestanden (Kern 8,1 s, Screenshot des Setup-Bildschirms).
- Push weiterhin blockiert (B1). Git-Bundle unter backup/ aktualisiert.

Wiederaufnahmepunkt: Branch feature/spaider-foldable-v1 lokal vollstaendig.
Offen: (1) Musa gewaehrt `workflow`-Scope und pusht (siehe
INTEGRATION_HANDOFF.md), (2) Zwei-PC-Test mit USB-SSD (TEST_REPORT.md,
Abschnitt „Nicht verifiziert“), (3) Integrationsentscheidung mit Cofounder.


---

## Folgeauftrag: Performance, Live-Aktivitaet, offene Modelle (2026-11-02, ab 11:40)

Branch `feature/spaider-foldable-performance-models` (von acc3ce1). Push weiterhin
blockiert (Scope `workflow` fehlt; unveraendert).

### Bestandsaufnahme
- Nutzerlauf unter `foldable/build/out/SpAIder-Foldable-0.1.0-win-x64/SpAIder/data`
  mit Modell `qwen3-4b-q4_k_m` (Qwen/Qwen3-4B-GGUF Q4_K_M, SHA verifiziert), 5 Gespraeche,
  32 Nachrichten, 14 Fakten, 0 Ersetzungen, 2 offene Nachverarbeitungen. Dieser
  Ordner wird nicht angefasst (assemble_portable.py verweigert jetzt das
  Ueberschreiben genutzter Ordner; neue Version 0.2.0 in eigenem Ordner).
- Logs des Nutzerlaufs (nur Zeiten, kein Inhalt): TTFT 0,4 s bis 85 s. Ursache der
  Ausreisser: Gedaechtnis-Extraktion (bis 4000 Token JSON + Richter) blockiert den
  einzigen Server-Slot; Prompt-Eval 60 Token/s, Generierung 7 bis 10 Token/s (4B, CPU).
- Vulkan-Variante b12032 erkennt Intel Arc 140V (17,6 GB frei); 0.6B: 64 Token/s
  (GPU) vs 26,5 (CPU), TTFT 0,26 s vs 0,44 s, Laden 6,1 s vs 2,1 s.

### Umgesetzt (Commit c275be3)
- Aktivitaetsereignisse (ACTIVITY_EVENTS.md), globaler Kanal /api/activity,
  Timings je Nachricht.
- LLM-Planer: Chat verdraengt Hintergrundarbeit (Item zurueck in die Warteschlange),
  Cooldown, Duty-Cycle je Modus (auto/quiet/fast).
- Sofort sichtbare Fakten: Nutzertext wird synchron als roher FACT gespeichert,
  Extraktion reichert denselben Knoten an; Quellen tragen `processing`.
- Prompt-Layout cache-stabil (Gedaechtnisblock in der letzten Nutzernachricht),
  cache_prompt + --cache-reuse 256, return_progress -> Prozent fuer Kontextverarbeitung.
- Hardwareprofil, Laufzeitoptionen (auto/quiet/fast, GPU-Schichten, CPU-Rueckfall),
  Vulkan-Runtime gepinnt und gebuendelt.
- Katalog v2 mit 17 Eintraegen (Qwen3/3.5/3.8, SmolLM3, Llama 3.2, Ministral 3,
  Phi-4-mini, Gemma 3/4, Embeddings), Suche/Resolve mit Remote-GGUF-Header,
  Empfehlungen (3 Picks), Kalibrierungsjob, Messprofile je Hardware.
- 49 Unit-Tests gruen. UI-Agent arbeitet an Aktivitaetszeile, Modellseiten, Mock.

### Laufend / offen
- Baseline-Messung (measure.py, Worktree repo-baseline @ acc3ce1) mit Qwen3-4B CPU.
- Downloads fuer Familientests: Llama 3.2 1B Q4_K_M, Ministral 3 3B Q4_K_M (2,96 GB).
- Danach: Nachher-Messung, reale Tests (Aktivitaet/Sofortkorrektur/Neustart, andere
  Familien, Vulkan im App-Pfad), Paket 0.2.0, PERFORMANCE_REPORT, TEST_REPORT.

### Fortschritt ab 12:30
- 12:31 Familienmodelle geladen (SHA verifiziert): Llama 3.2 1B Q4_K_M (808 MB),
  Ministral 3 3B Q4_K_M (2,15 GB); Testmodelle liegen ausserhalb des Repos.
- 12:40 Befund aus test_real_activity (Vulkan, 0.6B): nach Neustart verarbeitete
  der Hintergrund-Worker offene Items OHNE LLM, waehrend das Modell noch lud
  (Fakten galten dann als "extrahiert" ohne Extraktion). Fix: Worker wartet bei
  Zustand "starting"; Unit-Test. Zweiter Befund: der Test haengte sich an
  Keep-alive-Zeilen des globalen Kanals auf (Testfehler, behoben).
- 12:48 UI-Agent fertig (Typecheck, Lint, 59 Vitest, Build, Paket 0.2.0, Screenshots
  gegen Mock). Commit 1b8d444.
- 12:49 Familien-Echtlauf (test_real_families.py, Vulkan): Llama 3.2 1B und Ministral
  3 3B: Import (Weg C) -> Aktivierung -> Fakt/Korrektur/Sofortfrage -> Stopp ->
  Kalibrierung, alles ohne Familiencode. Llama 1B nannte bei der Sofortfrage den
  alten Namen, Ministral den neuen; 0.6B nannte nach Neustart den alten Namen.
  Ursache: ohne extrahierte Kanten greift der LLM-Richter nie; beide Fakten
  bleiben "aktuell". Fix: deterministische Korrekturschicht
  (memory/corrections.py: Korrekturmarker + Wortueberlappung, sofort beim
  Rohfakt wirksam, idempotent in der Extraktion) + reine Fragen werden nicht als
  Fakten gespeichert. Unit-Tests.
- 12:55 Messbefund: httpx.AsyncClient-Erzeugung pro Aufruf kostet ~400 ms auf
  diesem Rechner (TLS-Kontext). Betraf tokenize (prompt_build 406 ms), jede
  Chat-Verbindung, Embeddings, Health-Proben und den Stopp-Client der Tests.
  Fix: ein Client je Serverprozess (verify=False, Loopback), Harness ebenso.
  Ergebnis (0.6B, Vulkan): prompt_build 0 ms, erstes Token 219 ms statt 593 ms,
  Stopp -> done 0,03 s statt 0,44 s; Korrektur nach Neustart: "Mara Muster".
- Baseline (alter Code, Qwen3-4B CPU) laeuft seit 12:04 weiter; jede Nachverarbeitung
  blockiert dort 3 x 120 s (Extraktions-Timeouts), daher sehr lange Drain-Zeiten.

### Fortschritt ab 13:00
- 13:01 test_real_activity gruen (0.6B Vulkan): erstes Token 217 ms, Stopp 0,03 s,
  Sofortfrage und Frage nach Neustart "Mara Muster". Commit 757bce6.
- 13:06 test_real_e2e deckte auf: v1-"status"-Ereignisse fehlten im neuen Stream
  (wiederhergestellt); GPU-Fehlerflag wurde auch bei kaputter Modelldatei gesetzt
  (jetzt nur, wenn derselbe Start auf CPU gelingt); der Test zaehlte fremde
  llama-server-Prozesse (Baseline) mit (auf eigene Kindprozesse beschraenkt).
- 13:10 Leerlaufmessung: Kern 0,21 %, llama-server 0,21 % eines Kerns (idle-load.json).
  GPU-Rueckfalltest (Stellvertreter-Binary scheitert, CPU uebernimmt) gruen. Commit 674230f.
- 13:12 Offline-Test gruen (57 s), E2E gruen (1:48 min, Bericht e2e-report-0.2.json:
  erstes Token 172 ms Server / 0,27 s Client, 65,9 Token/s, Prompt 3165 Token/s,
  Stopp 0,33 s, Kernstart 10,1 s unter Parallellast).
- 13:15 E2E zeigte: mehrsaetziger Fakt ("Projekt heisst Orion. Leiter ist X. Budget Y.")
  wurde von der Korrektur nicht ersetzt (Wortueberlappung ueber den ganzen Text zu
  gering), und die alte Fassung der Korrekturschicht ersetzte auch verwandte, nicht
  widersprechende Fakten (neuer Wert und Marker zaehlten als Ueberlappung). Neu:
  Thema/Wert-Zerlegung, satzweise Pruefung, unberuehrte Saetze als abgeleiteter Fakt.
  52 Unit-Tests gruen.
- Baseline (alter Code) seit 12:04: Warm-Phase mit ~60 s TTFT je Antwort, weil die
  Extraktion den einzigen Slot haelt; Ende voraussichtlich ~13:45.

- 13:22 Baseline-Messprozess abgebrochen (78 min; Warm-Phase: jede Antwort ~10 min
  hinter Extraktionsanfragen im einzigen Slot). Zahlen aus Nachrichten-/Server-Logs
  rekonstruiert (baseline-qwen3-4b-cpu-partial.json): TTFT kalt 5,7/17,9 s, unter
  Hintergrundlast 47-86 s (Median 64,6 s), Drain nie < 900 s, 140 LLM-Timeouts.
- 13:24 E2E-Rerun gruen (1:55): mehrsaetziger Fakt korrekt aufgeteilt, Abruf ueber
  Gespraeche nennt neuen Leiter + Budget. Commit ccfbf9d/e5bdf64/dfe1f8a.
- 13:26 PyInstaller-Kern 0.2 gebaut (6 min). Echte Empfehlungen ohne Modell abgefragt
  (recommendations-real.json).
- 13:26-14:24 Nachher-Messung CPU (after-qwen3-4b-cpu): Fakten bei laufender
  Nachverarbeitung 5,2-8,2 s TTFT (Baseline 47-86 s), warm kurz 8,4-10,8 s (63,6 s),
  Stopp -> done 0,11 s, Drain 17-62 s je Nachricht (758 s nach 1500-Token-Eingabe).
  Direkt: Cache-Treffer 0,09 s, kalt 6,1/17,1 s (CPU-Promptverarbeitung 50-75 Tok/s).
  Ziel 1-2 s auf CPU mit 4B NICHT erreicht (Promptverarbeitung), Befund: Abruf-Frage
  mit 5200 Prompt-Tokens, weil lange Eingaben als Fakten voll im Kontextblock landen
  -> Fakten im Kontextblock auf 480 Zeichen gekuerzt (nach der CPU-Messung).
- 14:25 Vulkan-Nachher-Messung gestartet (after-qwen3-4b-vulkan).

- 14:25-15:00 Vulkan-Nachher-Messung (after-qwen3-4b-vulkan): kalt kurz 0,45 s, lang
  2,1 s, Fakten 1,0-1,9 s, warm kurz 1,2-1,3 s, lange Verlaeufe 4-8 s, Abruf 7,5 s
  (5 300 Prompt-Tokens), 20-31 Token/s, Stopp -> done 0,06 s, Drain 7 s / 484 s /
  18-32 s. PERFORMANCE_REPORT mit Zielbewertung und Engpaessen, Commit 0f25a4b.
- 15:01-15:46 Zusatzmessung mit gekuerztem Kontextblock (nur App-Pfad): Abruf
  4 600 statt 5 400 Prompt-Tokens; Lauf insgesamt 20-30 % langsamer (Taktschwankung,
  dokumentiert).
- 15:47 PyInstaller-Kern erneut gebaut (retrieve.py-Aenderung enthalten).

- 15:55 Paket SpAIder-Foldable-0.2.0-win-x64.zip (353 874 880 Bytes, SHA-256
  75aa4efa25c93f67e8cabb4e0294db605fcc86bc8f0deedb27e88401f79ec98c, Commit f3b22d3);
  0.1.0-ZIP und Nutzerordner unveraendert. verify_package: Layout ok, Kern 8,1 s,
  needs_model, Screenshot des Einrichtungsbildschirms mit echten Empfehlungen.
- 15:58 Gepackter Kern auf der migrierten Kopie der Nutzerdaten: bereit 4.42 s, 4B auf
  Vulkan, Gedaechtnis intakt, Testfrage erstes Token 0.719 s, Exit 0.
- Push erneut abgelehnt (workflow-Scope). Bundle unter backup/ aktualisiert.

Wiederaufnahmepunkt: Branch feature/spaider-foldable-performance-models lokal
vollstaendig; Paket 0.2.0 gebaut und geprueft. Offen: (1) Push nach
`gh auth refresh -h github.com -s workflow`, (2) Zwei-PC-Test, (3) Vorschlaege aus
PERFORMANCE_REPORT "Verbleibende Engpaesse" (Verlauf wie gesendet cachen, zweiter
Server-Slot, Extraktionslimit fuer lange Eingaben), (4) Integrationsentscheidung.


---

## Folgeauftrag: lokaler Agent, Live-Bildschirmhilfe (2026-11-03 23:35 bis …)

Branch `feature/spaider-foldable-agent-live` (von debc9be). Sicherung vorab:
`backup/spaider-foldable-pre-agent-20261103.bundle` (verifiziert). Push weiterhin
blockiert (workflow-Scope).

### Bestandsaufnahme (23:38)
- Desktop interaktiv (Input-Desktop „Default“), 3 Monitore (1920x1080 x2, 1920x1200 @125 %),
  Bildschirm-/Fensteraufnahme und UIA (pywinauto 0.6.9) funktionieren.
- llama-server b12032 kann `--mmproj`, `--mmproj-offload`, `--image-max-tokens`, `--json-schema`,
  `--parallel`. Dev-Abhängigkeiten installiert: pywinauto, pywin32, mss, playwright 1.64.1
  (Chromium 1248 „Chrome for Testing 156“, 437 MB, unter dev-app/browser).
- Vision-Kandidaten (offiziell/ungated, Apache-2.0): Qwen/Qwen3-VL-4B-Instruct-GGUF (Q4_K_M 2,33 GiB +
  mmproj Q8_0 0,42 GiB) und ggml-org/gemma-4-E2B-it-GGUF (Q4_0 2,65 GiB + mmproj 0,52 GiB). Beide
  geladen und SHA-geprüft (dev-app/data/downloads/vision, 5,9 GiB von 10 GiB Budget).

### Umgesetzt (Commits 5adf805, 69ff738, 30aaf71)
- API-Vertrag v1.2 (Live, Agent, Overlay-Bridge) in API_CONTRACT.md; Desktop-Agent arbeitet gegen Mock.
- observe/: Fenster mit Prozessidentität, Monitore/DPI, PrintWindow/mss-Aufnahme, UIA auf eigenem
  COM-Thread mit Zeitbudget, Snapshots mit Transformation + Bildhash. Gemessen: Fensteraufnahme 46 ms,
  UIA-Lesen 360 ms kalt / 63 ms warm (Notepad, 28 Elemente), Snapshot 156 ms.
- live/: Sitzungen (eine Quelle), Snapshot auf Abruf, Frage mit Struktur + optional Bild über den
  bestehenden Chat (observation-Ereignis, Phasen capture/observe/image_encode).
- Vision: Projektor-Paarung in Registry/Katalog (kind=mmproj), Runtime-Flags, Fähigkeiten
  vision_input/tool_call_format/image_grounding. Messung Qwen3-VL-4B (vlm-smoke-qwen3vl4b.json):
  Vulkan+Projektor-Offload erstes Token 4,7 s bei 915 Prompt-Tokens, 28 Token/s; ohne Offload 11,5 s;
  CPU 21,4 s / 16,5 Token/s. Grounding liefert normierte 0-1000-Koordinaten (kalibriert).
- agent/: Schema v3 (tasks/actions/recipes), Broker (Scopes, Schema-Prüfung, Frische, Freigaben,
  Abbruch-Generation), Runner (Zustandsmaschine, Budgets, Stagnation, Fragen, Artefakte, Rezepte),
  Werkzeuge files/memory/windows/vision/browser, Routen /api/agent/*. Scheduler: Agentenschritte
  warten auf laufende Nutzerantworten.
- 57 Unit-Tests grün (Agent: Dateiaufgabe mit skriptiertem Modell, Scope-Ausbrüche, Freigabe, Abbruch).

### Laufend
- Echtlauf Dateiaufgabe mit Qwen3-4B über die API; Adapter-Smoke (WinForms-Testapp, lokales Formular).
- Desktop-Agent: Overlay, Hotkeys, Tray, Aufgabenpanel.

### Fortschritt 00:10–00:40 (2026-11-04)
- Echtläufe: Live-Frage mit Qwen3-VL auf der WinForms-Testapp (erstes Token 2,8 s, Aufnahme
  94 ms, Antwort korrekt; test_real_live.py). Adapter-Smoke: UIA set_value/invoke + Datei
  geschrieben, Browser (Playwright) Formular befuellt/bestaetigt, private Hosts gesperrt,
  veraltete Refs abgelehnt (tools-smoke.json).
- Agent mit Qwen3-4B: Dateiaufgabe 6 Schritte/46 s, Ausgabe belegt (agent-file-task-qwen3-4b.json).
  Befunde und Fixes: Modell fragte nach bereits erlaubtem Lesen (jetzt deterministisch bejaht,
  Scopes im Prompt, Beispiel), meldete Erfolg ohne Aktion (jetzt abgelehnt), Browser-Refs
  verwechselt (jetzt Muster ^b\d+$ in Schema/Grammatik, Objekte statt Zeilen). Browseraufgabe:
  Formular korrekt gefuellt und abgeschickt; Abschluss-Erkennung ueber Seitentext nach Klick.
- Eigene Prozessfamilie korrekt erkannt (vorher wurden alle Geschwisterprozesse als "self"
  ausgeblendet). Browserpaket: Chrome for Testing 156.0.8078.4 (207 073 013 B, SHA 220021ec…)
  als Job installierbar; Downloader kennt CDN-Hosts und fertige .part-Dateien.
- PyInstaller-Build mit pywinauto/comtypes/mss/playwright: 4,3 min, 40 MB exe; comtypes-Cache
  im Datenordner. Frozen-Check und 20er-Aufgabenmatrix (Qwen3-VL-4B, 3 Wiederholungen) laufen.
- Docs: AGENT_LIVE_ARCHITECTURE.md, PORTABLE_AGENT_STATE.md, QUICKSTART/MODEL_COMPATIBILITY 0.3.

### Fortschritt 00:40–00:55
- Frozen-Check nach Rebuild gruen (UIA 311 ms, Browserpaket + Playwright im Bundle, Exit 0).
- Migration einer konsistenten Kopie der Nutzerdaten (Backup-API) auf Schema 3: ok, Zaehler
  unveraendert, Original unberuehrt (update-test-0.3.json). Live/Agent-API-Tests (ohne Modell)
  ergaenzt: 66 Unit-Tests gruen.
- Desktop-Agent fertig (Commit 614c916): Overlay, Hotkeys (Ctrl+Alt+Space hier belegt ->
  ehrlich gemeldet, Messung mit Ctrl+Alt+F9), Tray, Aufgabenpanel, Einstellungen, Mock v1.2,
  84 Vitest, Paket 0.3.0; Mock-Latenzen: Hotkey->show 12,3 ms, ->Paint 16,7 ms (Median).
- Zweiter Desktop-Agent misst jetzt gegen den echten Kern (Overlay-Frage mit Bildmodell,
  Stopp-Hotkey, Aufgabe aus der UI) und behebt zwei Luecken (Live-Sitzung beenden beim
  Schliessen, Agent-Aktivitaet im Zeitstrahl).
- Matrix Qwen3-VL-4B: Dateigruppe 13/15 (F4 zweimal ohne Dateinamen = Fehlerfolg),
  Browsergruppe laeuft.

### Fortschritt 01:00-02:00
- Matrix Qwen3-VL-4B (60 Laeufe) fertig: 34/60, 12 Fehlerfolge-Meldungen; Nachlauf der
  Fixture-Fixes (W3 3/3, W4 0/3, V2 0/3, V5 0/3) deckte vier Kernfehler auf: Scope-Verlust nach
  windows.launch, Browserseite der Voraufgabe als Beobachtung (B5/B2), Vordergrund-Sperre
  (SetForegroundWindow aus Hintergrundprozess), "bestaetigt" ohne Lesen. Alle behoben (ad42cac):
  Scope-Persistenz, Browser-Bindung an die oeffnende Aufgabe, bring_to_front mit
  AttachThreadInput/Modifier (10/10 Wechsel, 10-27 ms), Rueckweisung unbelegter done-Meldungen,
  Plausibilitaet der Koordinatenkonvention, click_at meldet Veraenderung.
- Rezepte wurden nie ausgefuehrt -> Rezept-Modus im Loop (f01a359): deterministische Wiedergabe
  ueber den Broker, Parameter {name}, Rueckfall auf den Planer; 70 Unit-Tests gruen.
- Desktop-Agent gegen echten Kern (a85dca6): Hotkey->Overlay 57 ms Median (n=24), Stopp-Hotkey
  3-4 ms, Bildfrage 6/6 korrekt (7,2 s erstes Token bei geteilter GPU), Aufgabe aus der UI 89,5 s
  belegt, Leerlauf 0 Anfragen; Befunde: Vorschau (max_elements 0 -> 422), Prozent > 100.
- Laufend: Kette Qwen3-4B (Text, F/B/W) -> Gemma 4 E2B Teilmenge -> finaler Nachlauf der
  korrigierten Aufgaben (W1,W4,V2,V5,B5,B2,B1 x3) -> A/B Rezept vs. Loop (B1). Desktop-Agent:
  30 An/Aus-Zyklen mit Handle-Zaehlung.

### Fortschritt 02:00-02:20
- Qwen3-4B-Teilmenge 22/30 (Dateien 10/10, Browser 8/10, Windows 4/10; W1 an Vordergrund-Sperre).
  Gemma 4 E2B Teilmenge 5/16 mit 51 (meist leeren) Rueckfragen -> leere ask_user werden verworfen
  (0a63eec); Katalog Qwen3-VL und Gemma auf "executed" mit ehrlichem Umfang.
- Kern mit f01a359 gebaut (PyInstaller 5,1 min), Paket 0.3.0 assembliert (377 MB, SHA 20462c91...),
  verify_package gruen (Pfad mit Umlaut/Leerzeichen, Kern 8,8 s, Token, Exit 0, Launcher-Screenshot).
  Hinweis: das Paket wird nach dem finalen Nachlauf mit dem Endstand neu gebaut (SHA im Abschluss).
- Laufend: finaler Nachlauf (W1,W4,V2,V5,B5,B2,B1 x3, Qwen3-VL), danach A/B Rezept vs. Loop,
  Paket-Volltest mit Modell (Netstat, Chat-Regression, Live, Agent, Abbruch, CPU-Rueckfall,
  Beenden), Gemma-Wiederholung mit Fix; Desktop-Agent: 30 An/Aus-Zyklen mit Handle-Zaehlung.

### Fortschritt 02:20-02:55
- Finaler Nachlauf (Kernfixes): B1 3/3, B5 3/3 (Injection-Seite), B2 2/3, W4 2/3, V2 3/3; V5 "0/3"
  entpuppte sich als Fixture-Encoding (PowerShell 5.1 liest .ps1 ohne BOM als ANSI -> Label
  "gedrÃ¼ckt"), W1 0/3 weil der Planer Feldnamen statt Element-IDs nutzt -> eindeutige Namen als
  Referenz akzeptiert (1d2bbd8), Fixtures mit BOM. Wiederholung W1/V5: 6/6.
- 30 Overlay-Zyklen gegen echten Kern: 47,8 ms Median, kein Leck (a85dca6-Stand, ui-cycles-real.json).
- A/B Rezept vs. Loop: Lauf 1 deckte Pfad-Schreibweise beim Templating auf (371a503), Lauf 2:
  Loop 3/3 in 71 s mit 9-10 Modellaufrufen vs. Rezept 3/3 in 0,4 s mit 0 Modellaufrufen.
- Kern mit 371a503 neu gebaut, Paket 0.3.0 neu assembliert (395 662 613 B, SHA 87058242...73fa),
  verify_package gruen. Laufend: Gemma-Wiederholung mit Leere-Rueckfragen-Fix (F1 2/2), danach
  Paket-Volltest mit Modell (Netstat, Chat-Regression, Live, Agent, Abbruch, CPU-Rueckfall, Beenden).

### Fortschritt 02:55-03:20
- Gemma-Wiederholung mit Endstand: 13/16 (vorher 5/16), 0 Fehlerfolge-Meldungen; Windows-Gruppe 1/4.
- Migration einer konsistenten Kopie mit dem gepackten 0.3.0-Kern auf subst-Laufwerk S: ok
  (Schema 1->3, quick_check ok, Zaehler unveraendert, Qwen3-4B auf Vulkan, Exit 0).
- Paket-Volltest (package_full_check.py): drei Harness-Anlaeufe (Importpfad, MAX_PATH im
  Scratch-Ordner, wmic fehlt auf 26200) -> Hinweis zur Pfadlaenge in QUICKSTART/KNOWN_LIMITATIONS;
  der durchlaufende Teil zeigte Chat-TTFT 0,27 s Median (0.2: 0,45 s), Live-Struktur 0,23 s, Bild
  4,9 s, Agentenaufgabe belegt in 21,5 s, Abbruch 10 ms, CPU-Rueckfall 1,8 s TTFT; Endlauf mit
  Netstat/Beenden folgt.

### Abschluss 03:20-03:35
- Paket-Volltest gruen (package-check-0.3.0.json): Chat-TTFT 0,25 s Median, Live 0,24 s / Bild 5,1 s,
  Agentenaufgabe belegt 19,7 s, Abbruch 20 ms, CPU-Rueckfall per gespeicherter Einstellung nach
  Neustart (Backend cpu, 9,1 s TTFT), Aufgabenzustaende ueber Neustart erhalten, sauberes Beenden,
  22 Netstat-Stichproben ohne externe Verbindung.
- Push-Versuch erwartungsgemaess abgelehnt (workflow-Scope). Bundle erstellt, Abschlussbericht.

Stand bei Uebergabe: Branch feature/spaider-foldable-agent-live, Paket 0.3.0 (SHA 87058242...73fa).
Offen fuer Musa: gh auth refresh -s workflow, dann push; erster Nutzertest laut QUICKSTART_DE.md.


## Review-Vorbereitung fuer den Mitgruender (2026-11-04, 12:15-)

- Bestandsaufnahme: Arbeitsbaum sauber bei f281d78; upstream/main unveraendert bei 73762b3;
  privates Repo leer; Token ohne workflow-Scope (gist, read:org, repo); gemeinsames Repo oeffentlich.
  -> Review-Weg B/C: lokaler Integrationsbranch feature/spaider-foldable-review (Worktree
  C:/Users/mucbay/spaider-foldable/repo-review) auf review/spaider-base-73762b3; Push/PR/Release
  erst nach `gh auth refresh -s workflow` (Befehle in INTEGRATION_HANDOFF.md).
- Diff seit Basis: nur foldable/, docs/portable/, .gitignore; keine .github-Aenderungen; der
  Sandbox-Commit 8f20d3a des privaten main ist nicht Teil des Branches.
- Review-Dokumente: REVIEW_START_HERE_DE.md, PR_BODY_REVIEW.md, Handoff-Nachtrag (cd50a04).
- Build aus cd50a04: Kern (PyInstaller, 12:48), Launcher (Electron 45.2.1, 12:41), Paket
  SpAIder-Foldable-0.3.0-review-cd50a04-win-x64.zip (395 922 585 B, SHA 20b40965...b5ff),
  verify_package gruen (Kern 10,5 s, Token, Exit 0, Launcher-Screenshot). Manifest:
  foldable/build/measurements/review-build-cd50a04.md.
- Pruefungen in dieser Runde: Kern pytest 73/6 uebersprungen, Desktop typecheck/lint/88 Tests gruen;
  60er-Matrix gegen den Review-Code und Paket-Volltest des Review-ZIP laufen (Ergebnis unten).
- 60er-Matrix zusammenhaengend gegen den Review-Code: 55/60 (F 12/15, B 15/15, W 14/15, V 14/15),
  4 Fehlerfolge-Meldungen, 0 Rueckfragen (matrix-qwen3-vl-4b-review-full.json).
- Paket-Volltest des Review-ZIP gruen: Gedaechtniskorrektur vor/nach Neustart, Live, Datei-Aufgabe,
  Abbruch, Beenden, 65 Netstat-Stichproben ohne externe Verbindung (review-package-check-cd50a04.json).
- Hinweis: Musas eigene SpAIder-Instanz lief waehrend der Pruefungen aus dem 0.3.0-Paketordner
  (seit 12:17) und wurde nicht beruehrt.
- UI-Test des Review-Builds (gepackt, eigener App-Ordner): alle Pruefungen bestanden; Aufgaben
  per API mit Formular-Scopes angelegt (Ordnerdialog nicht automatisierbar). Tag
  foldable-review-0.3.0-cd50a04 auf dem Build-Commit; Push/PR/Release warten auf den workflow-Scope.

## Modellbibliothek, Anbieter-Anbindung, portables Gedaechtnis (2026-11-11, Masterprompt)

### Phase 0 (10:00-10:45): Bestand, Sicherung, Quellen, Fehlerbefund
- Neuester Stand = Review-Branch 84f59c4 (Code f281d78). Neuer Branch
  feature/spaider-foldable-modelhub-connectors im Worktree C:/Users/mucbay/spaider-foldable/repo-modelhub;
  Bundle backup/spaider-foldable-pre-modelhub-20261111.bundle (verifiziert) vor Aenderungen.
- upstream/main ist jetzt 126ef75 (v0.5.0); Foldable bleibt auf Basis 73762b3 (Integrationsnotiz).
- Privates Repo weiterhin leer, Token ohne workflow-Scope -> am Ende genau ein Pushversuch.
- Gemeldeter Leerantwort-Fehler reproduziert aus den Logs der Nutzerinstanz: Qwen3.8-27B mit 36 %
  GPU-Offload -> llama-server ErrorOutOfDeviceMemory als SSE-Fehlerobjekt, vom Client ignoriert ->
  "Antwort fertig" ohne Text. Fix in Phase 1 (Fehler-Event + GPU-Rueckfall).
- Recherche in docs/portable/MODELHUB_RESEARCH.md (Abrufdatum 2026-11-11), ADR in
  MODELHUB_ARCHITECTURE.md. mcp 2.3.0 installiert (API MCPServer/Client, Protokoll 2026-07-28).
- Installierte Clients: Claude Code VS-Code-Erweiterung 2.1.322 (claude.exe), Codex-App 26.1103
  (~/.codex/config.toml, nicht veraendert), kein Claude Desktop, kein Ollama/LM Studio.

Naechster Schritt: Phase 1 - providers/ (Events, Adapter chat_completions/responses/anthropic,
lokaler Runtime-Adapter), llama-Fehlerpfad, ChatService auf Events, Testserver + Vertragstests.

### Phase 1 (10:45-12:10): Provider-Schicht, Fehlerpfad, Schema v4
- providers/ (Events, SSE-Reader, Adapter chat_completions/responses/anthropic_messages, Presets,
  Registry, LocalRuntimeAdapter), credentials (DPAPI + Tresor), connections/policy/projects,
  ChatService auf Events (leere Antwort/Stream-Fehler -> error mit next_action_de), Routen
  /providers /connections /credentials /projects /consents /conversations/{id}/binding.
  Commit 4f0176d; 110 Kern-Tests gruen (37 neue, davon Protokoll-Vertragstests gegen lokale
  Testserver fuer alle drei Wire-Protokolle: 401/403/429/5xx, JSON statt SSE, fragmentierter
  Stream, Tool-only, Reasoning-only, Abbruch, Verbindungsabbruch, unbekannte Events).
- OOM-Merker: nach ErrorOutOfDeviceMemory plant der naechste Start des Modells CPU.

### Phase 2 (12:10-): Katalog
- models/discover.py + Routen /models/discover, /models/repo/{a}/{n}, /models/card/{a}/{n}:
  Live-Check gegen den Hub: qwen3 (paginiert), GLM (zai-org offiziell), deepseek, gemma (gated
  "manual" sichtbar), mistral, Nicht-GGUF-Suche; Varianten mit Projektor-Zuordnung; Modellkarte.
- API_CONTRACT.md Addendum v1.3.

Naechster Schritt: UI-Brief an den Desktop-Agenten (Verbindungen, Modellbibliothek, Chat-Kopf,
Fehler/Freigabe-Dialog, Tresor), dann Profile (Phase 3) und MCP-Bruecke (Phase 4).

### Phase 3 (13:10-14:20): Portable Profile (ADR-5)
- profiles.py: Registry data/profiles.json, Profilordner data/profiles/<uuid>/ (DB, Modelle,
  Downloads, Importe, Cache, Tresor, lokale Kataloge); Host-Ebene bleibt geteilt (host/, logs/,
  electron/, connectors/, instance.lock). Alt-Layout 0.1-0.3 wird beim ersten Start per
  Journal (profiles.migrating.json) in das erste Profil verschoben - wiederaufnehmbar nach Absturz,
  Kollisionen werden geparkt statt gemischt.
- ProfileManager: Wechsel faehrt den aktiven AppContext herunter (Streams, Worker, Jobs,
  Modellprozesse, DB), baut den neuen auf, erhoeht die Generation (Alt-Ereignisse werden von
  Clients verworfen), Bus-Ereignis "profile". Eine Instanzsperre fuer den ganzen Prozess
  (idempotent fuer dasselbe Objekt; zweiter Start weiterhin abgewiesen).
- Routen /api/profiles (list/create/rename/delete/activate); ready-Zeile enthaelt profile_id.
  Commit 5493278; Tests test_profiles.py (Migration, Journal-Wiederaufnahme, Regeln, Pfade, Sperre).

### Phase 4 (14:20-16:05): MCP-Gedaechtnis-Connector (ADR-6)
- connectors/service.py: Connector = ein Client x ein Profil x Projekte x Rechte
  (read immer, write/forget nur explizit). Token wird einmal angezeigt, in der DB nur als
  SHA-256; die Bruecke liest ihre Kopie aus data/connectors/<id>.json. Autorisierung
  serverseitig (Profilbindung, Widerruf, Projektzuordnung ueber die Herkunft der Aussage:
  Gespraech/Dokument/Connector); Werkzeuge status, search_memory, get_source, remember,
  correct_memory, forget_memory; Schreibaufrufe idempotent per client_request_id
  (Tabelle connector_calls); forget markiert (kein physisches Loeschen); Treffer tragen
  den Hinweis "Daten, keine Anweisungen".
- Schema v5: ingest_queue akzeptiert kind 'fact' (Tabelle wird in der Migration neu aufgebaut,
  Zeilen bleiben); der Worker extrahiert Connector-Fakten im Hintergrund wie Gespraechsfakten.
- connectors/bridge.py: `spaider-foldable-core.exe --mcp-stdio --connector <datei>` =
  MCP-Server (mcp 2.3.0, stdio, strukturierte Ausgaben, Annotationen readOnly/destructive),
  leitet jeden Aufruf per Loopback-HTTP an den LAUFENDEN Kern weiter (nur dieser schreibt SQLite).
  Discovery ueber data/host/core.json (Port, PID; ohne Geheimnisse), beim Beenden entfernt;
  Kern-Neustart auf anderem Port braucht keine neue Client-Konfiguration. Logs nur stderr,
  Token nie geloggt.
- api/routes_connectors.py: Verwaltung unter App-Token (/api/connectors anlegen/listen/
  widerrufen/Setup-Schnipsel), RPC /api/connector-rpc/call mit Connector-Token, nur Loopback,
  Browser-Aufrufe (Origin) abgewiesen; App-Token-Middleware nimmt genau diesen Pfad aus.
  Setup-Schnipsel: Claude Desktop JSON, `claude mcp add`, Codex TOML, generisches stdio-JSON.
- Tests (tests/test_connectors.py, 8): Rechte, Projekttrennung zwischen zwei Connectoren,
  Idempotenz, Widerruf, falsches Profil, Loopback/Origin-Regeln, MCP-Werkzeugflaeche mit
  In-Memory-Client und ein echter Lauf stdio-Bruecke <-> echter Kern-Prozess (ohne Modell):
  list_tools, status, remember, search_memory, get_source, forget (korrekt verweigert),
  Fakt danach in der normalen UI-Suche sichtbar. Kern-Suite: 123 bestanden, 6 uebersprungen.
  Commit 24d1fa8.
- Echte Clients (scratchpad/client_check.py, isolierte Client-Konfigurationen, keine
  Aenderung an ~/.claude.json oder ~/.codex/config.toml, kein Modellaufruf):
  * Claude Code 2.1.322 (claude.exe der VS-Code-Erweiterung) mit CLAUDE_CONFIG_DIR=<temp>:
    `claude mcp add -s local spaider-memory -- <bridge>` -> `claude mcp list` meldet
    "Connected" (Claude Code hat die Bruecke gestartet, MCP-Handshake gegen den laufenden
    Kern erfolgreich), `claude mcp get` zeigt die Konfiguration, danach `claude mcp remove`.
    Hinweis: Projekt-Scope (.mcp.json) steht in Claude Code auf "Pending approval", bis der
    Nutzer das Projekt interaktiv freigibt - fuer die Anleitung daher lokaler Scope empfohlen.
  * Codex-CLI 0.167.0-alpha.3.2 (codex.exe aus dem Codex-App-Paket) mit CODEX_HOME=<temp>
    und dem erzeugten TOML: `codex mcp list` zeigt spaider_memory "enabled", `codex mcp get`
    die Konfiguration. Das ist nur die Konfigurationsebene; ein Handshake durch Codex selbst
    braucht eine Sitzung mit Modell und wurde nicht ausgefuehrt (kein fremdes Kontingent).
  * Claude Desktop: nicht installiert - JSON-Schnipsel nur vorbereitet.
  * ChatGPT: kein Tunnel (keine Tunnel-ID/Runtime-Key) - nur Status/Anleitung.
- Dev-Modus: bridge_command() liefert einen cwd-unabhaengigen Startbefehl (python -c ...);
  im Paket ist es die Core-EXE selbst.

Naechster Schritt: Phase 5 (SIWC-Skelett providers/siwc.py mit Testserver, ChatGPT-Tunnel-
Status), UI-Integration des Desktop-Agenten, dann Phase 6 (echte Tests, Paket 0.4.0, Docs).

### Phase 5 (16:05-17:10): ChatGPT-Anmeldung (Skelett), Tunnel-Status, UI-Integration
- providers/siwc.py: PKCE S256 + state/nonce, Loopback-Empfaenger 127.0.0.1:<port>/auth/callback,
  dynamische Registrierung (client_id=dynamic_agent_client, agent_name_hint, ext_agent_host_id),
  Token-Austausch mit resource, ID-Token-Pruefung gegen JWKS (Issuer/Audience/Nonce; PyJWT +
  cryptography), Token host-gebunden (DPAPI) oder nur Sitzung - nie im portablen Tresor,
  serialisierter Refresh mit Rotation, invalid_grant -> "erneut anmelden" (kein stiller
  API-Key-Rueckfall), Logout mit Revocation. Nie unbeaufsichtigt: der Kern oeffnet keinen
  Browser und bestaetigt nichts. Routen /connections/{id}/siwc/{status,start,cancel,logout}.
  Tests: lokaler Fake-Issuer mit RSA-signierten ID-Tokens (voller Ablauf, Verweigerung,
  falscher state, fremder Schluessel, HTTPS-Pflicht). Gegen auth.openai.com nichts ausgefuehrt.
- /connectors/chatgpt-tunnel/status: nur Status/Schritte (tunnel-client nicht gefunden, keine
  Tunnel-ID); nichts veroeffentlicht. Commit 3c49b57.
- Desktop-UI (Subagent, von mir verifiziert: typecheck/lint/104 Tests/Build): Verbindungen
  (Schluesselspeicher/Tresor, 13 Anbieterkarten gleichwertig, Formular, vier Teststufen,
  Modelle mit Faehigkeits-Chips, Freigaben), Modelle in vier Bereichen (Entdecken mit
  Paginierung/Filtern/Offline-Banner, Repo-Detail mit Varianten/Projektor/Modellkarte),
  Chat-Kopf mit Bindung, Freigabedialog, Fehlerzustand mit naechstem Schritt und
  "Erneut senden", Teiltext als unvollstaendig markiert. Commit f3335ad.
- Vertragsnachzuege aus den UI-Hinweisen: resolve nimmt den gewaehlten Projektor an,
  last_test hat eine feste Form (AccessCheck mit "at"), Fehler bei Freigabeblockaden tragen eine
  Zaehler-Schaetzung, verbindungsgebundene Chats brauchen kein lokales Modell, Launcher-Allowlist
  fuer Anbieter-Dokumentationslinks. Commit 2affef0.
- Sicherheitsluecke geschlossen, bevor sie eine wurde: Erinnerungen aus "Nur lokal"-Projekten
  wurden vom profilweiten Abruf auch fuer Cloud-gebundene Gespraeche geliefert. Jetzt filtert
  der Abruf nach Herkunft (Gespraech/Dokument/Connector -> Projekt), Kanarien-Test
  tests/test_cloud_isolation.py (CANARY-7731 erscheint in keiner Anfrage an den Fake-Cloud-Server,
  Fakt aus dem offenen Projekt schon). Kern-Suite: 128 bestanden, 6 uebersprungen.
- Dokumente: CONNECT_MEMORY_CLIENTS_DE, CONNECT_PROVIDERS_DE, CONNECT_CHATGPT_DE,
  PROVIDER_ADAPTER_GUIDE, MODELHUB_QUICKSTART_DE; API_CONTRACT Addendum v1.4;
  MODELHUB_ARCHITECTURE Umsetzungsstand.

### Phase 6 (17:10-): echte Tests, Paket, Uebergabe
- scratchpad/real_modelhub.py gegen den Dev-Kern mit echter Laufzeit (Ergebnisse in
  measure/modelhub/): T3 echter Download Qwen3-0.6B-Q8_0 aus dem eingebetteten Katalog
  (gepinnte Revision, SHA-256), T1 lokaler Chat ueber die Event-Schicht + Stopp, T17 Wissen
  Qwen -> Gemma 4 E2B -> Qwen, T5 eigener OpenAI-kompatibler Server (zweiter llama-server mit
  --api-key: vier Stufen, falscher Schluessel, Chat ueber die Verbindung, Gedaechtnis sichtbar),
  T9 Bruecke mit echtem Gedaechtnis (SDK-Client), T7/T8 Profilwechsel. Zahlen im Testbericht.
