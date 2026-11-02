# SpAIder Foldable: Modellkompatibilität

Stand: 2026-11-02 (Core 0.2.0). Was die gebündelte Laufzeit kann, welche
Modellfamilien der Katalog führt, was davon tatsächlich ausgeführt wurde und
was nur nach Metadaten geprüft ist. Ausführungsergebnisse stehen in
`TEST_REPORT.md`; diese Datei nennt die Quelle jeder Angabe.

## Laufzeit

| | |
|---|---|
| Engine | llama.cpp `llama-server`, Build **b12032** (2026-11-01), gepinnt in `foldable/build/fetch_runtime.py` |
| Varianten im Paket | `resources/runtime/llama` (CPU x64, SHA-256 `5296f5d1…d0d4`), `resources/runtime/llama-vulkan` (Vulkan x64, SHA-256 `19a8bbf2…cc32`) |
| Backend-Wahl | automatisch: Vulkan, wenn `llama-server --list-devices` der Vulkan-Variante ein Gerät mit ≥ 1 GB freiem Speicher meldet und das Modell (geschätzt) hineinpasst; sonst CPU. Scheitert der GPU-Start, wird dieselbe Sitzung auf CPU fortgesetzt und die GPU für diese Hardware gemerkt. |
| Geprüfte GPU | Intel Arc 140V (Lunar Lake iGPU, Treiber 32.0.101.8860) über Vulkan: **verifiziert** (0.6B-Modell: 64 Token/s GPU vs. 26,5 CPU). Andere GPUs (NVIDIA, AMD): Erkennung und CPU-Rückfall sind getestet, GPU-Beschleunigung dort **nicht verifiziert**. CUDA/ROCm/SYCL-Varianten sind nicht gebündelt. |
| Server-Funktionen genutzt | OpenAI-kompatibles Streaming, `cache_prompt` + `--cache-reuse 256`, `return_progress`, `timings`, `/tokenize`, `/props`, `--reasoning-format deepseek`, `chat_template_kwargs`, `--embeddings`, `--api-key`, `--prio`, `-t/-tb`, `-ngl` |
| Format | GGUF v2/v3, Einzel- oder Split-Dateien (`-00001-of-0000N`), Architekturliste des Builds in `models/supported_architectures.json` (167 Einträge) |

## Adaptervertrag

`inference/manager.py` (Laden/Entladen, Backend-Wahl, Rückfall), `inference/client.py`
(Streaming, Abbruch durch Verbindungsabbau, Embeddings, Tokenize, Fortschritt),
`models/capabilities.py` (Fähigkeiten aus GGUF-Header + `families.json`):

| Fähigkeit | Quelle |
|---|---|
| `chat_template` | GGUF `tokenizer.chat_template` vorhanden |
| `reasoning_toggle` | Familie kennt einen Umschalter (`enable_thinking`) **und** die ausgelieferte Vorlage enthält ihn; sonst `null` (kein Denkmodus, UI zeigt keinen Denktext) |
| `system_role` | Familie (Gemma-Vorlagen kennen keine Systemrolle; llama.cpp hängt den Systemtext an die erste Nutzernachricht) |
| `context_length` | GGUF `<arch>.context_length` (Katalog: Herstellerangabe) |
| `modalities` | nur Text; Bildprojektoren (`mmproj`) werden nicht geladen |
| `structured_output` | immer (Grammatik-/JSON-Schema-Sampling des Servers), vom Gedächtnis derzeit nicht genutzt |
| `backends` | gebündelte Runtime-Varianten |
| `type` | chat / embedding (Pooling-Metadaten oder Embedding-Architektur) / base (keine Chatvorlage) / vision / audio |

Modelle vom Typ base, vision, audio werden beim Import und bei der Auflösung
abgelehnt bzw. als ungeeignet markiert; Tool-Calling-Fähigkeiten aktivieren
keine Werkzeuge.

## Katalog (eingebaut, `catalog.json` v2)

| Eintrag | Familie / Architektur | Quelle | Lizenz (Art) | Status |
|---|---|---|---|---|
| Qwen3 0.6B Q8_0 | Qwen3 / `qwen3` | Qwen (offiziell) | Apache-2.0 (open-source) | **ausgeführt** (CPU und Vulkan, b12032) |
| Qwen3 1.7B Q8_0 | Qwen3 / `qwen3` | Qwen (offiziell) | Apache-2.0 | Metadaten |
| Qwen3 4B Q4_K_M | Qwen3 / `qwen3` | Qwen (offiziell) | Apache-2.0 | **ausgeführt** (CPU; Nutzerlauf + Baseline-Messung) |
| Qwen3 4B Q8_0, 8B Q4_K_M, 8B Q6_K | Qwen3 / `qwen3` | Qwen (offiziell) | Apache-2.0 | Metadaten |
| Qwen3.8 27B Q4_K_M | Qwen3.8 / `qwen35` | ggml-org (Konvertierung von Qwen/Qwen3.8-27B) | Apache-2.0 | Metadaten; nicht ausgeführt (CPU zu langsam, ~22 GB RAM) |
| Qwen3.5 0.8B Q8_0 | Qwen3.5 / `qwen35` | ggml-org (Konvertierung) | Apache-2.0 | Metadaten |
| SmolLM3 3B Q4_K_M | SmolLM3 / `smollm3` | ggml-org (Konvertierung von HuggingFaceTB) | Apache-2.0 | Metadaten |
| Llama 3.2 1B Instruct Q4_K_M | Llama / `llama` | bartowski (Konvertierung von meta-llama) | Llama 3.2 Community (open-weights) | **ausgeführt** (Vulkan; Import → Aktivierung → Chat/Korrektur → Stopp → Kalibrierung: 71 Token/s, TTFT 656 ms) |
| Ministral 3 3B Instruct Q4_K_M | Mistral / `mistral3` | Mistral AI (offiziell) | Apache-2.0 | **ausgeführt** (Vulkan; gleicher Ablauf: 28,5 Token/s, TTFT 891 ms) |
| Phi-4-mini Instruct Q4_K_M | Phi / `phi3` | unsloth (Konvertierung von microsoft) | MIT | Metadaten |
| Gemma 4 E2B it Q4_0 | Gemma / `gemma4` | ggml-org (Konvertierung von google) | Apache-2.0 (laut HF-Metadaten) | Metadaten |
| Gemma 3 1B it Q4_K_M | Gemma / `gemma3` | unsloth (Konvertierung von google) | Gemma Terms (open-weights) | Metadaten |
| Qwen3-Embedding 0.6B Q8_0 | Embedding / `qwen3` (Pooling) | Qwen (offiziell) | Apache-2.0 | **ausgeführt** |
| EmbeddingGemma 2 Q8_0 | Embedding / `gemma-embedding2` | ggml-org | Apache-2.0 | Metadaten |
| nomic-embed-text v1.5 Q8_0 | Embedding / `nomic-bert` | Nomic AI (offiziell) | Apache-2.0 | Metadaten |

„Metadaten“ bedeutet: Repository, Revision, Dateigröße und SHA-256 per
Hugging-Face-API geprüft, Lizenz gelesen, Architekturname laut llama.cpp-
Dokumentation bzw. HF-Metadaten in der Liste des gebündelten Builds. Erst
Download + Header-Prüfung + Probe-Antwort bei der Aktivierung bestätigen die
Ausführbarkeit; dann erscheint das Modell als „getestet auf diesem Rechner“.

Nicht aufgenommen: `Qwen/Qwen3.8-Flash-Next` (Lizenz „other“, 160 GB),
`google/gemma-3-1b-it-qat-q4_0-gguf` (manuelle Freischaltung), LFM2/LFM3
(Lizenz „other“), alle Repositories mit `gated` ≠ false.

## Familienregeln (`families.json`)

| Familie | Thinking-Umschalter | Systemrolle | Hinweis |
|---|---|---|---|
| Qwen3, Qwen3.5/3.8, Qwen4 | `enable_thinking` | ja | Thinking standardmäßig aus |
| SmolLM3 | `enable_thinking` | ja | |
| Llama, Mistral, Phi, LFM | keiner | ja | |
| Gemma | keiner | nein | Systemtext wird vorangestellt |

Die Regeln liegen im Adapter, nicht in UI oder Gedächtnislogik. Ein
Umschalter wird nur gesendet, wenn die ausgelieferte Chatvorlage ihn enthält.

## Suche und freie Importe

- `GET /api/models/search` nutzt die Hub-Suche (`filter=gguf`), Treffer sind
  **ungeprüft** und werden nie empfohlen.
- `POST /api/models/resolve` löst eine Repository-ID zu fester Revision und
  Dateiliste auf, liest den GGUF-Header per Range-Request (Architektur,
  Chatvorlage, Pooling, Kontext) und liefert ein Kompatibilitätsurteil. Erst
  dann ist der Eintrag `metadata`-geprüft und speicherbar. Zugangsbeschränkte
  Repositories werden mit `gated` gemeldet; es gibt keinen Umweg.
- Lokale Importe prüfen Header, Architektur, Typ und Vollständigkeit von
  Split-Dateien; fehlende Prüfsummen werden angezeigt (nur Größe geprüft).

## Ausgeführte Familien (Echtläufe, `foldable/build/measurements/families-report.json`)

| Modell | Familie | Vorlage / Denkmodus | Import + Header | Aktivierung (Vulkan) | Erstes Token (1. Antwort / Sofortfrage) | Kalibrierung | Stopp → `done` | Gedächtnis |
|---|---|---|---|---|---|---|---|---|
| Qwen3 0.6B Q8_0 | Qwen3 | Vorlage ja, `enable_thinking` erkannt | Katalog-Download | 2,7–3,1 s | 0,22 s / – | 64 Token/s (CPU 26,5) | 0,03 s | Korrektur sofort sichtbar; nach Neustart „Mara Muster“ |
| Llama 3.2 1B Instruct Q4_K_M | Llama | Vorlage ja, kein Denkmodus (0 `thinking`-Ereignisse) | lokaler Import (Weg C) | 4,6 s | 1,77 s / 0,45 s | 71,2 Token/s, 1046 Prompt-Token/s | 0,52 s¹ | Quellen korrekt (2 rohe Fakten sichtbar); Antwort nannte vor der Korrekturschicht den alten Namen² |
| Ministral 3 3B Instruct Q4_K_M | Mistral | Vorlage ja, kein Denkmodus | lokaler Import (Weg C) | 5,9 s | 1,06 s / 0,59 s | 28,5 Token/s, 283 Prompt-Token/s | 0,55 s¹ | Sofortfrage korrekt („Mara Muster“) |

¹ Der Familienlauf fand vor der Harness-Korrektur statt: ~0,4 s davon sind die
Erzeugung des HTTP-Clients im Testskript (siehe PERFORMANCE_REPORT). Der
Kern-Stoppwert liegt bei 0,03 s (Aktivitätstest nach der Korrektur).
² Der Lauf fand vor der deterministischen Korrekturschicht statt
(`memory/corrections.py`); mit ihr ist der ersetzte Fakt für kein Modell mehr
„aktuell“. Die 1B-Antwort ist eine Modelleigenschaft und wird nur
protokolliert, nicht als Test gefordert.

Alle drei Familien laufen über denselben Adapterpfad (Header → Fähigkeiten →
llama-server mit Jinja-Vorlage des Modells); es gibt keinen familien-
spezifischen Prompt und keine `/think`-Annahme.

## Unbestätigte Angaben

- Kontextlängen und Sprachunterstützung stammen aus Modellkarten.
- Die Architekturzuordnung nicht ausgeführter Einträge ist aus Dokumentation
  abgeleitet, bis der Header beim Download gelesen wurde.
- Vulkan auf anderen GPUs als Intel Arc 140V, Partial-Offload (Teilschichten)
  und Modelle über 8B wurden nicht ausgeführt.
