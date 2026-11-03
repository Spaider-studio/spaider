# NOTES – Annahmen, Abweichungen, bekannte Lücken

Stand: 2026-11-04, Shell 0.3.0. Bezugsdokumente: `docs/portable/API_CONTRACT.md`
(v1 + Addendum v1.1 + Addendum v1.2 inkl. Browser-Paket) und
`docs/portable/ACTIVITY_EVENTS.md`.

## Annahmen und Interpretationen (Addendum v1.2 / Live, Agent, Overlay)

- **Overlay-Hotkey und Vordergrund**: Der Hauptprozess ruft bei jedem Hotkey
  zuerst `POST /api/live/foreground` (Timeout **400 ms**, Fehler → Overlay ohne
  Hint) und zeigt erst danach das Fenster. Ist das Overlay bereits sichtbar,
  blendet derselbe Hotkey es aus (Toggle). Der Vertrag nennt nur „unmittelbar
  beim Hotkey, bevor das Overlay erscheint“.
- **Overlay wird beim Start versteckt angelegt** (Renderer bereits geladen),
  damit der Hotkey nur `show()` auslösen muss. Die gemessene Latenz gilt für
  dieses warme Fenster; ein Kaltstart des Renderers wäre deutlich langsamer.
- **Quellenbestätigung**: Beim ersten Öffnen muss der Picker bestätigt werden
  (Enter/Klick, Esc ist dort gesperrt, bis eine Quelle gewählt ist). Danach ist
  der Vordergrund-Hint vorausgewählt; weicht er von der bestätigten Quelle ab,
  zeigt die Zielzeile „Bestätigen“, und **Enter im Eingabefeld bestätigt den
  Hint und sendet die Frage in einem Schritt** (der Hint ist dabei sichtbar).
  Ein Hint mit `is_self` oder ohne Hint lässt die bestätigte Quelle unverändert.
  Ein Zielwechsel bei bestehender Sitzung geht über `PATCH /live/sessions/{id}`.
- **Live-Sitzung und Gespräch**: Die erste Frage legt die Sitzung mit
  `conversation_id: null` an (der Kern erzeugt „Live: <Titel>“); „Im
  Hauptfenster öffnen“ fokussiert das Hauptfenster und wählt dieses Gespräch
  (`loadConversations` + `selectConversation`). Das Overlay hält die Züge der
  Sitzung nur im Sitzungsspeicher.
- **Vorschau**: Nach jeder Antwort (`done`, auch gestoppt) wird
  `POST …/snapshot {include_image:false, preview:true, max_elements:0}`
  geholt; nur `preview_png_base64` wird angezeigt (CSP `img-src data:`).
  `live_preview:false` unterdrückt den Aufruf vollständig.
- **Qualität**: `observation.quality`, `snapshot.quality` und `event: live`
  (Zustand `lost`) setzen dieselbe Anzeige; `lost`, `minimized`, `black` und
  `error` bieten „Quelle neu wählen“, `occluded` nur den Hinweis. 409
  `source_lost` beim Fragen wird als „Quelle verloren“ gezeigt.
- **`use_image`** ist immer `"auto"`, `use_memory` immer `true` (keine
  Oberfläche dafür im Overlay; 409 `vision_unavailable` wird als Hinweis
  gezeigt, falls der Kern es dennoch sendet).
- **Stopp-Hotkey**: Der Hauptprozess kennt die streamenden Gespräche nur, weil
  beide Renderer sie per `reportActiveConversations` melden (eine Liste je
  Fenster, Vereinigung). Alle Stopp-Aufrufe laufen parallel mit 2 s Timeout; es
  wird auf nichts gewartet. `event: stop` des Kerns trägt `source:"api"` – für
  3 s nach einem lokalen Hotkey-Ereignis behält die Oberfläche „Hotkey“ als
  Quelle.
- **Hotkey-Registrierung**: nach `ready` aus `GET /api/settings`; ungültige
  Werte fallen auf die Vertrags-Standards zurück. Nach `PATCH` der Hotkeys ruft
  der Renderer `registerHotkeys` erneut auf; `setTrayMode` nach `PATCH tray_mode`.
  Zwei identische Kombinationen werden als Fehler gemeldet, die zweite nicht
  registriert. Die Prüfung verlangt mindestens Ctrl/Alt/Super/Cmd (Shift allein
  würde das Tippen in allen Programmen stören) – strenger als Electron selbst.
- **Tray**: Das Tray-Symbol existiert immer (Menü Öffnen, Live-Fenster, Stopp,
  Beenden); `tray_mode` steuert nur das Schließverhalten des Hauptfensters. Kein
  Autostart (`app.getLoginItemSettings().openAtLogin` bleibt `false`).
- **Overlay-Position** wird je Monitor (`display.id`) in
  `data/electron/overlay.json` gemerkt; Standard ist die rechte untere Ecke des
  Arbeitsbereichs (24 px Rand). Der Monitor ergibt sich aus `rect` des Hints,
  sonst aus der Mausposition.
- **Fokus**: `win.show()` gibt dem Overlay den Tastaturfokus (nötig zum
  Tippen); `hide()` lässt Windows den Fokus an das zuvor aktive Fenster
  zurückgeben. `app.hide()` ist macOS-only und wird nicht benutzt. Esc wirkt
  nur im fokussierten Fenster – nach „Als Aufgabe ausführen“ (Hauptfenster wird
  bewusst fokussiert) schließt Esc das Overlay erst, wenn es wieder fokussiert
  ist.
- **Aufgaben**: Die Liste folgt jeder nicht-terminalen Aufgabe über
  `GET /api/agent/tasks/{id}/events` (eine Verbindung je Aufgabe; endet der
  Strom ohne `done`, wird die Aufgabe per GET nachgeladen und bis zu 30-mal
  erneut abonniert). `event: task` des globalen Kanals aktualisiert die Liste
  und startet das Abonnement für neue Aufgaben. Ein älterer Task-Snapshot
  (`updated_at`) überschreibt nie einen neueren.
- **Rückfrage**: `pending_question` am Task hat Vorrang vor dem
  `question`-Ereignis des Stroms. Ablehnen sendet `approve:false`; bei
  `kind:"input"` wird „Abbrechen“ ebenfalls als `approve:false` gesendet.
- **Beleg-Pfade**: Als „Beleg“ gilt ein absoluter Pfad in
  `result.evidence.{path,file,file_path,output_path,screenshot_path}` oder
  `artifact.path`; er wird als kopierbarer Pfad angezeigt (kein `shell.openPath`
  – das würde Dateien ausführen; `showItemInFolder` ist nicht im Vertrag).
- **Scopes** stammen ausschließlich aus dem Formular (`draftToRequest`); der
  Entwurf hält Dateiwurzeln, Schreibordner, Fenster-IDs und Prozesse getrennt.
  „Mit diesem Ablauf starten“ übernimmt `recipe.scope` und `purpose_de` als
  Ziel; „Als Aufgabe ausführen“ aus dem Overlay übernimmt den Fragetext, die
  Quelle (`windows.source_ids`), deren Prozess und die Sitzungs-/Gesprächs-ID.
- **Browser-Paket**: `GET /api/agent/browser` wird beim Öffnen des
  Aufgaben-Formulars und der Einstellungen geladen; fehlt der Endpunkt (älterer
  Kern), bleibt der Browser-Schalter benutzbar. Der Install-Job (Kind
  `browser`) wird mit `lib/jobs.followJob` wie ein Modell-Download verfolgt
  (`GET /api/models/jobs/{id}/events`), nach `done` werden Status und
  `agent/capabilities` neu geladen. `{"job":null,"installed":true}` wird als
  Hinweis gezeigt.
- **Agent-Aktivitätsereignisse** (`event: activity` im Task-Strom) werden
  entgegengenommen, aber nicht dargestellt (der Zeitstrahl zeigt Aktionen).

## Annahmen und Interpretationen (v1 / v1.1, unverändert)

- `onBackendStatus` liefert nach dem Abonnieren sofort den aktuellen Zustand;
  `getBackend()` wird bei fatalem Startfehler abgelehnt.
- Job-Ereignisströme enden nach einem terminalen Zustand (`followJob` holt sonst
  nach); Downloads sind bei `paused` **oder** `failed` fortsetzbar.
- Automatische Aktivierung nur im Setup-Screen; Setup → Chat sobald `ready`.
- Chat-Abbruch wartet auf `done` (Status `stopped`); Gesprächsliste nach
  `updated_at`; `setup/status` alle 15 s bei sichtbarem Fenster.
- Aktivität: `message` vor dem ersten `activity`, fremde `request_id`/alte
  `seq` verworfen; `thinking` = `{"text"}`; `runtime` liest `state`/`runtime_state`;
  `postprocessing_pending` wird lokal verrechnet und nach 800 ms nachgeladen.
- Globaler Kanal: eine Verbindung je Fenster (Haupt- **und** Overlay-Fenster
  halten je eine), Backoff 1 s → 30 s.
- Gated-Modelle: Download-Knopf deaktiviert; `measured` nur wie geliefert.

## Abweichungen von der Aufgabenstellung / vom Vertrag

- **Bridge-Zusätze außerhalb des Vertrags** (alle als „shell-intern“ markiert,
  keine neuen Node/Electron-Fähigkeiten im Renderer): `onOverlayShown`
  (Hint + `showId` an den Overlay-Renderer), `reportOverlayPainted`
  (Paint-Messung), `reportActiveConversations` (für den Stopp-Hotkey),
  `focusMain` („Im Hauptfenster öffnen“), `requestTaskForm` /
  `onOpenTaskForm` („Als Aufgabe ausführen“), `onOpenConversation`. Die im
  Vertrag genannten Methoden (`onHotkey`, `overlay.*`, `getOverlayState`,
  `pickFolder`, `pickFiles`, `registerHotkeys`, `setTrayMode`) sind unverändert;
  `getPerfSamples()` kommt aus der Aufgabenstellung.
- **Standard-Hotkey belegt**: Auf dem Entwicklungsrechner konnte
  `Control+Alt+Space` nicht registriert werden (`globalShortcut.register` →
  false, eine andere Anwendung hält die Kombination). Der Standard bleibt wie im
  Vertrag; die Shell meldet den Konflikt (Log, Einstellungen → „Prüfen“) und
  läuft mit dem Stopp-Hotkey weiter. Die Messungen unten nutzen deshalb
  `Ctrl+Alt+F9` (Mock-Option `--hotkey-overlay`).
- **Seitenleiste**: „Aufgaben“ ist ein Bereichswechsel in der Seitenleiste
  (Gespräche | Aufgaben); die Aufgabenliste steht dort, Formular/Detail/Abläufe
  im Hauptbereich. Die Seitenleiste ist dafür 280 px breit (vorher 260).
- **Esc im Hauptfenster** stoppt weiterhin nur die laufende Antwort; der
  globale Stopp ist der Hotkey bzw. das Tray.
- **`getPerfSamples()`** hält 20 Einträge insgesamt (vier je Hotkey:
  `overlay_foreground_ms`, `overlay_hotkey_to_show_ms`,
  `overlay_show_to_painted_ms`, `overlay_hotkey_to_painted_ms`), also die
  letzten fünf Hotkeys; das `launcher.log` enthält alle.
- **Mock**: `startGeneration` bekommt einen `live`-Parameter (Phasen
  `capture`/`observe`/`image_encode` + `observation` vor dem regulären
  Verlauf); `image_encode` erscheint nur mit Bildmodell (`vision_available`,
  im Mock `false`). Der Mock-Browser startet **nicht installiert**
  (`--browser-installed` zum Überspringen); `agent/capabilities.browser`
  folgt dem Install-Zustand. `event: stop` des Mocks hat immer `source:"api"`.
- TypeScript 5.9.3 statt 7.x, Umgebungs-Overrides auch paketiert, Attach-Modus
  und Shutdown, CSP im Dev-Modus, Modellwege als Reiter: wie in 0.2.0.

## Bekannte Lücken

- **Kein echter Kern getestet**: Alle Abläufe laufen gegen `mock/server.mjs`
  (v1 + v1.1 + v1.2). Echte Werte für `POST /live/foreground` (Vertrag: ≤ 50 ms),
  Snapshot-Qualitäten, UIA-Elemente, Agent-Zustände und die Browser-Installation
  müssen beim ersten Integrationslauf nachgezogen werden.
- **Tray-Menü nicht als Screenshot**: Das native Kontextmenü lässt sich nicht
  über `capturePage` aufnehmen; verifiziert sind Erzeugung („tray created“),
  Verstecken statt Beenden bei `tray_mode` und das Fehlen eines Autostarts.
- **Native Dialoge** (`pickFolder`, `pickFiles`) wurden nicht automatisiert
  (modal); die Knöpfe und die IPC-Handler sind vorhanden, das Formular wurde im
  Harness ohne Ordnerwahl abgeschickt.
- **Overlay-Screenshots mit Content-Protection**: `webContents.capturePage()`
  funktioniert trotz `setContentProtection(true)` (es rendert aus dem
  Compositor); ob Aufnahmeprogramme das Fenster wirklich auslassen, wurde nicht
  mit einem externen Capture-Tool geprüft.
- **Overlay-Sitzung bleibt bestehen**, bis das Overlay eine neue Quelle
  bestätigt oder SpAIder beendet wird; `DELETE /live/sessions/{id}` wird nur bei
  einem Fehler nicht aufgerufen – das Beenden der Sitzung beim Schließen des
  Overlays ist nicht implementiert (die Hooks des Kerns bleiben bis zum Ende
  aktiv).
- **Aktivität historischer Nachrichten**, Denktext nach Neustart, `excluded`
  nur als Textliste, Python-Spawn im Dev-Modus ungetestet, keine Dokument-
  Textansicht: wie in 0.2.0.
- Vitest-Laufzeit ~35 s (jsdom-Start je Datei); Lint ohne typgestützte Regeln.

## Verifikation (2026-11-04, Shell 0.3.0, alles gegen den Mock)

- `npm run typecheck` – beide Projekte ohne Fehler.
- `npm run lint` – keine Meldungen.
- `npm test` – 12 Dateien, 84 Tests bestanden (neu: `tasks` 11 Tests,
  `accelerator` 5 Tests, `overlay` 8 Tests).
- `npm run build` – `dist/renderer/assets/index-*.js` 837 kB (gzip 254 kB).
- `npm run package` – `out/SpAIder-win32-x64/SpAIder.exe`; `VersionInfo`:
  ProductName „SpAIder Foldable“, FileVersion/ProductVersion **0.3.0**,
  `resources/app.asar` 1,31 MB.
- **Paketierte EXE gegen den Mock** (Attach-Modus, `--state ready`,
  `SPAIDER_FOLDABLE_SCREENSHOT`): „tray created“, attach in 40 ms, Hotkeys
  registriert (Overlay-Hotkey ehrlich als belegt gemeldet), Screenshot, Shutdown
  an den Mock, Exit-Code 0.
- **UI-Harness** (`scratchpad/ui-v12.cjs`: echter Hauptprozess `dist/main` im
  Attach-Modus gegen den Mock, beide Fenster per `executeJavaScript`, Hotkeys
  per **echter Tastatureingabe** `keybd_event` über `sendkeys.ps1`): erster
  Hotkey öffnet den Picker mit hervorgehobenem Vordergrund („Protokoll.txt –
  Editor“), Enter bestätigt; später kein Picker, Hint identisch; Live-Frage →
  Aktivität „Struktur wird gelesen“, Beobachtung „ohne Bild · 3 Elemente · 146
  Zeichen“, Vorschau 96×61 PNG, Klartext während des Streams, Markdown danach;
  Stopp → „Wird gestoppt …“ → „Abgebrochen · 2,1 s · 63 Token/s“; Picker ohne
  `is_self`; Wechsel auf minimiertes Fenster → „Fenster ist minimiert“ +
  „Quelle neu wählen“; Frage „verloren“ → 409 `source_lost` angezeigt; „Als
  Aufgabe ausführen“ → Formular im Hauptfenster mit Ziel, Fenster-Scope und
  Prozess; Browser-Schalter deaktiviert mit Hinweis, solange das Paket fehlt;
  Esc (Overlay fokussiert) blendet aus; Aufgabe mit Rückfrage: Zeitstrahl
  (`memory.search` fertig, `windows.read` läuft), Badge „Führt aus“, Schritt
  1/20, Rückfrage-Dialog + Badge „1“ in der Seitenleiste, Freigeben →
  „Abgeschlossen“ 4/20 mit geprüftem Ergebnis und Artefakt; „Als Ablauf
  speichern“ → Abläufe-Liste (Seed + neu), „Mit diesem Ablauf starten“ → Formular
  mit Ablauf-Chip, Pause → „Pausiert“, Weiter → „Beobachtet“; **Stopp-Hotkey
  (echt)** während einer Chat-Antwort: „Wird gestoppt …“ lokal, Antwort
  abgebrochen, laufende Aufgabe `cancelled`, Log „agent/stop → HTTP 200“,
  „conversation stop → HTTP 200“, Anzeige „Globaler Stopp (Hotkey)“;
  Einstellungen → Live & Agent: „Prüfen“ → beide registriert, `Ctrl+Alt+Foo` →
  „Unbekannte Taste“, Speichern → `Control+Alt+F10` im Mock und neu registriert,
  Tray-Modus → `tray_mode:true` und Schließen versteckt das Fenster (nicht
  zerstört, App läuft), Browser: „Nicht installiert – …“ → „Browser laden
  (≈ 207 MB)“ → Job `Läuft · 25,9 MB von 207,0 MB · 12 % · 34,5 MB/s` →
  „Installiert · Version 131.0.6778.33 · Playwright-fähig“, Schalter im
  Formular wieder aktiv. Keine Konsolenfehler in beiden Fenstern; `launcher.log`
  enthält keinen Fragetext.
- **Gemessene Latenzen (echtes Electron, echte Tastatureingabe, Kern = Mock,
  Overlay-Fenster vorab versteckt angelegt)**, letzter Lauf, 12 Hotkeys
  (vier weitere Läufe am selben Tag lagen im selben Bereich):
  - Hotkey → `show`-Ereignis des Overlay-Fensters (`overlay_hotkey_to_show_ms`):
    Median **12,3 ms**, min 10,5, max 24,0 (frühere Läufe: Median 10,7 / 13,3 / 13,4).
  - Hotkey → erster Paint des Overlay-Renderers (2× rAF,
    `overlay_hotkey_to_painted_ms`): Median **16,7 ms**, min 13,6, max 54,4
    (frühere Läufe: Median 16,6 / 15,9 / 17,1; einzelne Ausreißer 44–54 ms).
  - `show` → Paint: Median 14,0 ms. `POST /api/live/foreground` (Mock, enthalten
    in den obigen Werten): Median 1,6 ms, max 2,0 ms – beim echten Kern kommt
    dessen Antwortzeit (Ziel ≤ 50 ms, Timeout 400 ms) hinzu.
  - Tastendruck → übernommener DOM-Zustand des Overlay-Eingabefelds
    (`overlay_input_to_dom_ms`, 49 echte Tastenereignisse): Median 1,2 ms,
    max 2,5 ms.
  - Klick auf Stopp → Statuszeile „Wird gestoppt …“ (`overlay_stop_to_status_ms`,
    1 Messung je Lauf): 1,0 ms (frühere Läufe 0,7–1,0 ms).
  - Frame-Lücken im Overlay-Stream (2,1 s, 125 Frames): max 17,3 ms, Mittel
    16,7 ms, 0 Lücken > 50 ms (längerer Stream 5,1 s/302 Frames: max 33,4 ms).
- **Stolperstein in Agent-/VS-Code-Terminals:** Ist `ELECTRON_RUN_AS_NODE=1`
  gesetzt, startet jede Electron-Binärdatei als reines Node. Ports 8769 u. ä.
  können auf Windows reserviert sein (`listen EACCES`, PID 4) – für den Mock
  einen anderen Port wählen.
