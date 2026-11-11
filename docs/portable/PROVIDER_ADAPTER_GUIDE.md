# Provider-Adapter-Leitfaden

Stand: 2026-11-11 (Core 0.4.0). Wie ein neuer Anbieter in SpAIder Foldable
angebunden wird – ohne Code über ein Preset, mit Code über einen Adapter – und
wie beides mit der gemeinsamen Testschnittstelle geprüft wird.

## Bausteine (`foldable/core/spaider_foldable/providers/`)

| Datei | Inhalt |
|---|---|
| `events.py` | `GenerationRequest`, `GenerationEvent` (Arten `start`, `activity`, `text_delta`, `reasoning_delta`, `tool_call`, `tool_result`, `usage`, `completed`, `cancelled`, `error`), `GenerationError` (Codes `unauthorized`, `forbidden`, `payment_required`, `rate_limited`, `server_error`, `network`, `timeout`, `bad_request`, `not_found`, `unsupported`, `protocol`, `out_of_memory`, `empty_answer`, `credential`, `runtime_not_ready`), `ModelInfo`, `AccessCheck` (vier Stufen) |
| `sse.py` | inkrementeller SSE-/NDJSON-Leser (UTF-8-sicher bei zerschnittenen Chunks) |
| `base.py` | `AdapterConfig`, `ProviderAdapter` (ABC): HTTP-Client je Event-Loop, keine Redirects, `http://` nur für private Adressen mit Freigabe, `map_http_error`, `map_transport_error`, `check_access` |
| `chat_completions.py` | OpenAI-kompatibles Chat-Completions-Streaming (`{"error":…}`-Objekte im Stream → Fehler, leere Antwort → Fehler) |
| `responses.py` | OpenAI Responses-Ereignisse (Stand SDK 2.54.0), SIWC-Fehlercodes |
| `anthropic.py` | Anthropic Messages SSE |
| `presets.py` | `ProviderPreset`, eingebaute Presets, `load_presets(data/providers.local.json)` |
| `registry.py` | `ADAPTERS` (Protokoll → Klasse), `ProviderRegistry.adapter_for(conn)` (Cache je Fingerabdruck), `test_connection`, `LocalRuntimeAdapter` (eingebettetes llama.cpp über dieselben Events) |
| `connections.py`, `credentials.py`, `policy.py`, `siwc.py` | Verbindungen/Fähigkeitsprofile/Freigaben, Schlüsselspeicher, Datenfreigabe-Entscheidung, ChatGPT-Anmeldung |

Der Chat (`chat/service.py`) kennt nur Events. Ein Adapter darf nie Text
„erfinden“: Ohne `text_delta` endet ein Stream in `error(empty_answer)`, nie in
„Antwort fertig“.

## Weg A: Preset ohne Code

Für jeden Dienst, der eines der drei Wire-Protokolle spricht. Datei
`SpAIder\data\providers.local.json` (Liste; Felder von `ProviderPreset`):

```json
[
  {
    "id": "firmenserver",
    "name": "Firmen-LLM (vLLM)",
    "protocol": "chat_completions",
    "base_url": "https://llm.firma.example/v1",
    "base_url_editable": true,
    "auth_methods": ["api_key"],
    "execution": "lan",
    "models_path": "models",
    "completions_path": "chat/completions",
    "param_allowlist": ["temperature", "max_tokens", "stream", "tools", "tool_choice", "stop"],
    "extra": {"static_headers": {"X-Client": "SpAIder"}, "reasoning_param": null},
    "capabilities": {"text": "declared", "vision": "unknown", "tools": "declared"},
    "key_url": "https://llm.firma.example/keys",
    "docs_url": "https://llm.firma.example/docs",
    "data_note_de": "Anfragen bleiben im Firmennetz.",
    "sort": 50
  }
]
```

Regeln: `protocol` ∈ `chat_completions` | `responses` | `anthropic_messages`
(`llama_local` ist reserviert); `param_allowlist` ist die Positivliste der
Anfrageparameter (alles andere wird **nicht** gesendet); `execution` steuert
die Datenfreigabe (`local` nie, `lan`/`cloud` nach Projekt und Zustimmung).
Ein fehlerhafter Eintrag blockiert den Start nicht (Datei wird dann ignoriert).

## Weg B: Adapter mit Code

1. Klasse von `ProviderAdapter` ableiten und drei Methoden implementieren:

```python
class MeinAdapter(ProviderAdapter):
    async def list_models(self) -> list[ModelInfo]: ...
    async def check_access(self, *, probe_model=None, allow_generation=False) -> AccessCheck: ...  # oder Basisklasse nutzen
    async def generate(self, req: GenerationRequest) -> AsyncIterator[GenerationEvent]:
        yield GenerationEvent(kind="start", ...)
        async for chunk in self._stream(...):
            yield GenerationEvent(kind="text_delta", text=...)
        yield GenerationEvent(kind="completed", finish_reason=..., usage=...)
```

2. Fehler nur als `GenerationEvent(kind="error", error=GenerationError(code, message_de, …))`
   bzw. `raise GenerationError` vor Streambeginn; HTTP-Status über
   `map_http_error`, Transportfehler über `map_transport_error` abbilden.
   `message_de` ist nutzerlesbar; Rohdaten gekürzt in `raw`.
3. Protokollnamen in `registry.ADAPTERS` eintragen und ein Preset mit diesem
   Protokoll anlegen.
4. Keine Geheimnisse loggen; Schlüssel nur im Header, nie in der URL.
5. Abbruch: `cancel()` der Basisklasse schließt die Verbindung; der Chat
   beendet den Stream mit `cancelled`.

Beispiel: `anthropic.py` ist der kompakteste vollständige Adapter (ca. 150
Zeilen) und eignet sich als Vorlage.

## Gemeinsame Testschnittstelle

`foldable/core/tests/providers/fakeserver.py` stellt einen deterministischen
lokalen HTTP-Server bereit (`FakeProvider`, `Scripted`, `sse()`, `cc_chunk()`,
`split_bytes()`): Statuscode + Body, SSE-Skripte in Chunks (zerschnittene
UTF-8-Sequenzen), Verbindungsabbruch nach n Chunks, Aufzeichnung aller
Anfragen (Header, Body, Query).

Ein neuer Adapter sollte dieselben Fälle wie `tests/providers/test_protocols.py`
bestehen:

| Fall | Erwartung |
|---|---|
| Erfolg mit Text | `text_delta`… `completed`, `usage` falls geliefert |
| 401 / 403 / 429 (mit `Retry-After`) / 5xx | `error` mit passendem Code, `retryable`, `retry_after_s` |
| JSON-Fehlerobjekt statt Stream | `error`, nicht leere Antwort |
| Fehlerobjekt **im** Stream | `error` (z. B. llama.cpp `ErrorOutOfDeviceMemory` → `out_of_memory`) |
| Fragmentierter Stream (Byte-Grenzen in Mehrbyte-Zeichen) | identischer Text |
| Nur Tool-Aufruf / nur Reasoning ohne Text | `tool_call` bzw. `error(empty_answer)` |
| Abbruch durch Nutzer | `cancelled`, keine weiteren Events |
| Verbindungsabbruch mitten im Stream | `error(network)` mit `partial` |
| Unbekannte Ereignisse | ignoriert, Stream läuft weiter |
| Parameter-Allowlist | nicht erlaubte Felder fehlen im aufgezeichneten Request |
| `http://` an öffentliche Adresse | abgelehnt (`bad_request`) |

Für den Verbindungstest zusätzlich `check_access`: Server erreicht / Zugang /
Modell gelistet / echte Antwort jeweils einzeln prüfbar; „Zugang geprüft“ darf
nur `true` liefern, wenn der Anbieter einen kostenlosen Prüfweg hat
(`free_access_check` im Preset).

## Checkliste vor dem Merge

- [ ] Vertragstests gegen `FakeProvider` grün
- [ ] Verbindungstest zeigt vier Stufen getrennt und deutsche Erklärungen
- [ ] Leere Antwort endet in `error(empty_answer)`
- [ ] Keine Schlüssel in Logs (grep nach dem Testschlüssel in `core.log`)
- [ ] Eintrag in `CONNECT_PROVIDERS_DE.md` (Tabelle + Hinweise) und im Testbericht
      mit ehrlicher Einstufung (echt / Testserver / nur Metadaten)
