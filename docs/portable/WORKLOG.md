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
- [ ] F Windows-Paket, Offline-/Umzugs-/Fehlertests
- [ ] G Doku, Test-Build, Übergabe

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

Nächster konkreter Schritt: E2E-Lauf erneut (läuft), Frozen-Core-Start prüfen,
Desktop-Paket zusammenführen (`assemble_portable.py`), Fresh-Start-/Offline-Test
mit dem fertigen Paket.
