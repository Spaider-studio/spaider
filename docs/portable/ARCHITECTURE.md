# SpAIder Foldable: Architektur (V1)

SpAIder Foldable ist die portable Betriebsart von SpAIder: Programme, Modell und
der vollständige Arbeitsstand liegen in einem Ordner auf einem externen
Datenträger. Es gibt keinen Installer, keinen Dienst im Hostsystem und keine
Cloud-Anbindung. Diese Datei beschreibt Zustandsmodell, Komponenten,
Abhängigkeiten und Erweiterungspunkte. Was V1 ausdrücklich nicht kann, steht in
`KNOWN_LIMITATIONS.md`.

## 1. Zustandsmodell

Alles Dauerhafte liegt unter `<App-Ordner>/data/`. Der App-Ordner ist der Ordner
mit `SpAIder.exe`; der Launcher übergibt ihn dem Kern als `--app-root`. Kein
Pfad wird aus dem Arbeitsverzeichnis, einem Laufwerksbuchstaben oder dem
Benutzerprofil abgeleitet. In der Datenbank werden Pfade relativ zu `data/`
gespeichert (Forward-Slashes), damit ein anderer Laufwerksbuchstabe oder ein
anderer Rechner nichts ändert.

| Ort | Inhalt | Lebensdauer |
|---|---|---|
| `data/spaider.db` (+ `-wal`, `-shm`) | SQLite: Gespräche, Nachrichten, Dokumente, Chunks, Wissensgraph (Knoten, Kanten, Vektoren), Erinnerungen, Modelle, Jobs, Einstellungen, Ingest-Warteschlange | dauerhaft |
| `data/models/<id>/` | GGUF-Datei + `manifest.json` (Quelle, Revision, SHA-256, Lizenz, Architektur) | dauerhaft, bewusst löschbar |
| `data/downloads/<job>/` | Teildownloads (`.part`), fortsetzbar | bis zum erfolgreichen Abschluss |
| `data/imports/<doc-id>/` | Kopie der importierten Originaldatei | bis zum bewussten Löschen |
| `data/logs/` | `core.log`, `llama-chat.log`, `llama-embedding.log`, Electron-Logs | rotierend |
| `data/electron/` | Electron userData, Session, Cache | verwerfbar |
| `data/cache/`, `data/tmp/` | modellgebundene Caches, Scratch | verwerfbar (tmp wird beim Start geleert) |
| `data/instance.lock` | PID, Hostname, Startzeit der laufenden Instanz | nur während des Laufs |

Datenbank: WAL-Journal, `synchronous=NORMAL`, `busy_timeout`, `quick_check`
beim Öffnen (eine beschädigte Datei wird gemeldet, nie stillschweigend
ersetzt), versionierte Vorwärts-Migrationen (`schema_migrations`,
`PRAGMA user_version`). Beim Beenden wird das WAL per `wal_checkpoint(TRUNCATE)`
in die Hauptdatei geschrieben. Ein Zweitstart mit demselben Datenverzeichnis
auf demselben Rechner wird über den Lock verweigert; ein verwaister Lock (Absturz
oder anderer Rechner) wird übernommen und protokolliert.

Gedächtnis-Zustände eines Fakts: `aktuell`, `veraltet (ersetzt durch …)`,
`unsicher` (Extraktionssicherheit < 0,6). Konflikte werden nicht automatisch
aufgelöst, sondern als ältere/neuere Angaben im Kontext ausgewiesen; der
Supersession-Judge (SpAIder) markiert nur einwertige Zustandsänderungen.

## 2. Komponenten

```
SpAIder.exe (Electron, Hauptprozess)
  ├─ startet  resources/core/spaider-foldable-core.exe --app-root <Ordner>
  │           (Env: SPAIDER_FOLDABLE_TOKEN, SPAIDER_FOLDABLE_RUNTIME_DIR)
  ├─ liest    stdout: {"event":"ready","port":N}
  └─ Renderer (React, isoliert, nur window.spaider-Bridge)  ──HTTP+SSE──▶ 127.0.0.1:N
                                                                    │ X-SpAIder-Token
spaider-foldable-core.exe (Python, FastAPI, Loopback)
  ├─ AppContext ─ Lock, SQLite, Registry, Jobs, Ingest-Worker, Chat
  ├─ InferenceManager ─ startet llama-server.exe (Chat) [+ llama-server.exe (Embedding)]
  │     --host 127.0.0.1 --port <frei> --api-key <zufällig> --jinja --no-webui
  │     Windows-Job-Object: Kindprozesse enden mit dem Kern
  ├─ Memory ─ LocalMemoryStore (SQLite+FTS5+numpy) · Ingestor · Retriever · Supersession
  └─ Reused SpAIder (backend/app als Bibliothek): parser_service, schemas,
     compressor (Prompt, JSON-Parser, Ontologie, Chunking), entity_resolver,
     supersession (Gate + Judge), query_service (_rrf_fuse, _build_context),
     litellm_retry, config.Settings (auf Loopback-Endpunkte gesetzt)
```

### Schichten und Schnittstellen

| Schicht | Modul | Vertrag |
|---|---|---|
| Oberfläche | `foldable/desktop/src/renderer` | HTTP/SSE laut `API_CONTRACT.md`, `window.spaider`-Bridge |
| Fenster/Launcher | `foldable/desktop/src/main` | Prozessstart, Token, graceful shutdown, Pfade (`userData` unter `data/electron`) |
| API | `spaider_foldable/api` | FastAPI-Router, Token-Middleware, CORS nur `null`/Vite-Dev |
| Chat-/Sitzungssteuerung | `spaider_foldable/chat` | `ChatService.send()` → SSE-Ereignisse; ein Stream pro Gespräch; Stop; Teilantworten werden persistiert |
| Gedächtnis | `spaider_foldable/memory` | `LocalMemoryStore` (GraphService-Duck-Type für den Resolver), `MemoryIngestor`, `MemoryRetriever`, `resolve_supersession_local` |
| Modellverwaltung | `spaider_foldable/models` | Katalog (`catalog.json`), GGUF-Validierung, Downloader, Registry (Manifeste), `JobManager` (Download/Import/Aktivierung/Löschen) |
| Inferenzlaufzeit | `spaider_foldable/inference` | `LlamaServerProcess`, `LocalLLMClient` (OpenAI-kompatibel, Streaming, Embeddings, Tokenize), `InferenceManager` |
| Werkzeuge (später) | `spaider_foldable/tools` | `ToolSpec`, `ToolCall`, `ToolResult`, `CapabilityGrant` (host-gebunden), `ToolRegistry` |

### Wiederverwendung aus SpAIder (belegt durch Tests)

| SpAIder-Modul | Genutzt für | Test |
|---|---|---|
| `app.services.parser_service.parse_sync` | TXT/MD/PDF-Textextraktion beim Import | `test_documents_tools.py` |
| `app.models.schemas` (Node, Edge, GraphPayload) | Datentypen des Gedächtnisses | alle Memory-Tests |
| `app.services.compressor.SemanticCompressor` (+ `OntologyManager`, Prompt `compressor_system.txt`, `_build_payload`, `_strip_markdown`, Chunking) | Entitäten/Relationen aus Nachrichten und Dokumentabschnitten | `test_memory.py::test_ingestor_keeps_fact_when_extraction_fails`, E2E |
| `app.services.entity_resolver.EntityResolver`, `build_embed_text` | Deduplizierung (exakt, Alias, Levenshtein, Kosinus) gegen `LocalMemoryStore` | `test_memory.py::test_entity_resolver_reuse_merges_duplicates` |
| `app.services.supersession._is_state_update`, `_judge`, `_edge_text` | Zustandsänderungen erkennen und alte Fakten als ersetzt markieren | `test_memory.py::test_supersession_marks_old_fact`, E2E |
| `app.services.query_service._rrf_fuse`, `QueryService._build_context` | Hybride Fusion FTS+Vektor, Kontexttext für das Modell | `test_memory.py`, E2E |
| `app.lib.litellm_retry.acompletion_with_retry` + `app.config.Settings` | LLM-Aufrufe der wiederverwendeten Services gegen den lokalen Server | E2E |
| `app.services.embedding_service.EmbeddingService.cosine_similarity` | Kosinus im Resolver | `test_memory.py` |

Adapter-Eingriffe (keine Änderung am gemeinsamen Code):
`SemanticCompressor._redis` wird durch einen No-op-Cache ersetzt (privates
Attribut; Vorschlag für upstream: Cache injizierbar machen), `app.config.settings`
wird zur Laufzeit auf die Loopback-Ports gesetzt, `_attach_fact_node` aus
`api/v1/ingest.py` ist 15 Zeilen nachgebildet (nicht importierbar ohne Kafka/
Postgres-Stack; Vorschlag: in ein Service-Modul verschieben).

## 3. Datenflüsse

**Chat-Turn** (`POST /api/conversations/{id}/messages`):
1. Nutzer-Nachricht und leere Assistenten-Nachricht (`streaming`) speichern.
2. Retrieval: FTS5 (BM25 über Fakten/Entitäten) + optional Vektorsuche
   (Qwen3-Embedding, Instruktionspräfix für Anfragen), RRF-Fusion, 1-Hop-
   Graph-Erweiterung, `_build_context`; dazu Notizen und Dokumentabschnitte
   (FTS). Ersetzte Fakten zu den gefundenen Entitäten werden als „veraltet“
   angehängt. `sources`-Ereignis an die UI.
3. Prompt: deutscher Systemprompt + Gedächtnisblock (≤ 25 % des Kontextfensters)
   + Verlauf innerhalb des Budgets (`context_tokens − max_answer_tokens`,
   Tokenzählung über `/tokenize` des Servers).
4. Streaming über `/v1/chat/completions` (Thinking standardmäßig aus;
   `reasoning_content` wird getrennt als `thinking`-Ereignis gesendet). Alle
   ~1 s wird der Teilstand persistiert; Stop → Status `stopped`.
5. Danach landet die Nutzer-Nachricht in der Ingest-Warteschlange.

**Ingest** (Worker, läuft nur bei Chat-Leerlauf, weil der Server einen Slot hat):
Text → `SemanticCompressor.extract` → FACT-Knoten + MENTIONS-Kanten →
`EntityResolver.resolve` gegen den Store → `write_graph` (Knoten, Kanten,
Vektoren) → `resolve_supersession_local`. Schlägt die Extraktion fehl (kleines
Modell, ungültiges JSON), bleibt der FACT-Knoten trotzdem gespeichert und über
FTS/Vektor auffindbar.

**Modellwechsel** (`POST /api/models/activate`, Job `activate`):
`check_space` (Datei vollständig, RAM-Schätzung) → `load` (altes Modell
entladen, neues starten; nie beide gleichzeitig im RAM) → `probe` (`/health` +
Mini-Completion bzw. Embedding) → `commit` (aktive ID in einer Transaktion) →
`cleanup` (nur Download-Reste; altes Modell nur mit `remove_previous`). Jeder
Fehler vor `commit` führt zu `rollback`: vorheriges Modell wird neu gestartet,
die persistierte Auswahl bleibt unverändert. Unterbrochene Jobs werden beim
nächsten Start als `failed` (Aktivierung) bzw. `paused` (Download, fortsetzbar)
markiert.

**Embedding-Index**: Vektoren sind pro Embedding-Modell-ID gespeichert
(`node_embeddings(node_id, model_id, dims, vector)`); nur Vektoren des aktiven
Modells werden gelesen, Dimensionen werden nie gemischt. Nach einem Wechsel
füllt ein Hintergrundlauf fehlende Vektoren nach. Ein Wechsel des Chatmodells
berührt das Gedächtnis nicht.

## 4. Abhängigkeiten

| Komponente | Version | Lizenz | Bezug |
|---|---|---|---|
| llama.cpp (llama-server, CPU x64) | b12032 (2026-11-01) | MIT | GitHub-Release, SHA-256 gepinnt in `build/fetch_runtime.py` |
| Python-Kern | Python 3.12, FastAPI 0.141, uvicorn 0.53, pydantic 2, litellm 1.106, httpx 0.28, numpy 2.5, pdfplumber 0.11, markdown-it-py 4, Levenshtein 0.27, PyInstaller 6.22 | diverse (MIT/BSD/Apache) | pip, gebündelt via PyInstaller |
| SpAIder backend/app | v0.4.0 (73762b3) | AGPL-3.0 | Monorepo |
| Electron | 45.x | MIT | npm, gebündelt |
| Modelle | Qwen3 / Qwen3-Embedding GGUF (Apache-2.0), Katalog in `models/catalog.json` | je Eintrag | Hugging Face, Revision + SHA-256 gepinnt |

Zur Laufzeit beim Nutzer: keine Installation von Python, Node, Ollama, Docker,
WSL oder Datenbanken. Netzwerk nur für bewusst gestartete Modell-Downloads
(nur Hosts `huggingface.co`/`hf.co`, HTTPS, Weiterleitungen geprüft).

## 5. Sicherheit

- Kern nur auf 127.0.0.1; jeder Request braucht das Sitzungs-Token des
  Launchers; CORS nur für den Electron-Renderer (`null`) und den Vite-Dev-Server.
- llama-server nur auf 127.0.0.1 mit zufälligem API-Key; kein Web-UI.
- Electron: `contextIsolation`, `sandbox`, kein `nodeIntegration`, CSP,
  Navigation und `window.open` blockiert; `openExternal` nur für Hugging Face
  und das llama.cpp-Repository.
- Markdown wird ohne Roh-HTML gerendert; Chat-, Dokument- und Modellinhalte sind
  Daten, nie Anweisungen (Systemprompt sagt das dem Modell ausdrücklich).
- Alle Dateipfade aus Eingaben werden gegen `data/` geprüft (`is_inside_data`);
  Importnamen werden auf einen Dateinamen reduziert.
- Keine externen KI-APIs, kein Cloud-Fallback (litellm nur gegen Loopback,
  Kostenkarte lokal, Telemetrie aus).

## 6. Erweiterungspunkte

- **Werkzeuge/Agenten**: `spaider_foldable/tools/contracts.py` definiert
  `ToolSpec`, `ToolCall`, `ToolResult`, `CapabilityGrant` (gebunden an
  `current_host_id()`, mit Ablauf und Projektwurzel) und `ToolRegistry.execute`,
  das Grants prüft und jede Ausführung protokolliert. Ein späterer Coding-Agent
  registriert Executor für `read_project_files`/`write_project_files`/
  `run_dev_command`; Computersteuerung erhält OS-Adapter hinter
  `os_control_*`. Grants wandern nie mit dem Datenträger auf einen fremden PC.
- **Modelle**: neue Katalogeinträge in `models/catalog.json` (Repo, Revision,
  Dateien mit Größe/SHA-256, Lizenz, Architektur, Kontext); neue Architekturen
  durch Hochziehen des Runtime-Pins in `build/fetch_runtime.py` und
  `models/supported_architectures.json`.
- **Laufzeitvarianten**: `fetch_runtime.py --variant` (z. B. Vulkan) und
  `InferenceManager` sind auf weitere Backends vorbereitet; V1 liefert nur CPU.
- **Retrieval**: `MemoryRetriever.retrieve` ist die einzige Stelle, die Kontext
  baut; Reranking oder Dokument-Embeddings lassen sich dort ergänzen.
- **Weitere Plattformen**: Pfadlogik, Lock, Runtime-Wrapper sind
  plattformneutral gehalten; nur Job-Objects, CTRL_BREAK und Volume-Abfragen
  sind Windows-spezifisch.
