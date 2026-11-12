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
cd C:\Users\<Nutzer>\spaider-foldable\repo
git push -u origin main
git push origin foldable-base-73762b3
git push -u origin feature/spaider-foldable-v1
```

Bis dahin liegen Git-Bundles unter `C:\Users\<Nutzer>\spaider-foldable\backup\`
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
  (`C:/Users/<Nutzer>/spaider-foldable/measure/results`).
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
- Nachträge aus der Aufgabenmatrix (Commits `ad42cac`, `f01a359`): Scope-Erweiterung durch
  Werkzeuge wird persistiert; Browserwerkzeuge sind an die öffnende Aufgabe gebunden;
  `bring_to_front` arbeitet gegen die Vordergrund-Sperre; unbelegte Erfolgsmeldungen werden einmal
  zurückgewiesen; `vision.locate` prüft die Koordinatenkonvention je Antwort; Rezepte werden
  deterministisch über den Broker wiedergegeben (Parameter `{name}`, Rückfall auf den Planer).
  Vertrag v1.2 ergänzt: `max_elements` 0..1000 beim Snapshot, `save-recipe`-Parameter mit `value`,
  Ausführungssemantik von `recipe_id`. Keine neuen Abhängigkeiten.
- Für eine spätere Integration in den SpAIder-Hauptzweig: Die Werkzeuge sind über
  `agent/broker.py` registriert (ein `Tool` je Fähigkeit, Schema + Scope + Wirkungsklasse); ein
  MCP-Adapter könnte dieselbe Registrierung nutzen, ist aber nicht enthalten.
- Weitere Nachträge (`0a63eec` leere Rückfragen verworfen, `1d2bbd8` eindeutige Elementnamen als
  Referenz + Fixtures mit BOM, `371a503` Rezept-Parameter in beiden Schrägstrich-Schreibweisen).
- **Paket 0.3.0** (`foldable/build/out/SpAIder-Foldable-0.3.0-win-x64.zip`, 395 662 613 Bytes,
  SHA-256 `870582424be196d6a3bff97c67b35e684f4a1de6dc38952814a06dd6e54773fa`): gepackter Kern aus
  `371a503`, Electron-Launcher aus `a85dca6`, Runtime llama.cpp b12032 (CPU + Vulkan). Keine
  Gewichte, kein Browser, kein Datenordner im ZIP (`verify-package-0.3.0.json`,
  `package-check-0.3.0.json`). Die 0.1.0- und 0.2.0-Pakete liegen unverändert daneben
  (`SHA256SUMS.txt` kumulativ).
- Push-Status: unverändert blockiert (gh-Token ohne `workflow`-Scope, siehe oben); Bundle
  `backup/spaider-foldable-0.3-final-<datum>.bundle` enthält alle Branches.



## Nachtrag 2026-11-04: Review-Branch und Übergabe an den Mitgründer

**Ausgangslage (lokal verifiziert):** `upstream` = `https://github.com/Spaider-studio/spaider`
(öffentlich, Default-Branch `main` bei `73762b3`, seit der Foldable-Basis unverändert);
`origin` = `https://github.com/Useebay/spaider-foldable-private` (privat, leer – noch nie gepusht).
Der GitHub-Token hat die Scopes `gist`, `read:org`, `repo`, aber nicht `workflow`; die Historie
enthält `.github/workflows/*.yml` aus dem gemeinsamen Repository, deshalb lehnt GitHub jeden Push
in das leere private Repository ab („refusing to allow an OAuth App to create or update
workflow“).

**Review-Weg (B/C laut Auftrag):** Das gemeinsame Repository ist öffentlich, also findet die erste
Review-Runde im privaten Repository statt. Vorbereitet (lokal):

| Ref | Zeigt auf | Zweck |
|---|---|---|
| `review/spaider-base-73762b3` | `73762b3` = `upstream/main` | Basis des Review-PRs (unverändert) |
| `feature/spaider-foldable-review` | f281d78 + Review-Dokumente (`cd50a04`) + Ergebnisse/Manifest | Integrationsbranch mit dem vollständigen Foldable-Stand |
| `foldable-review-0.3.0-cd50a04` (Tag) | Review-Commit | Prerelease mit dem Review-ZIP |

Herkunft: Der Integrationsbranch enthält die 66 Produktcommits `73762b3..f281d78` unverändert
(0.1 `acc3ce1`, 0.2 `debc9be`, 0.3 `f281d78`); sie liegen bereits linear auf `73762b3`, es gab
keine Konflikte und keine Sandbox-Commits (der private `main` trägt nur `8f20d3a` „mark private
sandbox“, der **nicht** übernommen wird). Diff gegenüber der Basis: ausschließlich `foldable/`,
`docs/portable/` und `.gitignore` (276 Dateien, nur Ergänzungen); keine Änderungen unter
`.github/`, `backend/`, `frontend/`, `cli/`, `sdk/`.

**Wiederaufnahme (nach der einmaligen Freigabe durch Musa):**

```powershell
gh auth refresh -h github.com -s workflow        # einmalig, interaktiv, nur von Musa
cd C:\Users\<Nutzer>\spaider-foldable\repo-review
git push origin review/spaider-base-73762b3       # Basis zuerst
git push origin feature/spaider-foldable-review   # Integrationsbranch
git push origin foldable-review-0.3.0-cd50a04       # Review-Tag (nie --all / --mirror)
gh pr create --repo Useebay/spaider-foldable-private --draft `
  --base review/spaider-base-73762b3 --head feature/spaider-foldable-review `
  --title "SpAIder Foldable: portable Windows-App mit lokalem Chat, Gedächtnis und Agent/Live-Vorschau" `
  --body-file docs/portable/PR_BODY_REVIEW.md
gh release create foldable-review-0.3.0-cd50a04 --repo Useebay/spaider-foldable-private `
  --prerelease --title "Foldable Review-Build 0.3.0 (cd50a04)" --notes-file foldable/build/out/REVIEW_BUILD_MANIFEST.md `
  foldable/build/out/SpAIder-Foldable-0.3.0-review-cd50a04-win-x64.zip foldable/build/out/SHA256SUMS-0.3.0-review-cd50a04.txt
```

Vorher prüfen: `gh auth status` zeigt `workflow`; `git ls-remote origin` ist leer (sonst nichts
überschreiben); das private Repository bleibt privat, Actions dort deaktiviert. Danach die
tatsächlichen PR-/Release-Links in `REVIEW_START_HERE_DE.md` und der PR-Beschreibung eintragen.

**Später, erst nach gemeinsamer Review-Entscheidung – gemeinsames Repository (öffentlich!):**

```powershell
git remote set-url --push upstream https://github.com/Spaider-studio/spaider.git   # Push ist lokal bewusst deaktiviert
git push upstream feature/spaider-foldable-review
gh pr create --repo Spaider-studio/spaider --draft --base main --head feature/spaider-foldable-review `
  --title "SpAIder Foldable: portable Windows-App mit lokalem Chat, Gedächtnis und Agent/Live-Vorschau" `
  --body-file docs/portable/PR_BODY_REVIEW.md
```

CI-Hinweis für den späteren Push: `.github/workflows/ci.yml` läuft bei `pull_request` gegen `main`
und filtert nach geänderten Paketbäumen (`backend/`, `frontend/`, `cli/`, `sdk/`); der Foldable-
Diff berührt keinen davon, also sollten nur der `changes`-Job und die Pfadfilter laufen. Der
PR-Labeler (`pr-labeler.yml`) kann ein Label setzen. Der CLA-Bot kommentiert beim ersten PR.
Weder Merge noch Auto-Merge noch Veröffentlichung ohne gemeinsame Entscheidung.

**Zugriff für den Mitgründer:** Falls er das private Repository noch nicht sieht: Repository →
Settings → Collaborators → „Add people“ (Leserecht genügt für PR, Diff und Prerelease-Assets).
Das Repository dafür nicht öffentlich schalten.


## Stand 0.4.0 (2026-11-11): Branch `feature/spaider-foldable-modelhub-connectors`

| | Wert |
|---|---|
| Basis | `feature/spaider-foldable-review` @ `84f59c4` (= 0.3.0 + Review-Dokumente), Ausgangscommit weiterhin `73762b3` |
| Worktree | `C:\Users\<Nutzer>\spaider-foldable\repo-modelhub` |
| Bundle vor den Änderungen | `backup/spaider-foldable-pre-modelhub-20261111.bundle` (verifiziert) |
| Ziel-Repo | nur `Useebay/spaider-foldable-private` (privat); kein Push nach `Spaider-studio/spaider` |

### Neue und geänderte Bereiche (nur unter `foldable/` und `docs/portable/`)

- `foldable/core/spaider_foldable/providers/` – Event-Schicht, drei Wire-Adapter, Presets,
  Registry, Verbindungen/Freigaben/Policy, Schlüsselspeicher (DPAPI, Tresor, Sitzung), SIWC.
- `foldable/core/spaider_foldable/connectors/` – Connector-Dienst (Rechte, Werkzeuge) und
  stdio-MCP-Brücke (`--mcp-stdio`).
- `profiles.py`, `paths.py`, `lock.py`, `__main__.py` – Profile, Discovery-Datei, Bridge-Modus.
- `models/discover.py` – Hugging-Face-Suche/Repo-Detail/Modellkarte; `api/routes_*` – Vertrag
  v1.3/v1.4; `db/schema_v4.sql`, `schema_v5.sql` (Migrationen 4 und 5).
- `chat/service.py`, `memory/retrieve.py` – Events, Bindung, Freigabe, Herkunftsfilter.
- `foldable/desktop/` – Verbindungen, Modellbibliothek (vier Bereiche), Bindung, Freigabedialog,
  Fehlerzustände; Mock-Server v1.3.
- Neue Abhängigkeiten (Kern): `mcp==2.3.0`, `PyJWT==2.15.1`, `cryptography==50.0.2`
  (in `pyproject.toml` und in der PyInstaller-Spec gesammelt).

### Drift zum öffentlichen Upstream

`Spaider-studio/spaider` `main` steht inzwischen auf `126ef75` (v0.5.0). Diese Arbeit
basiert weiterhin auf `73762b3` (v0.4.0 des Backends). Vor einer Integration:
Upstream-Diff `73762b3..126ef75` auf Änderungen an `backend/app/models/schemas.py`,
`backend/app/services/*` (werden als Bibliothek importiert) prüfen; die Foldable-Dateien
berühren keine Upstream-Dateien außer `.gitignore`. Das MCP-SDK des Upstreams (falls
vorhanden) und `mcp==2.3.0` müssen auf dieselbe Hauptversion gebracht werden.

### Spätere Integration (nicht ausgeführt)

1. Review-Branch gegen `review/spaider-base-73762b3` (siehe `REVIEW_START_HERE_DE.md`).
2. Rebase auf aktuellen Upstream, Tests (`pytest tests` im Kern; `npm test`, `npm run build`
   im Desktop), Paketbau (`foldable/build/*`), Vergleichslauf des Testberichts.
3. Erst dann Pull Request gegen das gemeinsame Repository; Veröffentlichungen (Releases,
  Plugins, Tunnel) bleiben eine Entscheidung der Gründer.


## Stand 0.5.0 (Agent v2, 2026-11-12)

- Branch `feature/spaider-foldable-agent-v2` setzt auf `feature/spaider-foldable-0.4-review`
  (PR #60, Commit 219828a = offizielles `main` 126ef75 + Foldable 0.1–0.4) auf. Der Draft-PR
  für 0.5 zielt deshalb auf den Branch von #60, nicht auf `main`; nach dem Merge von #60 kann
  er auf `main` umgestellt werden (keine Konflikte erwartet: alle Änderungen liegen unter
  `foldable/` und `docs/portable/`).
- Neue Dateien: `foldable/core/spaider_foldable/agent/{contracts,deps,compiler,reuse,bindings,
  verifier,planner,inputguard,teach,workflows}.py`, `db/schema_v6.sql`, `api/routes_teach.py`,
  `tests/test_agent_v2.py`, `tests/test_agent_v2_api.py`, `tests/test_teach.py`,
  `tests/matrix/tasks_v2.json`, `tests/matrix/run_matrix_v2.py`, `tests/fixtures/winforms_formapp.ps1`;
  Desktop: `components/tasks/{WorkflowsList,TeachPanel}.tsx` (RecipesList entfernt),
  Bridge `openPath`/`revealPath`.
- Geänderte 0.3/0.4-Dateien: `agent/loop.py` (neu geschrieben), `agent/models.py`, `agent/store.py`,
  `agent/broker.py` (unverändert), `agent/tools/windows.py` (Eingabesperre), `agent/tools/files.py`
  (Schreibordner lesbar), `agent/tools/browser.py` (Elementnamen), `memory/store.py` (Hook),
  `connectors/service.py` (Invalidierung), `context.py` (Verdrahtung), `api/routes_agent.py`,
  `api/app.py`, `models/registry.py` (agent_checks), `observe/windows.py` (last_input_age_ms),
  `build/spaider_foldable_core.spec` (schema_v6).
- Schema: v6 additiv; `agent_recipes` bleibt, Rezepte werden beim Start als Kandidaten übernommen.
- Befund am veröffentlichten 0.4-Stand (PR #60): `tests/matrix/run_matrix.py` enthält ein
  Backspace-Byte im Standardpfad `dev-app\browser` (durch die Pfad-Neutralisierung entstanden);
  auf diesem Branch repariert. Beim Review von #60 bitte mitnehmen.
- Vergleichsläufe und Baseline: `foldable/build/measurements/matrix2-*.json`,
  `baseline-*.json`, Produktdemo `demo-x10-full-*.json`; Erzeugung mit
  `tests/matrix/run_matrix_v2.py` (Arme none/simple/full, `--tasks X10 --mode full`).
- Historie: vor dem ersten Push lokal umgeschrieben (Nutzerpfad in Zwischenständen entfernt);
  die Commit-IDs in den Dokumenten sind die veröffentlichten. Kein Force-Push nötig oder erfolgt.
- Nach der Messreihe behoben (0e6bdb4, im Paket enthalten, Tabellen nicht neu gemessen):
  Reparatur lesender/abgeleiteter Schritte ohne Planer, Zwischendateien keine Abhängigkeit
  (`agent/loop.py`, `agent/reuse.py`, `agent/compiler.py`, Tests angepasst/ergänzt).
