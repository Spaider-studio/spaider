# Modellbibliothek, Anbieter und Gedächtnis-Clients: Rechercheprotokoll

Stand: 2026-11-11 (Abruf aller Quellen am 2026-11-11 zwischen 10:00 und 10:40
Uhr lokaler Zeit über WebFetch; installierte SDK-Versionen am selben Tag aus
der Entwicklungsumgebung gelesen). Die Beobachtungen hier sind die Grundlage
der Architekturentscheidungen in `MODELHUB_ARCHITECTURE.md`; sie sind keine
Zusagen über Konten, Abos oder installierte Programme des Nutzers.

## 1. Lokaler Ausgangsstand (verifiziert am Code)

| Punkt | Befund |
|---|---|
| Review-Branch | `feature/spaider-foldable-review` bei `84f59c4` (Code = `f281d78`), Arbeitsbaum sauber; neuer Branch `feature/spaider-foldable-modelhub-connectors` im Worktree `repo-modelhub` |
| Upstream | `Spaider-studio/spaider` `main` ist inzwischen `126ef75` (Tag `v0.5.0`): 78 Dateien in `backend/app` geändert (Kompressor, Entity-Resolver, Embedding-Service, Schemas, Config). Foldable bleibt auf der Basis `73762b3`; eine Rebasis auf 0.5.0 ist eine eigene Integrationsaufgabe (Abschnitt 8) |
| Privates Repo | `Useebay/spaider-foldable-private` ist leer; Token-Scopes `gist, read:org, repo` (kein `workflow`) |
| Gemeldeter Leerantwort-Fehler | Reproduzierbar aus den Logs der Nutzerinstanz (`data/logs/llama-chat.log`, `core.log` vom 2026-11-04): Modell `qwen3.8-27b-q4_k_m` (16 GB) wurde mit 36 % der Schichten auf der Arc 140V gestartet; jeder Request endete in `decode() failed: vk::Device::allocateMemory: ErrorOutOfDeviceMemory`. Der llama-server sendet das als SSE-Zeile `data: {"error": …}`; `inference/client.py` wertete nur `choices` aus und lieferte `done` ohne Text → „Antwort fertig“ nach 0,4–1,1 s. Zwei Fehler: Fehlerobjekt im Stream ignoriert; Teil-Offload ohne Reserve für Rechenpuffer |
| Installierte KI-Clients | Claude Code als VS-Code-Erweiterung 2.1.322 mit nativer `claude.exe` (`%USERPROFILE%\.vscode\extensions\anthropic.claude-code-2.1.322-win32-x64\resources\native-binary\claude.exe`); OpenAI Codex-App 26.1103.4239.0 (MSIX) mit `%USERPROFILE%\.codex\config.toml` (enthält Plugins/Marketplaces des Nutzers, **nicht verändert**); Claude Desktop nicht installiert; Ollama und LM Studio nicht installiert (Ports 11434/1234 geschlossen) |
| Python-Bibliotheken (venv) | `httpx 0.28.1`, `openai 2.54.0` (Abhängigkeit von litellm), `litellm 1.106.6`, `cryptography 50.0.2` (enthält Argon2id), `pywin32 312` (DPAPI über `win32crypt`), neu installiert `mcp 2.3.0` |

## 2. Quellen und Befunde

| # | Quelle (Abruf 2026-11-11) | Befund, relevant für die Umsetzung |
|---|---|---|
| S2/S3/S4 | Hugging-Face-Hub-Dokumentation (Suche, Download, GGUF); die Endpunktreferenz ist in das OpenAPI-Dokument `https://huggingface.co/.well-known/openapi.md` umgezogen | `GET /api/models` mit `search`, `author`, `filter` (z. B. `gguf`), `pipeline_tag`, `sort`/`direction`, `limit`; Repo-Info mit `siblings` (Dateien, LFS-Größe/SHA) und `cardData`; Baum-Endpunkt mit `cursor`-Pagination. Die bestehende `models/hub.py` nutzt bereits Suche, Repo-Info, Quantisierungserkennung, Split-Gruppen und Range-Lesen des GGUF-Headers |
| S5 | Gated Models | Freigabe nur im Browser durch den Nutzer; Download danach nur mit Nutzer-Token; `gated` ist `auto`, `manual` oder `false` |
| S6 | Inference Providers Hub-API | `https://router.huggingface.co/v1/models` listet bediente Chat-Modelle mit Provider-Status, Kontext, Preisen, `supports_tools`, `supports_structured_output`; `?expand[]=inferenceProviderMapping` je Modell. Router ist OpenAI-kompatibel; Provider-Wahl per Modellsuffix/Policy. HF-Downloadkonto ≠ HF-Inferenzverbindung |
| S9 | LiteLLM Core | Eigene Distribution `litellm-core` (gleicher Namensraum `litellm`), ohne boto3/tokenizers/huggingface-hub (−32 % Größe). Der Foldable-Kern bündelt bereits `litellm 1.106.6` für die wiederverwendeten Backend-Services. Entscheidung (ADR-1): eigene schlanke httpx-Adapter für den Chat-Pfad, litellm bleibt für die wiederverwendeten Services |
| S10 | DeepSeek Thinking Mode | Base `https://api.deepseek.com`; Thinking standardmäßig an (`thinking: {type: enabled|disabled}` per `extra_body`); Reasoning als `reasoning_content` (Stream: `delta.reasoning_content`); **mit `tools` muss `reasoning_content` aller vorherigen Runden zurückgesendet werden (sonst 400), ohne `tools` wird es ignoriert**; `temperature`/`presence_penalty`/`frequency_penalty` wirkungslos im Thinking-Modus; Modelle z. B. `deepseek-flash`, `deepseek-v4-pro` |
| S11 | Z.ai Quick Start | Base `https://api.z.ai/api/paas/v4/`, `POST …/chat/completions`, `Authorization: Bearer`; Modelle z. B. `glm-5.3`; GLM Coding Plan nutzt einen **gesonderten Endpunkt** (separate Anleitung) → nur Standard-API im Preset, Coding-Plan nicht als stiller Ersatz |
| S12 | Ollama OpenAI-Kompatibilität | `http://localhost:11434/v1/`; `/v1/models`, `/v1/chat/completions`, `/v1/embeddings`, `/v1/responses` (stateless); lokal kein Schlüssel nötig (Platzhalter); Cloud-Modelle über `https://ollama.com/v1` oder im lokalen Server als `…:cloud` → Herkunft prüfen, nicht „vollständig lokal“ nennen; nicht unterstützt u. a. `tool_choice`, `n`, Bild-URLs (nur base64) |
| S13 | LM Studio OpenAI-Kompatibilität | `http://localhost:1234/v1`; `GET /v1/models`, `POST /v1/chat/completions` (Text + Bild), `/v1/responses`, `/v1/embeddings`; Modell-ID aus LM Studio verwenden |
| S14 | Anthropic Messages Streaming | Header `x-api-key`, `anthropic-version: 2026-10-21` (aktueller Wert der Dokumentation); Events `message_start`, `content_block_start` (`text`, `tool_use`, `thinking`), `content_block_delta` (`text_delta`, `input_json_delta` mit `partial_json`, `thinking_delta`, `signature_delta`), `content_block_stop`, `message_delta` (`stop_reason`, `usage.output_tokens`), `message_stop`, `ping`, `error` (`{"type":"error","error":{"type":"overloaded_error",…}}`); unbekannte Eventtypen müssen toleriert werden |
| S15 | OpenAI Responses Streaming | Dokumentationsseite nennt nur `response.created`, `response.output_text.delta`, `response.completed`, `error`; die vollständige Liste stammt aus dem installierten SDK `openai 2.54.0` (`types/responses`): u. a. `response.created`, `response.in_progress`, `response.output_item.added/done`, `response.content_part.added/done`, `response.output_text.delta/done`, `response.refusal.delta/done`, `response.function_call_arguments.delta/done`, `response.reasoning_summary_text.delta`, `response.completed`, `response.failed`, `response.incomplete`, `error` |
| S16–S21 | OpenAI „Sign in with ChatGPT“ (SIWC) | Für Open-Source/lokal gehostete Apps; Registrierung über Autorisierungsanfrage mit `client_id=dynamic_agent_client`, `agent_name_hint`, `ext_agent_host_id` (stabil je Rechner, z. B. `urn:uuid:`); Endpunkte `https://auth.openai.com/api/accounts/authorize` und `…/oauth/token`, Issuer `https://auth.openai.com`; PKCE S256, `state`, `nonce`, Scope `openid profile email offline_access resource.invoke chatgpt.tokens.use.direct`, `resource=https://api.openai.com/v1`; Loopback `http://127.0.0.1:<port>/auth/callback` (kein `localhost`); ID-Token gegen JWKS prüfen (Issuer, Audience = ausgestellte `oaiapp_…`-Client-ID, Nonce); Plan-Berechtigung nur über gewährten Scope `chatgpt.tokens.use.direct`. Modelle: `GET https://api.openai.com/v1/models` (Felder `slug`, `display_name`, `visibility`). Inferenz: `POST /v1/responses` mit `store:false`, `stream:true`, Kontext je Request in `input`; Erfolg nur bei `response.completed`. Nicht unterstützt: `previous_response_id` (HTTP), `max_output_tokens`, `temperature`, `top_p`, `metadata`, `truncation`, `user`, Systemnachrichten als Items (stattdessen `instructions`), gehostete Tools (Image-Generation, File Search, Code Interpreter, Computer Use, hosted MCP); erlaubt: Function/Custom Tools, Text/Bild/Datei-Eingaben. Refresh: `grant_type=refresh_token` + ausgestellte `client_id` + `resource`, Rotation, serialisiert; Logout per `revocation_endpoint` aus `.well-known/openid-configuration`. Fehler: `subscription_sharing_usage_limit_exceeded` (429, warten), `subscription_sharing_usage_unavailable` (503, warten), `invalid_grant`/`refresh_token_*` (erneut anmelden), `subscription_sharing_user_not_eligible` (403, stoppen); **kein stiller Wechsel auf API-Key-Abrechnung**. Kein Zugriff auf ChatGPT-Gespräche |
| S22 | API-Guthaben für Abonnenten | Nur als Anbieterangebot verlinkbar; kontobezogen, nicht im Produkt versprechen (nicht erneut abgerufen) |
| S23 | ChatGPT/Codex MCP | ChatGPT-Desktop-App, Codex CLI und Codex-IDE-Erweiterung teilen eine Konfiguration: `~/.codex/config.toml` mit `[mcp_servers.<name>] command = … args = […]` (STDIO) bzw. Streamable HTTP; Desktop: Settings → MCP servers → Add server; `codex mcp add <name> -- <command>`. ChatGPT-Web nur Remote-MCP über Plugins (keine lokalen Server) |
| S24 | Secure MCP Tunnel | `tunnel-client` (Download aus den Plattform-Tunnel-Einstellungen oder github.com/openai/tunnel-client), benötigt `tunnel_id`, Runtime-API-Key (Tunnels Read+Use), Profile `tunnel-client init --profile … --tunnel-id … --mcp-command "…"`/`--mcp-server-url`, `doctor`, `run`; Limits 600 Req/min, 10 MiB, 120 s; keine öffentliche Plugin-Verteilung; Workspace-/Organisationszuordnung erforderlich |
| S25 | Öffentliche Verteilung lokaler Desktop-Plugins | Gesonderte Zulassung; nicht Teil dieses Auftrags (nicht erneut abgerufen) |
| S26 | MCP Python SDK | PyPI `mcp`; installiert **2.3.0**; Transporte stdio, Streamable HTTP, SSE; Server-API `mcp.server.MCPServer` (2.x, kein `FastMCP`-Modul mehr), Client `mcp.Client`/`ClientSession`, stdio über `mcp.client.stdio`; `LATEST_PROTOCOL_VERSION = 2026-07-28`; Python ≥ 3.11 |
| S27 | Electron safeStorage | Windows: DPAPI; synchrone `encryptString`/`decryptString` sind veraltet (`encryptStringAsync` …), Entfernung in Electron 46 angekündigt; schützt vor anderen Nutzern desselben Rechners, nicht vor Apps derselben Sitzung; nicht portabel |
| S28 | libsodium Argon2id | Nicht erneut abgerufen; umgesetzt mit `cryptography.hazmat.primitives.kdf.argon2.Argon2id` (in `cryptography 50.0.2` enthalten) + AES-256-GCM; Parameter im Prüfbericht gemessen |
| S29 | MCP Security Best Practices (2025-11-25) | Kein Token-Passthrough (nur für den Server ausgestellte Tokens); Sessions nie als Authentifizierung, sichere zufällige Session-IDs, an Nutzer binden; lokale Server bevorzugt stdio oder HTTP nur mit Autorisierungstoken; Origin-/Host-Prüfung gegen DNS-Rebinding; Scope-Minimierung (Start mit Lese-Scopes, Erhöhung gezielt); Consent vor Ausführung lokaler Serverkommandos |
| S30 | OpenAI MCP-Auth | Nicht erneut abgerufen; Remote-OAuth nur mit dokumentiertem Flow (außerhalb des Umfangs dieses Release) |
| S31 | Gemini OpenAI-Kompatibilität (`aistudio.google.com/docs/openai`) | Base `https://generativelanguage.googleapis.com/v1beta/openai/`, API-Key als Bearer; unterstützt Chat Completions mit Streaming, Function Calling, Bild-Eingabe (base64), `response_format`, Embeddings, `models.list`, `reasoning_effort`, `extra_body.thinking_config`; **Beta**, Thinking bei 2.5 Pro/3/4 nicht abschaltbar; Modelle z. B. `gemini-3.8-flash`. Entscheidung (ADR-4): Kompatibilitätsschicht, als Teilunterstützung sichtbar |
| S32 | Mistral Chat Completion / API-Referenz | `POST https://api.mistral.ai/v1/chat/completions`, `Authorization: Bearer`, Felder `model`, `messages`, `stream`, `temperature`, `max_tokens`, `tools`, `response_format` (`text`/`json_object`/`json_schema`); SSE data-only mit `[DONE]`; Modellliste über die separate Models-API |
| S33 | OpenRouter | Base `https://openrouter.ai/api/v1`, Bearer, optionale Header `HTTP-Referer`/`X-OpenRouter-Title`; `GET /api/v1/models`; Provider-Routing per `provider`-Objekt: `order`, `allow_fallbacks`, `only`, `ignore`, `require_parameters`, `data_collection: allow|deny`, `quantizations`, `sort`; Pinning = `order` + `allow_fallbacks:false` |
| S34 | Claude Desktop lokale MCP-Server (Support-Artikel + `modelcontextprotocol.io/quickstart/user`) | Windows-Konfiguration `%APPDATA%\Claude\claude_desktop_config.json` mit `mcpServers: {name: {command, args, env}}`, absolute Pfade, Neustart nötig, Logs unter `%APPDATA%\Claude\logs\mcp*.log`; alternativ `.mcpb`-Erweiterungen |

## 3. Abweichungen zu den Annahmen des Auftrags

- Das MCP-Python-SDK liegt in Version 2.x vor; die Server-Klasse heißt
  `MCPServer`, nicht `FastMCP` (Auftrag und ältere Beispiele nennen FastMCP).
- Die Gemini-Dokumentation ist nach `aistudio.google.com/docs/openai` umgezogen
  (301 von `ai.google.dev`).
- Die Anthropic-Dokumentation nennt als Versionsheader `2026-10-21`; der
  Adapter sendet genau diesen Wert und macht ihn konfigurierbar.
- Die Responses-Streaming-Seite listet die Events nicht vollständig; die
  Vollständigkeit stammt aus dem SDK (2.54.0).
- Hugging-Face-Endpunktdokumentation nur noch als OpenAPI; die bisherige
  Nutzung in `hub.py` entspricht ihr.

## 4. Nicht geprüft / Aktualitätsgrenzen

- Keine Live-Aufrufe gegen Cloud-Anbieter (kein freigegebenes Budget, keine
  Schlüssel des Nutzers verwendet); Protokolle wurden nur gegen lokale
  Testserver und das Request-/Event-Schema der Dokumentation geprüft.
- SIWC-Zulassung für SpAIder Foldable (Open-Source/lokal) ist nicht bestätigt;
  der Adapter bleibt bis zur ersten erfolgreichen Anmeldung durch den Nutzer
  im Zustand „Einrichtung erforderlich“.
- Secure MCP Tunnel: keine Plattform-Tunnel-ID, kein Runtime-Key → nur
  Status/Diagnose vorbereitet.
- Claude Desktop ist nicht installiert; Claude Code (VS-Code-Erweiterung) und
  die Codex-App sind installiert, ein echter Tool-Aufruf mit dem Konto des
  Nutzers wurde nicht ausgeführt (Konto/Kontingent des Nutzers).
