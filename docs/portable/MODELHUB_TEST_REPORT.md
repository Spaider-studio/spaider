# SpAIder Foldable 0.4.0: Testbericht Modellbibliothek, Anbieter, Gedächtnis

Stand: 2026-11-11. Rechner: Windows 11 Enterprise 26200, 32 GB RAM (zur
Testzeit nur ca. 6 GB frei), Intel Arc 140V (Vulkan), kein Admin, kein Docker.
Laufzeit llama.cpp b12032 (CPU + Vulkan). Einstufung je Zeile:

- **echt** – mit realem Modell/Prozess/Client ausgeführt
- **Testserver** – gegen lokalen Fake-Server mit dokumentiertem Wire-Format
- **Metadaten** – nur Katalog/Header/Konfiguration geprüft
- **blockiert** – durch Vorgabe (0 € Cloud, keine fremden Konten, kein unbeaufsichtigter OAuth) oder Umgebung nicht möglich
- **nicht ausgeführt** – aus Zeit-/Ressourcengründen

Rohdaten: `C:\Users\mucbay\spaider-foldable\measure\modelhub\` (drei Läufe
`real_modelhub_*.json/.log`, `package_check_0.4.0.json`, `real_regression_0.4.json`),
Kern-Suite `pytest tests` (**129 bestanden, 6 übersprungen** ohne Modell),
Desktop `npm test` (**104 Tests**).

## Matrix T1–T17

| # | Prüfung | Einstufung | Ergebnis |
|---|---|---|---|
| T1 | Leerantwort-Fehler: Ursache, Reproduktion, Fix | **echt** (Ursache aus Nutzer-Logs) + Vertragstest | Ursache: llama-server meldet `ErrorOutOfDeviceMemory` als SSE-Fehlerobjekt; der alte Client ignorierte Objekte ohne `choices` → „Antwort fertig“ nach 0,4–1,1 s ohne Text. Jetzt `error(out_of_memory)` mit nächstem Schritt, Modell für CPU vorgemerkt. Tests `test_cc_stream_error_object_like_llama_oom`, `test_cc_reasoning_only_end_is_an_empty_answer_error`. Lokaler Chat über die neue Event-Schicht echt: 3 Läufe Qwen3-0.6B (Vulkan) erster Token 0,11/0,16/0,17 s (Lauf 1; Lauf 2 mit vollem Gedächtnis 0,39/1,27/1,58 s), Stopp: Status `stopped`, 45 Token Teiltext erhalten. 27B-Wiederholung: **nicht ausgeführt** (RAM). |
| T2 | Modellbibliothek: Katalog + Live-Suche, Modellkarte, Varianten | **echt** (Hugging Face live, 2026-11-11) | `discover`/`repo`/`card` gegen den Hub: qwen3 paginiert, GLM (zai-org offiziell), deepseek, gemma (gated sichtbar), mistral, Nicht-GGUF; Varianten mit Projektor; Karte bereinigt; Offline-Fallback aus dem Cache. UI: vier Bereiche gegen den Mock, Screenshots `scratchpad/shots-hub`. |
| T3 | Download/Import/Aktivierung | **echt** | Download `Qwen/Qwen3-0.6B-GGUF` Q8_0 (gepinnte Revision, SHA-256 geprüft) 639 MB in 325 s (~5 MB/s), Aktivierung 2,0–2,7 s auf Vulkan; GGUF-Import Gemma 4 E2B (2,84 GB) Kopie 5,2 s, Aktivierung 4,8–6,8 s; Wechsel zurück 2,5–2,8 s. Resume/Abbruch/Gate: Tests `test_jobs.py`, Gate über Hub-Fehler `gated` (Metadaten). |
| T4 | Protokoll-Vertragstests | **Testserver** | 28 Tests `tests/providers/test_protocols.py`: chat_completions, responses, anthropic_messages – Erfolg, 401/403/429(+Retry-After)/5xx, JSON statt Stream, Fehlerobjekt im Stream, fragmentiertes UTF-8, Tool-only, Reasoning-only, Abbruch, Verbindungsabbruch, unbekannte Events, Allowlist, HTTPS-Pflicht, Zugangsprüfung in vier Stufen inkl. Probe-Rückfall auf das erste Modell. |
| T5 | Echte Inferenz über eigenen Endpunkt | **echt** | Zweiter llama-server (Qwen3-0.6B, CPU, `--api-key`) als Preset „Eigener Server“: Server erreicht ✓, Zugang ✓, Modell gelistet ✓, echte Antwort ✓ (Lauf 3; in Lauf 2 lieferte die Probe nur Denktext → jetzt eigene Meldung mit Hinweis `--reasoning-budget 0`). Falscher Schlüssel: Zugang ✗ (401). Chat über die Verbindung: Antwort mit `connection_id`/`provider_model`; Gedächtnis aus dem lokalen Chat wurde mitgeliefert (12 Quellen, „Fennek“ enthalten; Antworttext in Lauf 2 „Fennek.“). Latenz erster Token 10–37 s (CPU-Server mit 836–1150 Prompt-Token bei 125–140 Token/s Prompt-Verarbeitung, parallel zum Vulkan-Modell). |
| T6 | Freigabe-Dialog, Nur-lokal-Projekt, Fehlerzustände | **echt** (API) + UI gegen Mock | `test_cloud_isolation.py`, `test_store_policy.py`: `consent_required` ohne Freigabe, `local_only` blockiert vor dem Versand, Fehler nie gefolgt von `done`, Teiltext markiert. UI: Freigabedialog, „Lokales Modell verwenden“, Fehlerkarte mit nächstem Schritt (Mock-Lauf, 0 Konsolenfehler). |
| T7/T8 | Profile und Isolation | **echt** | Lauf 1–3: Profil B sieht 0 Treffer, 0 Modelle, 0 Verbindungen; zurück zu A: Treffer da. Wechsel A→B 0,78 s, B→A 0,14–0,17 s (ohne Modell-Neuladen; Modell des Zielprofils lädt bei Bedarf, 2–7 s). Migration 0.3→Profil: `test_profiles.py` (Journal, Wiederaufnahme). |
| T9 | MCP-Brücke mit echten Clients | **echt** (SDK-Client, Claude Code) / **Konfiguration** (Codex) / **blockiert** (Claude Desktop nicht installiert) | SDK-Client gegen echte Brücke + echten Kern: 2,6–2,7 s Rundlauf inkl. Prozessstart, Treffer mit echtem Gedächtnis (`status.facts` 6→24), Schreibrecht korrekt verweigert. Claude Code 2.1.322: `claude mcp add -s local` → `claude mcp list` „✓ Connected“ (isoliertes `CLAUDE_CONFIG_DIR`, danach entfernt, kein Modellaufruf). Codex-CLI 0.167: `codex mcp list/get` zeigen den Server (isoliertes `CODEX_HOME`), kein Handshake. Paketierte Brücke: siehe T14. |
| T10 | Codex-Nutzertest (5 Minuten) | **vorbereitet** | Schnipsel + Schritte in `CONNECT_MEMORY_CLIENTS_DE.md`; `~/.codex/config.toml` des Nutzers unverändert. |
| T11 | Kanarien-Leck-Test | **echt** (Fake-Cloud auf Loopback) | `CANARY-7731` aus einem „Nur lokal“-Projekt erscheint in **keiner** Anfrage an die Fake-Cloud-Verbindung; Fakt aus dem offenen Projekt wird gesendet; Chat im Nur-lokal-Projekt endet mit `local_only` ohne Anfrage. |
| T12 | Portabilität (Umzug, Laufwerksbuchstabe) | **echt** (Regression 0.3-Suite) | `test_real_e2e.py` (subst-Laufwerk, Leerzeichen, Umlaute): bestanden (Lauf 3: Fälle Neustart-Persistenz, Gedächtnis über Gespräche, Korrektur, Dokument-Import mit entfernter Originaldatei, Modellwechsel-Fehler mit Rollback, Stopp, Lebenszyklus, Umzug auf anderes Laufwerk per `subst`, danach erneuter Start). Lauf 1 scheiterte am letzten Fall (Test erwartete `data/spaider.db`, liegt seit 0.4 im Profilordner – Test angepasst), Lauf 2 an der Dokument-Antwort des 0,6B-Modells (Modellqualität; Quellen waren vorhanden). Messungen Lauf 3: Kernstart 7,0 s, Aktivierung 2,1 s, erster Token 2,4 s bei 515 Prompt-Token (215 Token/s Prompt, 42 Token/s Ausgabe, Vulkan), Herunterfahren 1,7–2,1 s. |
| T13 | Tresor / Schlüsselspeicher | **echt** (Paket) + Tests | `test_credentials.py` (Argon2id-Parameter, AEAD-AAD, falsches Passwort, Sperren); Paketprüfung: Tresor anlegen/sperren/falsches Passwort/entsperren über die API des gepackten Kerns. DPAPI-Host-Speicher auf diesem Rechner verfügbar. |
| T14 | Frisches Paket | **echt** | ZIP `SpAIder-Foldable-0.4.0-win-x64.zip`, __ZIP_BYTES__ Bytes, SHA-256 `__ZIP_SHA__`; `verify_package.py` + `package_check_0.4.0.json`: __PKG_CHECK__ |
| T15 | Regression Agent/Live/Offline/GPU-Rückfall (0.3-Funktionen) | **echt** | `test_real_offline.py` (strikter Offline-Modus), `test_real_activity.py` (Aktivitätsereignisse), `test_real_live.py` (Live-Bildschirmhilfe/Overlay-API) und `test_real_gpu_fallback.py` (GPU-Start scheitert → CPU-Fortsetzung) **bestanden** mit Qwen3-0.6B auf dem 0.4-Kern; `test_real_e2e.py` bestanden (siehe T12). Keine Funktionsregression der 0.3-Funktionen gefunden. |
| T16 | Anbieterunabhängigkeit | **echt** | Kern ohne jeden Schlüssel gestartet (Umgebungsvariablen werden nie gelesen), frisches Profil ohne Verbindungen, lokaler Chat/Gedächtnis/Brücke funktionieren; eigener Anbieter per `providers.local.json` (`test_store_policy.py`). |
| T17 | Wissen über Modellfamilien | **echt** | Fakt mit Qwen3-0.6B gespeichert → Gemma 4 E2B antwortet „Mein Lieblingstier ist der Fennek und ich wohne in Bremen.“ (erster Token 1,4–1,6 s, 5 Quellen); Fakt mit Gemma gespeichert → Qwen3 antwortet „Meine Lieblingsfarbe ist Türkis.“; dieselben Fakten über die MCP-Brücke und über die Verbindung „Eigener Server“ abrufbar. |

## Anbieter – ehrliche Einstufung je Preset

| Preset | Einstufung | Bemerkung |
|---|---|---|
| Eingebettetes llama.cpp | echt | Qwen3-0.6B, Gemma 4 E2B (0.4); Qwen3-4B, Qwen3-VL-4B (0.2/0.3) |
| Eigener Server (OpenAI-kompatibel) | echt | zweiter llama-server mit API-Schlüssel |
| Ollama, LM Studio | Testserver | nicht installiert; Preset-URLs laut Doku |
| OpenAI (API-Schlüssel) | Testserver | Responses-Events laut SDK 2.54 |
| ChatGPT-Konto (SIWC) | Testserver (Fake-Issuer) | nicht gegen OpenAI; Zulassung offen |
| Anthropic, Gemini (Kompat.), DeepSeek, Z.ai, Mistral, OpenRouter, Hugging Face | Testserver | 0 € Vorgabe; kein Konto benutzt |

## Leistung und Regressionen

| Messung | 0.3 (Referenz) | 0.4 | Bewertung |
|---|---|---|---|
| Kernstart bis `ready` (Dev, ohne Modell) | ~4–5 s | 5,1 s (frisch), 8,8 s (mit Daten) | gleich; Profil-Registry + Migrationsprüfung < 0,2 s |
| Modellaktivierung Qwen3-0.6B (Vulkan) | ~2–3 s | 2,0–2,7 s | gleich |
| Erster Token lokal (0.6B, leeres Gedächtnis) | ~0,15 s | 0,11–0,17 s | gleich; kein zusätzlicher Arbeitsschritt im kritischen Pfad (Event-Schicht ist ein dünner Wrapper) |
| Erster Token lokal (0.6B, 14+ Fakten im Gedächtnis) | – | 0,39–1,58 s | Prompt wächst mit dem Gedächtnisblock (bekannt aus 0.3) |
| Profilwechsel | – | 0,14–0,78 s | neu |
| Brücke (Prozessstart + Handshake + 4 Aufrufe) | – | 2,6–2,7 s | neu |
| Gedächtnis-Extraktion (0.6B) | 20–30 s je Fakt-Charge | 24–31 s | gleich |
| Kern-Suite | 94 Tests | 129 Tests | +35, Laufzeit 55 s |

Keine Regression im lokalen Pfad gemessen; Zahlen sind Einzelläufe (Median nur
bei T1 über drei Läufe), keine 60-Lauf-Matrix wie in 0.3.

## Was nicht geprüft wurde (bewusst)

- Kein Cloud-Anbieter mit echtem Konto; kein ChatGPT-Login gegen OpenAI; kein
  Tunnel. Keine fremden Kontingente verbraucht, keine OAuth-Zustimmung
  automatisiert.
- Claude Desktop (nicht installiert), Codex-Handshake (bräuchte Modellsitzung).
- 27B-Modell (RAM), Vektorindex in der Connector-Suche (kein Embedding-Modell im
  Testprofil → lexikalisch).
- 60-Lauf-Leistungsmatrix aus 0.3 wurde nicht wiederholt.
