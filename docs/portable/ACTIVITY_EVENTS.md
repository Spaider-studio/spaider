# SpAIder Foldable: Aktivitätsereignisse (Vertrag v1)

Jede Chat-Anfrage erzeugt eine geordnete Folge typisierter Ereignisse. Sie
beschreiben ausschließlich tatsächlich ausgeführte Schritte des Kerns; Zahlen,
Quellen und Zeiten stammen aus echten Messpunkten. Die Oberfläche zeigt sie als
Aktivitätszeile unter der entstehenden Antwort und erzeugt daraus keine
zusätzlichen Modellaufrufe.

## Ereignis

```json
{"request_id":"<uuid der Anfrage>","seq":12,"phase":"generating","state":"progress",
 "t_ms":1834,"at":"2026-11-02T10:12:33.120Z","message_de":"Antwort wird geschrieben",
 "counters":{"tokens":12,"tokens_per_second":24.0},"source_ids":[]}
```

| Feld | Bedeutung |
|---|---|
| `request_id` | eindeutig pro Anfrage (= `assistant_message.id`); Ereignisse einer gestoppten Anfrage werden von der UI anhand dieser ID verworfen |
| `seq` | monoton steigende Sequenznummer pro Anfrage; die UI ignoriert Ereignisse mit kleinerer `seq` als die zuletzt gesehene |
| `phase` | siehe Tabelle unten |
| `state` | `started`, `progress`, `done`, `failed`, `cancelled` |
| `t_ms` | Millisekunden seit Anfragebeginn (Zeit des Kerns) |
| `at` | Zeitstempel UTC |
| `message_de` | kurzer deutscher Text für die Aktivitätszeile |
| `counters` | nur gemessene Werte, z. B. `sources`, `facts_pending`, `prompt_total`, `prompt_processed`, `prompt_cached`, `tokens`, `tokens_per_second` |
| `source_ids` | IDs der verwendeten Quellen (nur in `memory_search done`) |

## Phasen (in Reihenfolge; nicht jede Phase tritt immer auf)

| Phase | Wann | Anzeige (Beispiel) | Prozent |
|---|---|---|---|
| `queued` | Anfrage wartet auf freien Modell-Slot (Hintergrundaufgabe wird unterbrochen) | „Wartet auf das Modell“ | nein |
| `model_loading` | Modell wird gerade geladen (nur beim ersten Aufruf nach Start/Wechsel) | „Modell wird geladen“ | nein |
| `memory_search` | Gedächtnissuche (FTS + Vektoren + Graph) | „Gespeicherte Informationen werden gesucht“ → done: „3 passende Quellen gefunden“ | nein |
| `memory_reconcile` | nur wenn unverarbeitete Angaben dieses Gesprächs vor der Antwort berücksichtigt werden müssen | „Neue Angaben werden abgeglichen“ | nein |
| `prompt_build` | Kontext und Verlauf werden auf das Tokenbudget gekürzt | „Kontext wird vorbereitet“ | nein |
| `prompt_processing` | llama-server verarbeitet die Eingabe (`prompt_progress` des Servers, Bezugsgröße bekannt) | „Kontext wird verarbeitet (62 %)“ | ja, nur diese Teilphase |
| `generating` | Tokens treffen ein | „Antwort wird geschrieben · 24 Token/s“ | nein |
| `answer_done` | Antwort vollständig oder gestoppt (`state`: `done`/`cancelled`) | „Antwort fertig“ / „Abgebrochen“ | |
| `saved` | Nachricht persistiert | „Gespräch gespeichert“ | |
| `postprocess_pending` | Gedächtnis-Nachverarbeitung (Extraktion, Ersetzung, Vektoren) steht aus | „Gedächtnis-Nachverarbeitung ausstehend“ | |
| `postprocess_running` / `postprocess_done` | läuft / fertig (kommen über den globalen Kanal, auch nach Ende des Chat-Streams) | „Gedächtnis wird aktualisiert“ / „Gedächtnis aktualisiert“ | |
| `failed` | Fehler mit verständlicher Ursache (`message_de`) | „Fehlgeschlagen: …“ | |

Unterschiedliche Zustände, die die UI nicht vermischt: *Gespräch gespeichert*
(`saved`), *Gedächtnis aktualisiert* (`postprocess_done`), *Nachverarbeitung
ausstehend* (`postprocess_pending`).

## Kanäle

1. **Chat-Stream** (`POST /api/conversations/{id}/messages`, SSE): zusätzlich zu
   `message`, `token`, `thinking`, `sources`, `done`, `error` gibt es
   `event: activity` mit dem Ereignis-JSON. Antworttext (`token`), Denktext
   (`thinking`), Status (`activity`) und Fehler (`error`) bleiben getrennt.
2. **Globaler Kanal** (`GET /api/activity`, SSE, lange Verbindung): Ereignisse
   der Nachverarbeitung (`postprocess_*` mit `conversation_id`), Planerzustand
   (`event: scheduler` → `{"chat_active":bool,"background":{"kind":"message"|"chunk"|"reindex"|null,"pending":n},"paused":bool}`)
   und Modellzustände (`event: runtime`). Die UI verbindet sich einmal und
   verbindet nach Abbruch neu; der Kern sendet alle 15 s `: keep-alive`.

## Regeln

- Keine erfundenen Tätigkeiten, keine zeitgesteuerten Wechsel, keine künstlichen
  Wartezeiten. Fehlt ein Messpunkt, fehlt die Phase.
- Prozentwerte nur für `prompt_processing` (Server meldet `total`/`processed`)
  und Downloads. Ein Prozentwert ist nie der Gesamtfortschritt der Antwort.
- Ereignisse werden gebündelt gesendet: `generating`-Fortschritt höchstens alle
  250 ms, Textstücke sammelt die UI alle 30–50 ms vor dem Rendern.
- Der Kern behält pro Anfrage höchstens die letzten 200 Ereignisse im Speicher
  (`GET /api/conversations/{id}/messages/{mid}/activity` liefert sie für
  „Details“); auf den Datenträger werden nur Zusammenfassungen geschrieben
  (`usage`, Dauer je Phase in `Message.timings`).
- Stop: `POST /conversations/{id}/stop` → die UI zeigt sofort „Wird
  gestoppt“ (lokal), der Kern sendet `answer_done` mit `state: cancelled`,
  sobald der Server die Generierung beendet hat (gemessen, nicht angenommen).
- Nach Neustart gibt es keine laufenden Anfragen; offene Nachverarbeitung wird
  aus der persistierten Warteschlange fortgesetzt und über den globalen Kanal
  gemeldet.
- Später können Agentenaktionen (`tool_call` mit `ToolCall`-Vertrag) dieselbe
  Struktur verwenden; V1 sendet keine solchen Ereignisse.
