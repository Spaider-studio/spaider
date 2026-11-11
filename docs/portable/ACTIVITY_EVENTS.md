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

Zusätzliche Phasen seit 0.3 (Live-Fragen, `request_id` = Assistenten-Nachricht):

| Phase | Wann | Anzeige (Beispiel) | Prozent |
|---|---|---|---|
| `capture` | Zielfenster wird neu validiert und aufgenommen (vor der Gedächtnissuche) | „Quelle wird aufgenommen“ → done: „Aufnahme fertig (ok)“ mit `capture_ms`, `quality` | nein |
| `observe` | UI-Automation-Struktur wird gelesen (begrenzt) | „Struktur wird gelesen“ → „16 Elemente, 242 Zeichen“ (`elements`, `text_chars`, `uia_ms`) | nein |
| `image_encode` | Bild wird für das Bildmodell vorbereitet (nur mit Projektor) | „Bild wird vorbereitet“ → „Bild 626×413“ (`image_w`, `image_h`, `image_bytes`) | nein (Encoding im Server zählt zu `prompt_processing`) |

Phasen einer Agentenaufgabe (`request_id` = Aufgaben-Id, über `GET /api/agent/tasks/{id}/events`):

| Phase | Wann | Anzeige (Beispiel) |
|---|---|---|
| `observe` | frische, begrenzte Beobachtung der Quellen im Umfang je Schritt | „Schritt 3: Beobachtung“ → „Beobachtung fertig“ (`sources`) |
| `planning` | ein Modellaufruf mit Grammatik-JSON | „Schritt 3: Planung“ → „Entscheidung erhalten“ (`llm_calls`) |
| `executing` | Werkzeugaufruf nach Validierung/Freigabe | „files.write“ → „232 Bytes geschrieben und zurückgelesen“ (`ok`, `duration_ms`) |

Dazu `event: task` (Zustand), `event: action` (Vorschlag → validiert → läuft → done/failed/rejected/unknown_outcome),
`event: question` (Rückfrage/Freigabe), `event: token` (Erklärtext, kein Beleg), `event: done`.

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
