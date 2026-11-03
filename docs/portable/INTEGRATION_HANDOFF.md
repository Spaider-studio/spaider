# SpAIder Foldable: Integrationsübergabe

Stand: 2026-11-02. Dieses Dokument beschreibt, wie die privat entwickelte
Erweiterung später in das gemeinsame Repository übernommen werden kann. Die
Schritte unter „Spätere Integration“ wurden **nicht** ausgeführt.

## Herkunft

| | Wert |
|---|---|
| Original-Repository | `https://github.com/Spaider-studio/spaider` |
| Ausgangsbranch | `main` |
| Ausgangscommit | `73762b375f1b5e39768fd2002e4b3198d1e7d6f2` („0.4.0: faster ingestion, MCP fix, dependency refresh (#47)“, 2026-09-30) |
| Tag im privaten Repo | `foldable-base-73762b3` |
| Privates Repo | `https://github.com/Useebay/spaider-foldable-private` (privat, kein Fork) |
| Arbeitsbranch | `feature/spaider-foldable-v1` (basiert direkt auf dem Ausgangscommit) |
| Sandbox-Branch | `main` des privaten Repos = Ausgangscommit + Sandbox-Commit(s) |

Der Arbeitsbranch enthält ausschließlich Produktänderungen. Sandbox-Anpassungen
(nur auf dem privaten `main`): `SANDBOX.md`, Entfernen von
`.github/dependabot.yml`; GitHub Actions sind im privaten Repo auf
Repository-Ebene deaktiviert.

## Änderungen auf dem Arbeitsbranch

Neue Verzeichnisse (keine Änderung an bestehenden Dateien außer `.gitignore`):

- `foldable/core/` Python-Paket `spaider_foldable` (Kern) + Tests
- `foldable/desktop/` Electron-Launcher + React-Oberfläche
- `foldable/build/` Runtime-Download (gepinnt), PyInstaller-Spec, Paketierung
- `docs/portable/` diese Dokumentation
- `.gitignore`: Regeln für `foldable/`-Build-Ausgaben, Laufzeit-Binaries,
  `*.gguf`; Re-Include von `foldable/build/` (die upstream-Regel `build/` würde
  die Build-Skripte verstecken)

Keine Änderung an `backend/`, `frontend/`, `cli/`, `sdk/`, Compose-Dateien
oder Workflows. Die Server-/Cloud-Betriebsart ist unverändert; alle
Portable-Standardwerte wirken nur im Prozess `spaider-foldable-core`
(gesetzt in `foldable/core/spaider_foldable/bootstrap.py`).

Datenbank-Migrationen: nur die neue SQLite-Datenbank des portablen Modus
(`foldable/core/spaider_foldable/db/schema_v1.sql`, Version 1). Neo4j/Postgres
bleiben unberührt.

Vorschläge für kleine Upstream-Änderungen (nicht umgesetzt, um den Diff auf
das Neue zu beschränken):

1. `SemanticCompressor.__init__(cache=None)`: Cache injizierbar machen; der
   portable Adapter ersetzt derzeit das private Attribut `_redis`.
2. `_attach_fact_node` aus `backend/app/api/v1/ingest.py` in ein Service-Modul
   verschieben, damit es ohne Kafka/Postgres-Imports nutzbar ist.
3. CI: Job `foldable-tests` (`cd foldable/core && pytest`) und
   `foldable-desktop-build` (`npm ci && npm run build`), beide ohne Docker.

## Spätere Integration (nicht ausgeführt)

Zwei unabhängige Repositories ohne Fork-Beziehung ergeben keinen normalen
Fork-Pull-Request. Empfohlener Weg: den Branch in das gemeinsame Repo pushen
und dort einen PR öffnen.

```bash
# im Checkout des gemeinsamen Repos (Spaider-studio/spaider)
git remote add foldable https://github.com/Useebay/spaider-foldable-private.git
git fetch foldable feature/spaider-foldable-v1 foldable-base-73762b3

# 1) prüfen, dass der Branch wirklich auf dem dokumentierten Ausgangscommit sitzt
git merge-base foldable/feature/spaider-foldable-v1 origin/main   # erwartet 73762b375f1b...
git log --oneline 73762b375f1b..foldable/feature/spaider-foldable-v1

# 2) Integrationsbranch im gemeinsamen Repo (erst nach Absprache mit dem Cofounder)
git checkout -b feat/spaider-foldable origin/main
git merge --no-ff foldable/feature/spaider-foldable-v1      # oder: rebase, falls main weitergezogen ist
#   Alternativ selektiv:  git cherry-pick 73762b375f1b..foldable/feature/spaider-foldable-v1
#   oder als Patch:       git format-patch 73762b375f1b..foldable/feature/spaider-foldable-v1 -o /tmp/foldable

# 3) lokal prüfen
cd foldable/core && pip install -e .[dev] && pytest
cd ../desktop && npm ci && npm run typecheck && npm test && npm run build

# 4) PR mit docs/portable/PR_DRAFT.md als Beschreibung öffnen (Conventional-Commit-Titel)
```

Konflikte sind nur in `.gitignore` zu erwarten (angehängter Block).

## Push-Blocker (Stand 2026-11-02)

Das gh-Token auf dem Entwicklungsrechner hat die Scopes `repo, read:org, gist`,
nicht `workflow`. GitHub lehnt Pushes von Commits ab, die
`.github/workflows/*.yml` enthalten (jeder Commit der Historie). Lösung:
einmalig `gh auth refresh -h github.com -s workflow`, danach

```bash
cd C:\Users\mucbay\spaider-foldable\repo
git push -u origin main
git push origin foldable-base-73762b3
git push -u origin feature/spaider-foldable-v1
```

Bis dahin liegen Git-Bundles unter `C:\Users\mucbay\spaider-foldable\backup\`
(`git bundle verify <datei>`, `git clone <datei> -b feature/spaider-foldable-v1`).

---

## Nachtrag 2026-11-02: Branch `feature/spaider-foldable-performance-models`

- Basis: `acc3ce1` (Ende des V1-Branches `feature/spaider-foldable-v1`), damit
  die Historie linear bleibt: `73762b3 (upstream) → V1-Commits → 0.2-Commits`.
- Enthält ausschließlich Änderungen unter `foldable/` und `docs/portable/`
  (plus die bereits vorhandenen 17 Zeilen in `.gitignore`). Keine Änderung an
  `backend/`, `frontend/`, `cli/`, `sdk/`.
- Datenbank: Migration v2 (`foldable/core/spaider_foldable/db/schema_v2.sql`:
  Spalte `nodes.processing`, `ingest_queue.fact_id`, `messages.timings_json`,
  Tabellen `hardware_profiles`, `model_profiles`, `catalog_entries`,
  `catalog_cache`). Vorwärts-Migration beim Start, getestet an einer Kopie
  des Nutzer-Datenordners (siehe TEST_REPORT, Abschnitt „Update an Bestandsdaten“).
- Neue Laufzeit-Variante im Paket (`resources/runtime/llama-vulkan`, llama.cpp
  b12032 Vulkan, SHA-256 `19a8bbf2…cc32`), gepinnt in `foldable/build/fetch_runtime.py`.
- Messdaten in Git: `foldable/build/measurements/*.json` enthalten nur Zeiten,
  Zähler und synthetische Testsätze (keine Nutzerinhalte). Die Rohdaten des
  abgebrochenen Baseline-Laufs liegen außerhalb des Repos
  (`C:/Users/mucbay/spaider-foldable/measure/results`).
- Integrationsweg unverändert (Fetch des privaten Repos, Merge/Cherry-pick,
  PR-Entwurf in `PR_DRAFT.md`). Für den 0.2-Stand den Branch
  `feature/spaider-foldable-performance-models` verwenden; er enthält V1.
- Upstream-Vorschläge (nicht umgesetzt, um Fremdcode nicht zu ändern):
  `SemanticCompressor._extract_with_retry` sollte `max_tokens`/Timeout
  konfigurierbar machen (der Adapter kappt derzeit im Modul-Namensraum), und
  `_attach_fact_node` sollte in ein Service-Modul wandern.

---

## Nachtrag 2026-11-04: Branch `feature/spaider-foldable-agent-live` (0.3)

- Basis: `debc9be` (Ende des 0.2-Branches); Historie weiter linear. Nur `foldable/` und
  `docs/portable/` geändert; `backend/`, `frontend/`, `cli/`, `sdk/` unverändert.
- Neue Kernmodule: `spaider_foldable/observe/` (windows, capture, uia, snapshot),
  `spaider_foldable/live/sessions.py`, `spaider_foldable/agent/` (models, store, broker, loop,
  prompts, browserpkg, tools/{files,memory,windows,vision,browser}), Routen `routes_live.py`,
  `routes_agent.py`, Schema `db/schema_v3.sql` (Migration 3: `agent_tasks`, `agent_actions`,
  `agent_recipes`). Tests: `test_agent_core.py`, `test_agent_tools.py`, `test_real_live.py`,
  Fixtures `tests/fixtures/winforms_*.ps1`, Aufgabenmatrix `tests/matrix/`.
- Neue Abhängigkeiten (pyproject gepinnt, Lizenzen): pywinauto 0.6.9 (BSD-3), comtypes 1.4.17
  (MIT), pywin32 312 (PSF), mss 10.2.0 (MIT), playwright 1.64.1 (Apache-2.0; der Node-Treiber wird
  als Daten in den PyInstaller-Bundle gelegt). Browser: Chrome for Testing 156.0.8078.4
  (BSD-3, Playwright-Build 1248), **nicht** im ZIP, sondern per Job in `data/browser/`
  (207 073 013 Bytes, SHA-256 `220021ec…41db`, Host `cdn.playwright.dev` → Weiterleitung
  `storage.googleapis.com`; beide im Downloader erlaubt).
- Vision-Modelle: Katalogeinträge mit `kind: mmproj` (Qwen/Qwen3-VL-4B-Instruct-GGUF offiziell,
  ggml-org/gemma-4-E2B-it-GGUF); Registry paart Projektor und Gewichte.
- Electron: Overlay-Fenster, globale Hotkeys, Tray, Aufgabenpanel, Einstellungen „Live & Agent“
  (Desktop-Teil, siehe `foldable/desktop/NOTES.md`).
- Upstream-Vorschläge (nicht umgesetzt): keine; die Agentenschicht liegt vollständig unter
  `foldable/` und nutzt den Kompressor/Retriever nur lesend.

