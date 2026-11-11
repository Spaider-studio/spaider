# Anbieter und eigene Server anbinden

Stand: 2026-11-11 (Core 0.4.0). SpAIder Foldable braucht kein Konto bei
irgendeinem Anbieter: Der Standard ist das eingebettete llama.cpp mit einem
lokal gespeicherten Modell. Alles in diesem Dokument ist **optional** und wird
unter Einstellungen → **Verbindungen** eingerichtet. Jeder Anbieter wird gleich
behandelt; der ChatGPT-Login ist eine Karte von vielen (siehe
`CONNECT_CHATGPT_DE.md`).

## Grundbegriffe

| Begriff | Bedeutung |
|---|---|
| Anbieter-Preset | Vorlage mit Protokoll, Basis-URL, Anmeldeart, Parameter-Allowlist und Datenhinweis; 13 eingebaut, eigene über `SpAIder\data\providers.local.json` |
| Verbindung | Konkrete Instanz eines Presets mit Name, Basis-URL, Schlüssel-Referenz, Ausführungsort (`local`, `lan`, `cloud`) und Datenfreigabe (`allow`/`ask`/`deny`) |
| Bindung | Pro Gespräch: lokales Modell **oder** Verbindung + Modell-ID; Wechsel jederzeit im Chat-Kopf |
| Projekt | Ordnet Gespräche und Dokumente; „Nur lokal“-Projekte blockieren jede Verbindung mit Ausführungsort ≠ `local` |
| Freigabe | Einmalige Zustimmung je Verbindung × Projekt, bevor Inhalte den Rechner verlassen (bei `ask`); unter Verbindungen widerrufbar |

## Schlüssel speichern

| Speicherort | Was passiert | Wann sinnvoll |
|---|---|---|
| **Dieser Rechner** (Standard) | Windows DPAPI (Benutzerkonto-gebunden) in `SpAIder\data\host\credentials.json`; auf einem anderen Rechner unlesbar | Fester Arbeitsplatz |
| **Portabler Tresor** | `SpAIder\data\profiles\<Profil>\vault.json`, Argon2id (64 MiB, 3 Iterationen, 2 Lanes) + AES-256-GCM, Passwort nur im Arbeitsspeicher; Sperren unter Verbindungen | USB-Stick, mehrere Rechner |
| **Nur Sitzung** | Nur im Speicher des laufenden Kerns; nach dem Beenden weg | Ausprobieren |

Schlüssel werden nie in Logs, Prozessargumente, URLs oder Support-Exporte
geschrieben. Ein Schlüssel ist an Anbieter + Basis-URL gebunden; SpAIder sendet
ihn nie an eine andere Adresse. Für Cloud-Verbindungen ist HTTPS Pflicht; `http://`
ist nur für private Adressen (127.0.0.1, 10.x, 192.168.x, …) mit Ausführungsort
`local`/`lan` erlaubt.

Umgebungsvariablen wie `OPENAI_API_KEY` werden **nicht** gelesen. Schlüssel,
die irgendwo auf dem Rechner liegen, werden nicht benutzt.

## Verbindung testen: vier getrennte Stufen

| Stufe | Frage | Kostet etwas? |
|---|---|---|
| Server erreicht | Antwortet die Adresse überhaupt (TCP/TLS/HTTP)? | nein |
| Zugang geprüft | Wird der Schlüssel akzeptiert (Modell-Liste)? | nein (bei allen eingebauten Presets kostenlos) |
| Modell gelistet | Steht das gewählte Modell in der Liste? (`null`, wenn der Anbieter keine Liste liefert) | nein |
| Echte Antwort | Kurze Probe-Antwort (nur mit Haken „Probe-Antwort senden“) | **kann** beim Anbieter Kosten verursachen |

Jede Stufe hat ein eigenes Ergebnis; ein Fehler nennt Ursache und nächsten
Schritt (z. B. „401 – Schlüssel prüfen“, „Server nicht erreichbar – Adresse/Port
prüfen, läuft der Dienst?“).

## Übersicht der eingebauten Anbieter

Protokolle: `chat_completions` (OpenAI-kompatibel), `responses` (OpenAI
Responses), `anthropic_messages`. „Geprüft“ bezieht sich auf diese Version und
diesen Rechner (siehe `MODELHUB_TEST_REPORT.md`); 0 € Cloud-Ausgaben waren Vorgabe,
deshalb wurde **kein Cloud-Anbieter mit einem echten Konto aufgerufen**.

| Preset | Protokoll | Basis-URL (Standard) | Anmeldung | Ort | Geprüft |
|---|---|---|---|---|---|
| Eingebettetes llama.cpp | intern | – | keine | local | echt (Qwen3-0.6B, Gemma 4 E2B) |
| Eigener Server (OpenAI-kompatibel) | chat_completions | frei | keine / Schlüssel | frei | echt: zweiter llama-server mit `--api-key`, vier Stufen + Chat |
| Ollama | chat_completions | `http://127.0.0.1:11434/v1` | keine / Schlüssel | local | Vertrag (Testserver); Ollama nicht installiert |
| LM Studio | chat_completions | `http://127.0.0.1:1234/v1` | keine / Schlüssel | local | Vertrag (Testserver); LM Studio nicht installiert |
| OpenAI (API-Schlüssel) | responses | `https://api.openai.com/v1` | Schlüssel | cloud | Vertrag (Testserver, Events laut SDK 2.54) |
| ChatGPT-Konto (SIWC) | responses | `https://api.openai.com/v1` | Anmeldung im Browser | cloud | Vertrag (Fake-Issuer); nicht gegen OpenAI geprüft |
| Anthropic Claude | anthropic_messages | `https://api.anthropic.com/v1` | Schlüssel | cloud | Vertrag (Testserver) |
| Google Gemini | chat_completions | `https://generativelanguage.googleapis.com/v1beta/openai` | Schlüssel | cloud | Vertrag (Testserver); Kompatibilitätsschicht laut Google „Beta“ |
| DeepSeek | chat_completions | `https://api.deepseek.com` | Schlüssel | cloud | Vertrag (Testserver; `reasoning_content` wird getrennt gestreamt und nie zurückgesendet) |
| Z.ai / GLM | chat_completions | `https://api.z.ai/api/paas/v4` | Schlüssel | cloud | Vertrag (Testserver) |
| Mistral | chat_completions | `https://api.mistral.ai/v1` | Schlüssel | cloud | Vertrag (Testserver) |
| OpenRouter | chat_completions | `https://openrouter.ai/api/v1` | Schlüssel | cloud | Vertrag (Testserver; Provider-Routing über `extra.adapter.provider`) |
| Hugging Face Inference Providers | chat_completions | `https://router.huggingface.co/v1` | Token | cloud | Vertrag (Testserver) |

„Vertrag (Testserver)“ heißt: Der Adapter wurde gegen einen lokalen Testserver
mit dem dokumentierten Wire-Format geprüft (Erfolg, 401/403/429/5xx, JSON statt
Stream, fragmentierter Stream, Tool-Aufruf, Reasoning-only, Abbruch,
Verbindungsabbruch, unbekannte Ereignisse). Ob ein Anbieter heute genau so
antwortet, zeigt erst der Verbindungstest mit Ihrem Schlüssel.

## Anbieter-spezifische Hinweise

- **Eigener Server**: Basis-URL **ohne** blindes `/v1`-Anhängen – tragen Sie die
  Adresse so ein, wie Ihr Server sie erwartet (`http://192.168.1.20:8080/v1` für
  llama.cpp/vLLM/LocalAI, `…/v1` auch bei Ollama/LM Studio). Ausführungsort `lan`
  für Server im eigenen Netz.
- **Ollama / LM Studio**: Dienst zuerst starten; der Test „Server erreicht“
  sagt sonst sofort, dass der Port geschlossen ist.
- **OpenAI**: Responses-API mit Streaming; `store` ist standardmäßig `false`.
  Unterstützte Parameter werden per Allowlist gefiltert.
- **ChatGPT-Konto**: separate Karte; Abo ≠ API-Schlüssel; Details in
  `CONNECT_CHATGPT_DE.md`.
- **Anthropic**: Header `anthropic-version` ist gepinnt; `max_tokens` wird immer
  gesetzt (Pflichtfeld).
- **Gemini**: OpenAI-Kompatibilitätsschicht (Beta laut Google). Die native
  Gemini-API ist nicht angebunden – dokumentierte Entscheidung in
  `MODELHUB_ARCHITECTURE.md` (ADR-3).
- **DeepSeek**: `reasoning_content` wird als Denktext angezeigt, nicht
  gespeichert und bei Folgeanfragen nicht mitgesendet (laut DeepSeek-Doku
  Pflicht).
- **OpenRouter**: optionaler Aggregator; Routing-Vorgaben (`provider.order`,
  `allow_fallbacks`) über das Feld `extra.adapter.provider` der Verbindung.
- **Hugging Face**: Router-Endpunkt; Modell-IDs im Format `org/modell[:provider]`.

## Was beim Senden passiert

Vor der ersten Anfrage an eine Verbindung (bei `ask`) zeigt SpAIder, **was**
gesendet würde: Anzahl Verlaufsnachrichten, ob ein Systemprompt dabei ist,
Zeichen aus dem Gedächtnis, Anzahl Quellen und Bilder. Erinnerungen aus
„Nur lokal“-Projekten werden für Cloud-/LAN-Verbindungen **gar nicht erst
abgerufen** (Kanarien-Test `tests/test_cloud_isolation.py`). Nach der Antwort
steht am Chat-Kopf, welche Verbindung und welches Modell geantwortet haben; jede
Nachricht speichert `connection_id` und `provider_model`.

Fehler werden nie als „Antwort fertig“ verkauft: Eine leere Antwort, ein
abgebrochener Stream oder ein Anbieterfehler endet mit Code, Erklärung und
nächstem Schritt; bereits empfangener Text bleibt sichtbar und ist als
unvollständig markiert.

## Eigenen Anbieter ohne Code ergänzen

`SpAIder\data\providers.local.json` (Liste von Preset-Objekten, Felder wie in
`PROVIDER_ADAPTER_GUIDE.md`):

```json
[
  {"id": "firmenserver", "name": "Firmen-LLM (vLLM)", "protocol": "chat_completions",
   "base_url": "https://llm.firma.example/v1", "auth_methods": ["api_key"],
   "execution": "lan", "data_note_de": "Anfragen bleiben im Firmennetz.",
   "docs_url": "https://llm.firma.example/docs"}
]
```

Nach dem nächsten Start erscheint die Karte unter „Anbieter hinzufügen“.
