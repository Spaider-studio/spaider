# Modellbibliothek, Anbieter-Anbindung und portables Gedächtnis – Architektur und ADR

Stand: 2026-11-11, Branch `feature/spaider-foldable-modelhub-connectors`.
Grundlage: `MODELHUB_RESEARCH.md` (Quellen, Abrufdaten) und der bestehende Kern
(`ARCHITECTURE.md`, `AGENT_LIVE_ARCHITECTURE.md`). Dieses Dokument beschreibt
den **gewählten** Weg; was davon in diesem Release tatsächlich ausgeführt und
geprüft ist, steht in `MODELHUB_TEST_REPORT.md`.

## 1. Ziel

SpAIder Foldable bleibt eine lokale, portable App (entpacken, starten, Modell
wählen). Neu ist eine anbieterneutrale Ausführungsschicht: dieselben
Gespräche, Fakten, Dokumente und Rezepte können mit (A) lokalen Modellen,
(B) eigenen Schlüsseln/Servern, (C) offiziell unterstützten Konto-Zugängen
und (D) aus fremden KI-Anwendungen über MCP genutzt werden. ChatGPT ist dabei
eine Anbieterkarte unter mehreren; der Kern setzt keinen Cloud-Anbieter voraus.

## 2. Architekturentscheidungen (ADR)

| ADR | Entscheidung | Verworfen | Gründe |
|---|---|---|---|
| ADR-1 Adapter | Schlanke eigene Adapter je **Wire-Protokoll** auf `httpx`: `chat_completions` (OpenAI-kompatibel: llama.cpp, DeepSeek, Z.ai, Mistral, OpenRouter, Ollama, LM Studio, HF-Router, Gemini-Kompatibilitätsschicht, eigene Server), `responses` (OpenAI API-Key, SIWC), `anthropic_messages`. Anbieter sind **Presets** über diesen drei Protokollen | LiteLLM (voll oder `litellm-core`) als Chat-Pfad; separater LiteLLM-Gateway-Prozess | Volle Kontrolle über SSE-Parsing, Abbruch (Verbindung schließen), Fehler-Events und Tests mit deterministischen Testservern; keine zweite Abstraktionsschicht mit eigenen Event-Formaten; keine Paketvergrößerung. `litellm` bleibt gebündelt, weil die wiederverwendeten Backend-Services (Kompressor, Resolver) es nutzen – nur gegen den **lokalen** Server |
| ADR-2 Ereignisvertrag | Ein interner Strom `GenerationEvent` (`start`, `activity`, `text_delta`, `reasoning_delta`, `tool_call`, `tool_result`, `usage`, `completed`, `cancelled`, `error`) aus jedem Adapter; `ChatService` konsumiert nur diesen Strom. Die äußere SSE-API bleibt kompatibel (`token`, `thinking`, `activity`, `sources`, `done`, `error`) | Provider-Objekte bis in Renderer/Gedächtnis durchreichen | Responses-Items, Anthropic-Blöcke und Gemini-Felder enden an der Adaptergrenze; neue Presets brauchen keine Änderung an Memory, Renderer oder Planer |
| ADR-3 Verbindungen und Schlüssel | `ProviderConnection` in SQLite (kein Secret im Datensatz, nur `credential_ref`). `CredentialStore` mit zwei Fächern: **host-gebunden** (Windows DPAPI über `win32crypt`, Standard) und **portabler Tresor** (`vault.json`, Argon2id + AES-256-GCM, nur auf ausdrücklichen Wunsch, Passwort pro Sitzung im Speicher) | Electron `safeStorage` als einziger Speicher; eigene Kryptografie | Der Kern braucht die Schlüssel (nicht der Launcher); DPAPI ist dieselbe OS-Primitive wie `safeStorage` auf Windows; Argon2id/AES-GCM aus `cryptography` sind etablierte Bausteine |
| ADR-4 Gemini | OpenAI-Kompatibilitätsschicht (`…/v1beta/openai/`), als **Teilunterstützung (Beta)** markiert | Native Gemini-API-Adapter | Deckt Chat, Streaming, Tools, Bild, JSON-Schema und Modellliste ab; ein nativer Adapter wäre ein viertes Protokoll ohne Mehrwert für diesen Release. Grenzen (Thinking nicht abschaltbar, Beta) sichtbar im Fähigkeitsprofil |
| ADR-5 Profile | Profil = Unterordner `data/profiles/<uuid>/` mit eigener SQLite, Modellen, Importen, Downloads, Caches; Registry `data/profiles.json`; **ein** `AppContext` je aktivem Profil, Wechsel = geordneter Abbau (Streams, Aufgaben, Worker, Jobs, Modellprozesse, DB) und Neuaufbau mit neuer Sitzungsgeneration; Bestandsdaten werden beim ersten Start **in das erste Profil verschoben** (Umbenennen im selben Volume, Journal gegen Teilzustände) | Profil als Spalte in einer gemeinsamen DB; gemeinsame Modellablage | Strikte Trennung (keine Fremdschreiber, keine Queue-Übertritte), einfache Kopier-/Löschsemantik je Profil, unveränderte Store-Implementierungen. Nachteil: Modelle werden nicht zwischen Profilen geteilt (dokumentiert) |
| ADR-6 MCP-Brücke | Derselbe gepackte Kern-Prozess in einem zweiten Modus `spaider-foldable-core.exe --mcp-stdio --connector <datei>`: ein stdio-MCP-Server (`mcp 2.3.0`, `MCPServer`), der Tool-Aufrufe über die Loopback-HTTP-API des **laufenden** Kerns ausführt (Connector-Token, an Profil und Projekte gebunden) | Zweiter SQLite-Schreiber im Brückenprozess; eigener JSON-RPC; separates Bundle | Ein Schreiber (der Kern), keine zusätzliche Sprachlaufzeit beim Endnutzer, Autorisierung serverseitig im Kern. Streamable-HTTP-Transport mit Bearer-Token als optionaler zweiter Weg über denselben Vertrag |
| ADR-7 Konto-Zugänge | Zugangsmethoden je Preset deklariert (`none`, `api_key`, `oauth_siwc`); SIWC als eigener Adapter (`providers/siwc.py`) über dem Responses-Protokoll mit Allowlist-Profil; andere Abos werden **nicht** nachgebaut | Browser-Automation, Cookie-Import, inoffizielle Endpunkte | Nur offiziell dokumentierte Wege; alles andere wäre Umgehung |
| ADR-8 Datenfreigabe | Projekte als leichte Zuordnung (Tabelle `projects`, Spalte `project_id` auf Gesprächen/Dokumenten, Standardprojekt „Allgemein“, Flag `local_only`); Freigaben je (Verbindung, Projekt) mit Sichtbarkeit und Widerruf; Hintergrund-Extraktion/Embeddings bleiben auf dem lokalen Server | Globaler Schalter „Cloud erlaubt“ | Ein Chatmodellwechsel schaltet keine Hintergrundjobs um; „Nur lokal“-Projekte blockieren alle Cloud-Adapter inkl. Titel-/Zusammenfassungsaufrufe |

## 3. Datenverträge (Kern, `spaider_foldable/providers/`)

```
CatalogEntry (bestehend, erweitert um source_kind "search"/"api", task_tags, variants)
InstalledArtifact = Registry-Zeile (bestehend): relative Pfade, Größen, SHA-256, Revision, Architektur, Aktivierung
ProviderConnection {id, profile_id, provider (preset id), protocol, base_url (exakt, kein /v1-Anhängen),
                    auth_method none|api_key|oauth_siwc, credential_ref, account_label, workspace,
                    execution local|lan|cloud|unknown, data_sharing allow|ask|deny, extra (JSON), created_at}
ModelBinding {kind: "local"|"connection", model_id | connection_id + provider_model_id}
CapabilityProfile {text, vision, structured_output, tool_calls, streaming, context_length,
                   parameters: {name: declared|verified|unknown}, source: declared|verified|unknown, checked_at}
MemoryContext (bestehend: Retriever-Ergebnis mit Quellen-IDs, Gültigkeit, Budget)
GenerationEvent {kind, text, reasoning, tool_call{id,name,arguments}, usage{...}, error{code,message_de,retryable},
                 finish_reason, provider_state (opak, nie ins Gedächtnis)}
RunIdentity {profile_id, project_id, conversation_id, run_id, connection_id, generation}
```

Adapter-Schnittstelle (`providers/base.py`):
`list_models()`, `check_access()`, `capabilities(model)`, `generate(request) -> AsyncIterator[GenerationEvent]`,
`cancel()` (Verbindung schließen). Presets (`providers/presets.py`) deklarieren Protokoll, Basis-URL,
Zugangsmethoden, Modelllisten-Endpunkt, Parameter-Allowlist, Fähigkeitsvorgaben und Testweg.

## 4. Datenflüsse

- **Chat**: `ChatService.send` → Bindung des Gesprächs (lokal oder Verbindung) → Freigabeprüfung
  (Projekt `local_only`, `data_sharing`) → Prompt aus kanonischem Verlauf (keine fremden
  Provider-IDs, kein opaker Reasoning-Zustand) → Adapter → `GenerationEvent` → SSE. Eine
  Antwort ohne Text und ohne Tool-Aufruf und ohne Fehler wird als Fehler (`empty_answer`)
  gemeldet, nie als „Antwort fertig“. Gespeichert werden Modell/Verbindung je Nachricht.
- **Lokaler Fehlerpfad**: `data: {"error": …}` des llama-server → `error`-Event; bei
  `ErrorOutOfDeviceMemory` schlägt der Kern den CPU-/Teil-Offload-Rückfall vor und merkt die
  GPU-Kombination für dieses Modell.
- **Katalog**: eingebettet (Offline-Start) + `catalog.local.json` + Hub-Suche (paginiert,
  Filter, Cache mit Alter, Debounce/Coalescing im Kern, `Retry-After`) → Varianten je Repo
  gruppiert → Karte mit Belegen. Metadaten sind Daten (Schema-/Größenlimits, Markdown ohne
  Skripte, keine Endpunkte daraus).
- **MCP**: Client → stdio-Brücke → `POST /api/connectors/{id}/call` (Token) → Kern prüft Profil,
  Projekte, Rechte → Retriever/Store → Ergebnis mit Quellen-IDs. Standard lesend; Schreiben nur
  mit `write`-Recht; `forget` nur mit `forget`-Recht; Idempotenz über `client_request_id`.
- **Profilwechsel**: `POST /api/profiles/{id}/activate` → Generation+1, laufende Streams/Tasks
  abbrechen, Worker/Jobs stoppen, Modellprozesse beenden, DB schließen → neuen Kontext öffnen →
  Ereignis `profile` (alte Generation wird vom Renderer verworfen).

## 5. Sicherheit

- Schlüssel sind an Preset + Origin gebunden; keine Weitergabe bei Redirects auf fremde Hosts;
  Cloud nur TLS; Loopback/LAN als ausdrücklich gewählter Modus; kein blindes `/v1`-Anhängen.
- Verbindungstest unterscheidet `server_reached`, `access_ok`, `model_listed`, `answer_ok`.
- Connector-Tokens sind eigene Secrets (nur Hash im Kern), nicht die Provider-Schlüssel; der
  Kern prüft `Origin`/`Host` auf Loopback; Session-IDs sind keine Autorisierung.
- Keine Secrets in Logs, Prozessargumenten, URLs oder Supportexporten (Redaktion in `log_redact`).

## 6. Grenzen dieses Releases

Siehe `KNOWN_LIMITATIONS.md` (Abschnitt Modellbibliothek/Anbieter) und `MODELHUB_TEST_REPORT.md`:
Cloud-Adapter sind gegen Testserver geprüft, nicht live; SIWC und Tunnel bleiben
„Einrichtung erforderlich“; Profile teilen keine Modelle; Gemini nur über die
Kompatibilitätsschicht.
