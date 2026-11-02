# NOTES – Annahmen, Abweichungen, bekannte Lücken

Stand: 2026-11-02, Shell 0.2.0. Bezugsdokumente: `docs/portable/API_CONTRACT.md`
(v1 + Addendum v1.1) und `docs/portable/ACTIVITY_EVENTS.md`.

## Annahmen und Interpretationen des Vertrags (v1)

- **`onBackendStatus`** liefert nach dem Abonnieren sofort den aktuellen Zustand
  (per `invoke` nachgeladen), damit späte Abonnenten den Übergang
  `starting → ready` nicht verpassen. Der Vertrag beschreibt nur Ereignisse.
- **`getBackend()`** wird bei einem fatalen Startfehler des Kerns **abgelehnt**
  (statt ewig offen zu bleiben); die Oberfläche zeigt den Fehler über
  `onBackendStatus` (Zustand `error`) und bietet „Beenden“ an.
- **Zusätzliche stdout-Zeile** `{"event":"status","message":"…"}` des Kerns
  wird – falls vorhanden – als Startmeldung im Boot-Screen angezeigt; alle
  anderen stdout-Zeilen landen in `core-stderr.log` mit Präfix `[stdout]`.
- **Job-Ereignisse**: Es wird davon ausgegangen, dass der Stream
  `/api/models/jobs/{id}/events` nach einem terminalen Zustand endet. Endet er
  früher, holt `followJob` den Job per `GET` nach und abonniert erneut (bis 50
  Versuche, 1 s Pause).
- **Fortsetzen von Downloads**: `POST /jobs/{id}/resume` wird für Jobs mit
  `state` `paused` **oder** `failed` angeboten (nur `kind: download`).
- **Automatische Aktivierung** nach einem Download/Import erfolgt nur im
  Setup-Screen (kein aktives Modell). Unter Einstellungen → Modelle ist die
  Aktivierung ein bewusster Klick, damit ein laufendes Modell nicht ungefragt
  ersetzt wird.
- **Setup → Chat**: Sobald `GET /api/setup/status` `ready` meldet (nach der
  Aktivierung des Chat-Modells), wechselt die Oberfläche direkt in den Chat.
- **Dokument-Warnungen**: `message_de` aus der Antwort hat Vorrang; fehlt es,
  gilt die lokale Zuordnung (`no_text_layer` → „Kein Textinhalt erkannt
  (gescannte PDF?)“).
- **Chat-Abbruch**: „Stopp“ ruft nur `POST /conversations/{id}/stop` und wartet
  auf das `done`-Ereignis (Status `stopped`); der Fetch wird nicht lokal
  abgebrochen, damit die Teilantwort und das finale Objekt ankommen. Endet der
  Stream ohne `done`, wird die Nachricht lokal als `stopped` bzw. `error`
  markiert.
- **Antworten kopieren** kopiert den rohen Markdown-Text der Nachricht.
- **Gesprächsliste** sortiert nach `updated_at` absteigend (Fallback
  `created_at`), unabhängig von der Reihenfolge des Kerns.
- **`setup/status`** wird alle 15 s neu geladen (nur bei sichtbarem Fenster).
- **Rollback-Hinweis**: Bei fehlgeschlagener Aktivierung zeigt die Job-Anzeige
  immer „Vorheriges Modell bleibt aktiv.“ – entsprechend der Vertragszusage.

## Annahmen und Interpretationen (Addendum v1.1 / Aktivität)

- **Reihenfolge im Chat-Stream**: `event: message` kommt vor dem ersten
  `activity`-Ereignis (der Vertrag setzt `request_id = assistant_message.id`).
  Aktivitätsereignisse, die vor `message` eintreffen, werden verworfen, weil
  die aktuelle Anfrage-ID noch unbekannt ist; ebenso jedes Ereignis mit
  fremder `request_id` oder nicht steigender `seq`.
- **`thinking`-Ereignis**: Nutzlast `{"text":"…"}` wie bei `token` angenommen
  (der Vertrag nennt nur den Ereignisnamen). Denktext wird nur im
  Sitzungsspeicher gehalten (die `Message` hat kein Feld dafür) und nur
  angezeigt, wenn das aktive Modell `capabilities.reasoning_toggle` hat.
- **`event: runtime`** auf dem globalen Kanal: Der Vertrag nennt nur
  „Modellzustände“. Die Shell liest `state` (bzw. `runtime_state`), übernimmt
  einen gültigen `RuntimeState` sofort und lädt danach `setup/status` nach;
  unbekannte Felder werden ignoriert.
- **`postprocessing_pending`-Badge**: `postprocess_pending` (+1) und
  `postprocess_done`/fehlgeschlagen (−1) werden lokal verrechnet; 800 ms später
  wird `GET /api/conversations` nachgeladen, damit der Kern die Wahrheit setzt.
- **Zusammenfassungszeile**: Dauer = `t_ms` des `answer_done`-Ereignisses
  (Kernzeit), Token/s aus dessen `counters.tokens_per_second`, ersatzweise aus
  dem letzten `generating`-Ereignis mit Zählern; fehlt beides, fehlt der Teil.
  Die laufende Zeit während des Streams ist die lokale Zeit seit dem ersten
  Ereignis (250-ms-Takt), nicht `t_ms`.
- **Zustandsabgrenzung**: `saved` → „Gespräch gespeichert“;
  `postprocess_pending/running/done` → „… ausstehend“/„Gedächtnis wird
  aktualisiert“/„Gedächtnis aktualisiert“; ein `failed` innerhalb der
  Nachverarbeitung bleibt vom Ergebnis der Antwort getrennt („…fehlgeschlagen“
  in Gelb, Antwort bleibt „fertig“).
- **Globaler Kanal**: eine Verbindung pro Sitzung, Start sobald der Client
  existiert (auch im Setup-Screen), Reconnect-Backoff 1 s → 30 s; eine
  Verbindung, die ≥ 10 s stand, setzt den Backoff zurück. `: keep-alive` des
  Kerns wird vom SSE-Parser als Kommentar verworfen.
- **Status-Zeile unter dem Composer** zeigt die v1-`status`-Ereignisse nur
  noch, wenn für die laufende Antwort kein Aktivitäts-Track existiert (v1-Kern).
- **Dynamische Download-IDs**: Der Job-Schlüssel ist `job.catalog_id`; liefert
  der Kern dafür `null`, nutzt die Shell die `entry.id` (`hf:…`).
- **Gated-Modelle**: Der Download-Knopf ist bei `license.gated` oder
  `requires_account` deaktiviert (Hinweis statt Umgehung); zusätzlich wird ein
  409 `gated` des Kerns als „Modell erfordert ein Konto“ erklärt.
- **`measured` in Empfehlungen**: Es wird exakt angezeigt, was der Kern liefert
  („gemessen auf diesem Rechner: …“), sonst „noch nicht gemessen“. Die Shell
  rechnet nie aus `evidence` oder Katalogwerten eine Messung hoch.
- **v1-Kern-Kompatibilität**: Fehlt `GET /api/system/hardware`, bleibt die
  Hardwarezeile leer und das Feld „Grafikkarte“ unsichtbar; fehlen
  `verification`/`capabilities`, fällt die Anzeige auf `support` zurück;
  `catalog_date` wird nur angezeigt, wenn vorhanden.
- **Leistung**: Token-/Denktext-Batching 40 ms (`lib/batch.ts`),
  Plain-Text-Renderer während des Streams, memoisierte Blasen mit
  `content-visibility: auto` (`contain-intrinsic-size: auto 96px`).
  `window.__spaiderPerf` existiert nur unter `vite dev` oder mit `?perf=1`.

## Abweichungen von der Aufgabenstellung / vom Vertrag

- **TypeScript 5.9.3 statt 7.x.** Das npm-Paket `typescript@7` enthält nur die
  native Go-Binärdatei; `typescript-eslint` 8.72 verlangt `typescript <6.1`.
  Daher bleibt `typescript` auf 5.9.3 gepinnt.
- **Umgebungs-Overrides auch paketiert**: `SPAIDER_FOLDABLE_ROOT` und
  `SPAIDER_FOLDABLE_CORE_URL`/`_TOKEN` werden auch in der paketierten EXE
  beachtet, weil die Verifikation die EXE genau so mit dem Mock testet.
  Endnutzer setzen diese Variablen nie.
- **Attach-Modus und Shutdown**: Beim Beenden wird dem externen Kern
  `POST /api/system/shutdown` geschickt (3 s Timeout), ohne auf einen
  Prozess-Exit zu warten.
- **CSP im Dev-Modus**: Nur unter `vite dev` wird die Meta-CSP um
  `script-src 'unsafe-inline'` und `ws://localhost:5173` erweitert.
- **Drei Modellwege als Reiter**: Setup-Screen und Einstellungen → Modelle
  zeigen „Für diesen Computer empfohlen“ / „Weitere Modelle“ / „Lokales Modell
  importieren“ als Reiter (eine gemeinsame Komponente `ModelPaths`); der
  eingebaute Katalog (v1-Karten mit Download/Fortsetzen) liegt unter „Weitere
  Modelle“ unterhalb von Suche und Repository-Prüfung.
- **Zweiter Stopp-Knopf** in der Aktivitätszeile ruft dieselbe Aktion wie der
  Knopf im Composer (Esc bleibt erhalten).
- **`GET /api/models/catalog`** wird über `catalogResponse()` gelesen; das
  alte `catalog()` (nur `entries`) bleibt für Tests/Kompatibilität bestehen.
- **Hervorhebungs-Theme, Schriften, geteilte Komponenten**: wie bisher
  (Token-Farben in `styles.css`, `@fontsource-variable/*` lokal, `Toast`/
  `ConfirmDialog`/`LoadingSpinner`/`cn()` aus `frontend/src`).

## Bekannte Lücken

- **Kein echter Kern getestet**: Alle Abläufe wurden gegen `mock/server.mjs`
  geprüft (v1 + v1.1). Abweichungen des echten Kerns (Ereignis-Reihenfolge,
  Nutzlast von `thinking`/`runtime`, Fehlercodes) müssen beim ersten
  Integrationslauf nachgezogen werden.
- **Aktivität historischer Nachrichten**: `GET /conversations/{id}/messages/{mid}/activity`
  ist im Client vorhanden (`messageActivity`), wird aber nicht genutzt; nach
  einem Neustart oder Gesprächswechsel zeigen alte Antworten nur `usage` und
  `timings`, keine „Details“.
- **Denktext** geht beim Wechsel des Gesprächs nicht verloren (Sitzungsspeicher),
  aber beim Neustart der App; es gibt keinen Endpunkt, der ihn liefert.
- **`excluded`** der Empfehlungen wird nur als aufklappbare Textliste gezeigt.
- **Python-Spawn im Dev-Modus** (`python -m spaider_foldable` aus `../core`)
  ist implementiert, aber ungetestet.
- **Dokumente** haben keine eigene Textansicht; `GET /api/memory/facts` und
  `GET /api/system/logs` werden nicht angezeigt.
- **`system_prompt_extra`, `max_answer_tokens`, `threads`** sind bewusst nicht
  in der Oberfläche; Threads/Batch werden laut Addendum über den
  Leistungsmodus gesteuert.
- **Vitest-Laufzeit**: ~60 s, fast ausschließlich jsdom-Start (9 Dateien);
  `pool: 'vmThreads'` wurde nicht aktiviert, um das Setup unverändert zu
  lassen.
- Lint läuft mit typescript-eslint ohne typgestützte Regeln.

## Verifikation (2026-11-02, Shell 0.2.0)

- `npm run typecheck` – beide Projekte ohne Fehler.
- `npm run lint` – keine Meldungen.
- `npm test` – 9 Dateien, 59 Tests bestanden (neu: `activity`, `batch` inkl.
  Reconnect-Backoff, `recommendations` inkl. `ResolveResult`, `modelErrors`).
- `npm run build` – `dist/renderer/assets/index-*.js` 756 kB (gzip 232 kB).
- `npm run package` – `out/SpAIder-win32-x64/SpAIder.exe` (≈ 250 MB
  Electron-Stub), `resources/app.asar` 1,19 MB; `VersionInfo`: ProductName
  „SpAIder Foldable“, FileVersion 0.2.0.
- **Paketierte EXE gegen den Mock** (Attach-Modus, `SPAIDER_FOLDABLE_SCREENSHOT`,
  wartet auf `ready`): mit `--state needs_model` Setup-Screen mit Hardwarezeile
  und Empfehlungen; mit `--state ready` Chat. Shutdown-Request erreicht den
  Mock, Exit-Code 0.
- **UI-Harness** (Electron-Skript außerhalb des Repos gegen `dist/` + Mock
  `--state ready`, `?perf=1`): Aktivitätszeile während des Streams
  („Antwort wird geschrieben · 23 Token/s“, laufende Zeit), Plain-Text während
  des Streams und Markdown danach, eingeklappte Zeile „Antwort fertig · 4,7 s ·
  21 Token/s · Gedächtnis-Nachverarbeitung ausstehend“ mit 28 Details-Zeilen,
  globaler Kanal (Sidebar „Gedächtnis wird im Hintergrund aktualisiert · 1
  ausstehend“, Badge 1 am Gespräch, später „Gedächtnis aktualisiert“),
  `timings`-Zeile, Leistungsmodus „Leise“ + Grafikkarten-Feld, Denktext-
  Abschnitt, Stopp („Wird gestoppt …“ → „Abgebrochen · 3,1 s · 24 Token/s“),
  Empfehlungen (3 Karten, 1 gemessen, Schwerpunktwechsel), „Kurz messen“ →
  „gemessen auf diesem Rechner: 24 Token/s“, Suche (4 Treffer, alle
  „ungeprüft“), Repository-Prüfung (kompatibel, 1 Datei), 409 gated, Split
  (2 Dateien, eine ohne Prüfsumme) + Download über `hf:`-ID, Offline-Hinweis,
  Katalog-Refresh-Toast, Import mit `split_incomplete`. Keine Konsolenfehler.
- **Leistungsmessung** (`window.__spaiderPerf.last`, Mock-Stream mit 12 ms
  Token-Takt, währenddessen Tippen im Composer): 8,7 s, 524 Frames,
  `maxGapMs` 17,4, `meanGapMs` 16,7, 0 Lücken > 50 ms; kurzer Stream: 4,8 s,
  289 Frames, `maxGapMs` 17,2.
- **Stolperstein in Agent-/VS-Code-Terminals:** Ist `ELECTRON_RUN_AS_NODE=1`
  gesetzt, startet jede Electron-Binärdatei (auch `SpAIder.exe`) als reines
  Node und beendet sich sofort ohne Fenster. `scripts/dev-electron.mjs`
  entfernt die Variable; beim manuellen Start vorher `unset ELECTRON_RUN_AS_NODE`.
