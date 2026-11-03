# SpAIder Foldable – Desktop-Shell (Electron + React)

Portable, vollständig lokale Chat-Oberfläche für den SpAIder-Foldable-Kern
(`foldable/core`). Dieses Paket enthält den Electron-Hauptprozess (Start/Stopp
des Kerns, Portabilität, Sicherheit, globale Hotkeys, Live-Overlay, Tray), die
Preload-Bridge `window.spaider` und die React-Oberfläche (deutsch, dunkles
SpAIder-Design).

Der HTTP-Vertrag zum Kern steht in `docs/portable/API_CONTRACT.md` (v1,
Addendum v1.1 und Addendum v1.2); die Aktivitätsereignisse in
`docs/portable/ACTIVITY_EVENTS.md`. Version 0.3.0 der Shell setzt das Addendum
v1.2 um: Live-Overlay per Hotkey, globale Hotkeys, Tray-Modus und das
Aufgaben-Panel (Agent).

## Voraussetzungen

- Node 24 und npm 11 (keine nativen Module, kein Compiler nötig; Electron und
  TypeScript laden fertige Binärdateien).
- Im Bash-Tool: `export PATH="/c/Users/mucbay/AppData/Roaming/fnm/node-versions/v24.21.0/installation:$PATH"`
  und `unset ELECTRON_RUN_AS_NODE` (sonst startet Electron als reines Node).
- `npm install` (lädt Electron 45 von GitHub in den npm-Cache).

## Skripte

| Skript | Zweck |
| --- | --- |
| `npm run dev` | Vite-Devserver + Electron (`--app-root=dev-data`). Erwartet einen laufenden Kern im **Attach-Modus** (siehe unten) – sonst versucht Electron, `python -m spaider_foldable` aus `../core` zu starten. |
| `npm run dev:mock` | Startet den Mock-Kern (`mock/server.mjs`, Port 8765, Token `mock-token`), Vite und Electron im Attach-Modus. Damit lässt sich jede Oberfläche ohne Python/Modelle durchspielen – inklusive Overlay und Aufgaben. |
| `npm run mock` | Nur den Mock-Kern starten (z. B. für die paketierte EXE). Optionen: `--state ready` (Modell bereits aktiv), `--offline`, `--no-gpu`, `--fat32`, `--exit-on-shutdown`, `--hotkey-overlay "Ctrl+Alt+F9"` / `--hotkey-stop …` (Voreinstellung der Hotkeys, falls die Standardkombination auf dem Testrechner belegt ist). |
| `npm run build` | `tsc` für `src/main` + `src/preload` nach `dist/main`, `dist/preload` (CommonJS) und `vite build` nach `dist/renderer` (relative Pfade, läuft über `file://`). |
| `npm start` | Electron mit dem gebauten Renderer (ohne Vite), `--app-root=dev-data`. |
| `npm run typecheck` | Typprüfung beider Projekte (`tsconfig.main.json`, `tsconfig.renderer.json`). |
| `npm test` | Vitest: SSE-Parser, API-Client-Fehler, Markdown-Renderer, Byteformatierung, Store-Reducer, Aktivitäts-Reducer, Token-Batching + Reconnect-Backoff, Empfehlungskarten/Auflösungsergebnis, Fehlerzuordnung, **Aufgaben-Reducer, Accelerator-Prüfung, Overlay-Quellenzustand** (v1.2). |
| `npm run lint` | ESLint (Flat Config, typescript-eslint, react-hooks). |
| `npm run icon` | Erzeugt `assets/icon.png` und `assets/icon.ico` rein in JS (kein Download, keine nativen Module). |
| `npm run package` | Build + `@electron/packager` → `out/SpAIder-win32-x64/SpAIder.exe` (asar, `node_modules` ausgeschlossen, Icon + Versionsinfos per rcedit). |

## Attach-Modus (Entwicklung und Tests)

Statt den Kern selbst zu starten, verbindet sich die Shell mit einem bereits
laufenden Kern, wenn **beide** Variablen gesetzt sind:

```
SPAIDER_FOLDABLE_CORE_URL=http://127.0.0.1:8765
SPAIDER_FOLDABLE_CORE_TOKEN=mock-token
```

Der Hauptprozess prüft `GET /api/health` (bis 30 s lang) und meldet dann
`ready`. Beim Beenden wird `POST /api/system/shutdown` an den externen Kern
geschickt, aber nicht auf einen Prozess-Exit gewartet (der Prozess gehört uns
nicht). Der Mock-Kern beendet sich dabei nur mit `--exit-on-shutdown`.

Beispiel mit echtem Kern (PowerShell):

```powershell
$env:SPAIDER_FOLDABLE_CORE_URL = "http://127.0.0.1:43123"
$env:SPAIDER_FOLDABLE_CORE_TOKEN = "<token aus SPAIDER_FOLDABLE_TOKEN>"
npm run dev
```

## Paketierte EXE mit Mock testen

```powershell
npm run package
npm run mock                     # in einem zweiten Terminal (optional: -- --state ready)
$env:SPAIDER_FOLDABLE_ROOT = "C:\tmp\spaider-root"      # wird im paketierten Modus ignoriert, siehe unten
$env:SPAIDER_FOLDABLE_CORE_URL = "http://127.0.0.1:8765"
$env:SPAIDER_FOLDABLE_CORE_TOKEN = "mock-token"
$env:SPAIDER_FOLDABLE_SCREENSHOT = "C:\tmp\spaider.png"   # optional: Screenshot, sobald der Kern `ready` meldet (+1,5 s), dann beenden
.\out\SpAIder-win32-x64\SpAIder.exe
```

`SPAIDER_FOLDABLE_SCREENSHOT` wartet auf den Zustand `ready` des Kerns (spätestens
45 s), lässt die erste Ansicht 1,5 s rendern, schreibt die PNG und beendet die
App über den normalen Shutdown-Weg. `SPAIDER_FOLDABLE_PERF=1` lädt beide
Fenster mit `?perf=1`, damit `window.__spaiderPerf` auch im gebauten Renderer
existiert (nur für Messungen).

Hinweis: Im **paketierten** Modus ist der App-Root das Verzeichnis der EXE
(`out/SpAIder-win32-x64/`), die Daten liegen in `.../data/`. Nur wenn
`SPAIDER_FOLDABLE_ROOT` gesetzt ist, wird stattdessen dieser Ordner benutzt;
der Attach-Modus (beide `SPAIDER_FOLDABLE_CORE_*`-Variablen) gilt ebenfalls
paketiert. Endnutzer setzen diese Variablen nie (siehe NOTES.md).

## Live-Overlay, Hotkeys, Tray (v1.2, Shell 0.3)

### Hotkeys

- `hotkey_overlay` (Standard `Ctrl+Alt+Space`) öffnet/schließt das Live-Fenster,
  `hotkey_stop` (Standard `Ctrl+Alt+Backspace`) stoppt sofort alles. Beide
  liest der Hauptprozess nach `ready` aus `GET /api/settings` und registriert sie
  per `globalShortcut`. Nach einer Änderung in den Einstellungen (PATCH) werden
  sie neu registriert.
- Fehlschläge werden **gemeldet, nicht verschluckt**: ungültige Syntax,
  identische Kombinationen und von anderen Programmen belegte Kombinationen
  stehen in `HotkeyResult.errors`, im `launcher.log` („hotkey problem: …“) und
  unter Einstellungen → Live & Agent → „Prüfen“. Auf dem Entwicklungsrechner
  ist `Ctrl+Alt+Space` tatsächlich belegt (siehe NOTES.md) – die Shell läuft
  dann mit dem Stopp-Hotkey weiter und das Live-Fenster bleibt über Seitenleiste,
  Tray und Einstellungen erreichbar.
- Accelerator-Prüfung (`src/shared-types/accelerator.ts`, rein, getestet):
  Electron-Syntax, genau eine Taste, mindestens Ctrl/Alt/Super/Cmd (Shift allein
  würde normales Tippen blockieren).

### Overlay-Hotkey → Vordergrund → Fenster

1. `globalShortcut`-Callback: `performance.now()` wird festgehalten.
2. **Zuerst** `POST /api/live/foreground` (Timeout 400 ms; der Vertrag
   zielt auf ≤ 50 ms) – damit die Zielidentität (hwnd/pid/Prozess/Titel) noch
   stimmt, bevor das Overlay selbst den Vordergrund übernimmt.
3. Dann `overlay.show(sourceHint)`: das Fenster wird auf dem Monitor des
   Zielfensters (sonst des Mauszeigers) gezeigt und der Hint per IPC an den
   Overlay-Renderer übergeben. Ist das Overlay sichtbar, blendet der Hotkey es
   aus (Toggle).
4. Messung: `overlay_foreground_ms`, `overlay_hotkey_to_show_ms` (bis zum
   `show`-Ereignis des BrowserWindow), `overlay_show_to_painted_ms` und
   `overlay_hotkey_to_painted_ms` (der Renderer meldet den ersten Paint nach
   zwei `requestAnimationFrame`). Alle Werte landen im `launcher.log`
   („perf …=…“), die letzten 20 über `window.spaider.getPerfSamples()`.

### Overlay-Fenster

Eigenes BrowserWindow (`src/main/overlay.ts`): frameless, alwaysOnTop,
`skipTaskbar`, nicht minimierbar, in Grenzen größenveränderbar
(520×360, min 360×240), `setContentProtection(true)` (nie in Aufnahmen),
Position und Größe **je Monitor** in `data/electron/overlay.json`. Es wird beim
Start versteckt angelegt, damit der Hotkey nur noch `show()` auslösen muss, und
lädt denselben Renderer mit `#/overlay` (gleiche Preload-Bridge, gleiches Token).
Alt+F4/Schließen blenden es nur aus; unter Windows bekommt danach das zuvor
aktive Fenster den Fokus zurück.

Inhalt (`src/renderer/screens/OverlayScreen.tsx`):

- Kopfzeile (Ziehbereich) mit „Ziel: <Titel> – <Prozess>“ und „Quelle wählen“.
  Der Picker (`components/overlay/SourcePicker.tsx`) listet
  `GET /api/live/sources` (Fenster + Bildschirme, nie `is_self`), hebt den
  Vordergrund-Hint hervor; Pfeiltasten, Enter, Esc.
- **Ohne bestätigte Quelle keine Sitzung**: beim ersten Öffnen muss der Picker
  bestätigt werden; später ist der Vordergrund-Hint vorausgewählt und sichtbar –
  ein Klick auf „Bestätigen“ oder Enter im Eingabefeld bestätigt ihn (Enter
  sendet die Frage dann gleich mit). Ein neues Ziel führt zu
  `PATCH /api/live/sessions/{id}` (bewusster Wechsel), nie zu einem stillen.
- Eingabefeld: Enter sendet, Shift+Enter neue Zeile, Esc schließt. Erste Frage
  → `POST /api/live/sessions` mit der Quelle, dann
  `POST /api/live/sessions/{id}/ask` (SSE, gleicher Parser wie der Chat,
  zusätzlich `observation`).
- Aktivitätszeile (`ActivityLine`, inkl. der neuen Phasen `capture`,
  `observe`, `image_encode`), Antwort als Klartext während des Streams und
  Markdown nach `done`, Stopp (`POST …/stop`), „Im Hauptfenster öffnen“
  (fokussiert das Hauptfenster auf dem geteilten Gespräch), „Als Aufgabe
  ausführen“ (öffnet nur das vorausgefüllte Aufgaben-Formular im Hauptfenster
  – gestartet wird erst mit „Starten“), Vorschau-Umschalter (Einstellung
  `live_preview`).
- Vorschau: nach jeder Antwort `POST …/snapshot {preview:true}`; gezeigt wird
  nur `preview_png_base64`, das Vollbild bleibt im Kern. Qualitätszustände
  (`lost`, `minimized`, `black`, `occluded`, `error`) und `source_lost` erscheinen
  als kurze deutsche Zeile mit „Quelle neu wählen“.

Im Dev-Modus: `npm run dev:mock`, dann Hotkey drücken oder in der Seitenleiste
„Live-Fenster“ bzw. Einstellungen → Live & Agent → „Live-Fenster testen“
(`window.spaider.overlay.toggle()`). F12 öffnet auch im Overlay die DevTools.

### Tray

Tray-Symbol mit Menü „Öffnen“, „Live-Fenster“, „Stopp“, „Beenden“
(`src/main/tray.ts`). Mit `tray_mode` (Einstellungen → Live & Agent) blendet
das Schließen des Hauptfensters es nur aus (Hotkeys und Overlay laufen weiter);
„Beenden“ im Tray oder „Sicher beenden“ fahren wie bisher den Kern herunter.
Es wird **kein Autostart** eingerichtet (`app.setLoginItemSettings` wird nicht
aufgerufen).

### Stopp-Hotkey

Sofort, ohne auf irgendetwas zu warten: `POST /api/agent/stop` und
`POST /api/conversations/{id}/stop` für jedes Gespräch, das ein Renderer gerade
als streamend gemeldet hat (`reportActiveConversations`), jeweils mit 2 s
Timeout und ohne Verkettung; parallel `onHotkey({kind:"stop"})` an beide
Fenster, die lokal „Wird gestoppt …“ zeigen. Der globale Kanal liefert danach
`event: stop`.

## Aufgaben (Agent, v1.2)

Seitenleiste → Bereich „Aufgaben“ (`components/tasks/`):

- **Neue Aufgabe**: Ziel, Zugriffe (Dateien: Ordner über `pickFolder`, Liste
  entfernbar, Schreibordner; Browser; Fenster aus `/api/live/sources` plus
  erlaubte Prozesse; Gedächtnis; externe Wirkung standardmäßig aus), Budget aus
  `agent_max_steps`/`agent_max_seconds`. Die Scopes stammen ausschließlich aus
  diesem Formular (`store/tasks.ts: draftToRequest`).
- **Liste** (Seitenleiste): Zustandsbadge mit deutschem Text für jeden
  `TaskState`, `Schritt n/max`, Zusammenfassung, letzte Aktion, Stopp/Pause/Weiter
  beim Überfahren. Gespeist aus `GET /api/agent/tasks`, dem Ereignisstrom je
  Aufgabe und `event: task` des globalen Kanals.
- **Detail**: Kopf mit Badge, Schritten, Modellaufrufen, Zugriffen, Knöpfen,
  Ergebnis (geprüft/ungeprüft, Artefakte), Rückfrage-Dialog bei
  `pending_question` (Freigeben/Ablehnen bzw. Texteingabe →
  `POST …/answer`), „Als Ablauf speichern“ (`POST …/save-recipe` mit Name und
  optionalen Parametern), Aktions-Zeitstrahl (Werkzeug, kompakte Argumente,
  Zustand, Ergebnis, Dauer, Beleg-Pfad zum Kopieren) aus
  `GET /api/agent/tasks/{id}/events`, Erklärtext des Agenten (als Text, nicht als
  Beleg).
- **Abläufe**: `GET /api/agent/recipes` mit Löschen und „Mit diesem Ablauf
  starten“ (füllt das Formular mit `recipe_id` und den Scopes vor).

## Einstellungen → Live & Agent

Hotkeys (Eingabe mit Syntaxprüfung, „Prüfen“ registriert wirklich und zeigt das
Ergebnis, „Übernehmen“ speichert per PATCH), Tray-Modus, Vorschaubild,
`agent_max_steps`, `agent_max_seconds` und die Zeile **„Eigener Browser für
Aufgaben“**: Status aus `GET /api/agent/browser` (installiert / nicht
installiert + `reason_de`, Paketname, Größe, Lizenz) und „Browser laden
(≈ 207 MB)“ → `POST /api/agent/browser/install`; der Job (Kind `browser`) wird
wie ein Modell-Download über `GET /api/models/jobs/{id}/events` verfolgt und mit
`JobProgress` (Fortschritt, Abbrechen) angezeigt. Solange der Browser fehlt,
ist der Browser-Schalter im Aufgaben-Formular deaktiviert und verweist auf diese
Zeile.

## Aktivität, Hintergrundkanal, Denktext (v1.1)

- **Aktivitätszeile** unter der entstehenden Antwort (`event: activity` im
  Chat-Stream): aktueller Schritt (`message_de`), Prozent nur für
  `prompt_processing` (aus `counters.prompt_processed/prompt_total`), Token/s
  nur aus `counters.tokens_per_second`, laufende Zeit seit dem ersten Ereignis
  (250-ms-Takt), „Details“ mit allen Ereignissen (Phase, Zustand, `t_ms`,
  Zähler, Quellen-IDs) und ein zweiter „Stopp“-Knopf. Nach `answer_done`
  bleibt eine Zeile wie „Antwort fertig · 3,8 s · 24 Token/s“; `saved`,
  `postprocess_pending/running/done` werden getrennt angezeigt. `cancelled` →
  „Abgebrochen“, `failed` → rot mit `message_de`. Ereignisse fremder
  `request_id`s oder mit kleinerer `seq` werden verworfen
  (`src/renderer/store/activity.ts`, rein und getestet).
- **Stopp** zeigt sofort lokal „Wird gestoppt …“; das echte
  `answer_done: cancelled` des Kerns ersetzt den Text.
- **Globaler Kanal** `GET /api/activity` (fetch-SSE mit Token, eine Verbindung
  pro Fenster, Reconnect mit Backoff 1 s … 30 s, `src/renderer/lib/activityChannel.ts`):
  `activity` mit `conversation_id` aktualisiert den Badge des Gesprächs
  (`postprocessing_pending`) und die eingeklappte Zeile der betroffenen
  Nachricht; `scheduler` zeigt im Sidebar-Fuß „Gedächtnis wird im Hintergrund
  aktualisiert · n ausstehend“, solange `background.kind` gesetzt ist;
  `runtime` aktualisiert den Laufzeitzustand und lädt `setup/status` nach;
  v1.2: `task` aktualisiert die Aufgabenliste, `live` die Live-Sitzung des
  Overlays, `stop` zeigt „Globaler Stopp“.
- **Denktext des Modells**: eingeklappter Abschnitt über der Antwort, nur wenn
  das aktive Modell `capabilities.reasoning_toggle` hat **und** `thinking`-
  Ereignisse ankommen. Er wird nie als Aktionen beschriftet.
- Kein erfundener Fortschritt: Die Oberfläche rendert ausschließlich, was die
  Ereignisse sagen; fehlt ein Messpunkt, fehlt die Zeile.

## Leistung beim Streaming und Messhilfen

- Token- und Denktext werden gesammelt und höchstens alle 40 ms in den Store
  geschrieben (`src/renderer/lib/batch.ts`).
- Während des Streams wird die Antwort als reiner Text (Zeilenumbrüche,
  Cursor) gerendert; vollständiges Markdown mit Hervorhebung erst nach `done`.
  Fertige Nachrichten sind memoisiert und tragen `content-visibility: auto`.
- Dev-Messhilfe `window.__spaiderPerf` (nur unter `vite dev`, mit `?perf=1`
  oder `SPAIDER_FOLDABLE_PERF=1`): Frame-Lücken je Stream (`last`), dazu v1.2
  `samples` (Renderer: `overlay_input_to_dom_ms` = Tastendruck → übernommener
  DOM-Zustand des Overlay-Eingabefelds, `overlay_stop_to_status_ms` = Klick auf
  Stopp → Statuswechsel), `mainSamples()` (Hauptprozess: Hotkey → Fenster →
  Paint) und `all()`.

## Modelle (v1.1)

Setup-Screen und Einstellungen → Modelle bieten dieselben drei Wege
(`src/renderer/components/models/ModelPaths.tsx`):

1. **Für diesen Computer empfohlen** – `GET /api/models/recommendations?focus=`
   mit Schwerpunkt Alltag / Dokumente / Coding-Fragen, Hardwarezeile aus
   `GET /api/system/hardware`, bis zu drei Karten (Ausgewogen / Schnell und
   sparsam / Mehr Qualität) mit Gründen, Download-Größe, RAM/VRAM-Schätzung,
   Verifikation (getestet / nach Metadaten geprüft / ungeprüft), Lizenzart,
   Konto-/Zustimmungshinweis, Messwert nur wenn `measured` vorhanden
   (sonst „noch nicht gemessen“), ausgeschlossene Modelle mit Grund,
   „Katalogstand: …“.
2. **Weitere Modelle** – Suche (`GET /api/models/search`, Treffer immer
   „ungeprüft“, `offline:true` als ruhiger Hinweis), Repository-ID prüfen
   (`POST /api/models/resolve`: Dateien mit Größe und Prüfsumme ja/nein,
   Kompatibilitätsurteil, Fit, 409 `gated` → Konto/Zustimmung nötig, ohne
   Umgehungsvorschlag), Download über die dynamische ID `hf:…`,
   „Katalog aktualisieren“ (`POST /api/models/catalog/refresh`) mit Toast,
   darunter der eingebaute Katalog.
3. **Lokales Modell importieren** – bisheriger Ablauf mit Texten für
   `split_incomplete`, `not_chat_model`, `arch_unsupported`, `role_mismatch`
   (`src/renderer/lib/modelErrors.ts`).

Installierte Modelle zeigen `capabilities` (Kontext, Denken ja/nein,
Backends), `verification`, `measured` und „Kurz messen“
(`POST /api/models/{id}/calibrate`, Job abbrechbar).

Einstellungen → Einstellungen: Leistungsmodus (Automatisch/Ausgewogen, Leise,
Schnell) und – nur wenn `hardware.gpus` nicht leer ist – Grafikkarte
(Automatisch / Aus).

## Mock-Kern (`mock/server.mjs` + `mock/live-agent.mjs`)

Ohne Abhängigkeiten, Port 8765, Token `mock-token`. Testhaken:

- Nachricht mit „fehler“ → `generating failed` + `error`; mit „lang“ → lange
  Antwort (Leistungsmessung); Einstellung „Nachdenken“ → `thinking`-Ereignisse.
- Chat-Stream sendet `activity` in Vertragsreihenfolge (Gedächtnissuche,
  Kontext, `prompt_processing` mit Fortschritt, `generating` alle 250 ms mit
  Token/s, `answer_done`, `saved`, `postprocess_pending`); danach über
  `GET /api/activity`: `scheduler` und `postprocess_running/done` mit
  `conversation_id`; Gespräche tragen `postprocessing_pending`; Nachrichten
  `timings`.
- Suche: `?offline=1`, `--offline` oder ein Suchbegriff mit „offline“ →
  `offline:true`. Auflösen: Repo mit „gated“/„meta-llama“/„google/gemma“ →
  409 `gated`; „split“ → zwei Teile (eine ohne Prüfsumme); „base“/„nochat“ →
  nicht kompatibel; „missing“ → 404.
- Import: Pfad mit „invalid“, „split“, „embed“/„nochat“, „unsupported“,
  „rolemismatch“ → entsprechender Fehlercode.
- `qwen3.8-27b-community`: erst 409 `insufficient_space`, dann Abbruch bei
  30 % (fortsetzbar). Katalogeintrag `gemma-3-1b-it-qat` ist gated.
- `--state ready` installiert und aktiviert das erste Modell; „Kurz messen“
  schreibt `measured` in das Modellprofil.
- **v1.2** (`mock/live-agent.mjs`): 3 Fenster (Editor, Edge, minimierter
  Explorer) + SpAIder selbst (`is_self`, wird nie angeboten) + 2 Bildschirme;
  `POST /live/foreground` liefert den Editor; Sitzungen legen ein Gespräch
  „Live: <Titel>“ an; Snapshots mit erzeugtem PNG (`preview:true` → kleines
  Vorschaubild); der Ask-Stream sendet `capture`/`observe` (und `image_encode`
  nur mit Bildmodell) sowie `observation` vor dem regulären Verlauf. Frage mit
  „verloren“ → 409 `source_lost` (Sitzung `lost`), mit „schwarz“ → Qualität
  `black`, Sitzung auf dem Explorer → `minimized`. Aufgaben laufen mit einer
  Aktion alle ~800 ms durch `created → observing → planning → validating →
  executing → verifying → completed`; Ziel mit „mail“/„send“/„bestell“/
  „veröffentl“/„freigabe“/„frage“/„bestätig“ → Rückfrage (Freigabe) bei Schritt
  3, mit „eingabe“ → Texteingabe, mit „fehler“ → `failed`. Pause/Weiter/Stopp,
  `save-recipe`, ein vorgeseedeter Ablauf, `POST /agent/stop` (bricht Aufgaben
  und laufende Live-Antworten ab, `event: stop`).

## Pfade und Portabilität

- `appRoot`: paketiert `dirname(SpAIder.exe)`; dev `--app-root=<dir>`,
  `SPAIDER_FOLDABLE_ROOT` oder `foldable/desktop/dev-data`.
- `dataDir = <appRoot>/data`. Vor `app.whenReady()` werden `userData`,
  `sessionData`, `temp` und `logs` nach `data/electron/...` bzw. `data/logs`
  umgebogen; Fensterposition in `data/electron/window.json`, Overlay-Position
  je Monitor in `data/electron/overlay.json`.
- Logs: `data/logs/launcher.log` (Shell: Zustände, Zeiten, Hotkey-Ergebnisse –
  nie Frage- oder Antworttexte), `data/logs/core-stderr.log` (stderr des Kerns,
  Rotation bei 5 MB).
- Der Kern wird paketiert aus `resources/core/spaider-foldable-core.exe` mit
  `--app-root <appRoot>` gestartet, Umgebungsvariablen
  `SPAIDER_FOLDABLE_TOKEN` (32 Byte Hex) und `SPAIDER_FOLDABLE_RUNTIME_DIR`
  (`resources/runtime/llama`).

## Sicheres Beenden

Fenster schließen (ohne Tray-Modus), „Sicher beenden“, Tray → „Beenden“ oder
`before-quit` lösen dieselbe Sequenz aus: Hotkeys abmelden, Overlay und Tray
zerstören → `POST /api/system/shutdown` → bis 20 s auf den Prozess-Exit warten →
notfalls `taskkill /PID <pid> /T /F` → erst dann wird das Fenster zerstört. Der
gewählte Weg steht im `launcher.log`.

## Sicherheit

`contextIsolation`, `sandbox`, kein `nodeIntegration`, `webSecurity`, strikte
CSP in `index.html` (nur `connect-src http://127.0.0.1:*`, `img-src 'self' data:`
für die Vorschau), `window.open` wird verweigert, Navigation blockiert, keine
Berechtigungen außer Zwischenablage – für Haupt- und Overlay-Fenster gleich.
Markdown wird ohne Roh-HTML gerendert; Links öffnen nur über die Allowlist
(`https://huggingface.co/`, `https://github.com/ggml-org/llama.cpp`). Der
Denktext wird als reiner Text gerendert. Electron nimmt keine Bildschirme auf
und bedient keine Fenster – das tut ausschließlich der Kern; das Overlay zeigt
nur, was der Kern liefert, und ist per `setContentProtection(true)` selbst nie
Teil einer Aufnahme.

## Struktur

```
foldable/desktop/
  index.html               Renderer-Einstieg mit CSP
  src/main/                Electron-Hauptprozess (paths, core, window-state, log, index,
                           hotkeys, overlay, tray, perf)
  src/preload/index.ts     window.spaider-Bridge (contextBridge)
  src/shared-types/        Bridge-Typen, IPC-Kanalnamen, Allowlist, Accelerator-Prüfung
  src/renderer/
    main.tsx               Hash-Routing: #/overlay -> OverlayApp, sonst App
    App.tsx / OverlayApp.tsx
    api/                   types (v1 + v1.1 + v1.2), client, sse
    store/                 useAppStore (Chat, Aufgaben, Kanal), useOverlayStore, reducers,
                           activity, tasks (rein), overlay (rein)
    lib/                   batch, perf, activityChannel, chatStream, modelErrors, format, labels, jobs, notify
    components/chat/       ChatArea, MessageBubble (StreamingText), ActivityLine, ThinkingBlock, Markdown, …
    components/overlay/    SourcePicker, OverlayInput
    components/tasks/      TasksArea, TaskCreateForm, TaskDetail (Rückfrage, Ablauf speichern),
                           TasksSidebarList, RecipesList, TaskStateBadge
    components/models/     ModelPaths, RecommendationsPanel/Card, ModelSearchPanel, InstalledModelRow, …
    components/settings/   ModelsTab, PreferencesTab, LiveAgentTab, …
    screens/               BootScreen, SetupScreen, MainScreen, OverlayScreen
  mock/server.mjs          Mock-Kern nach API-Vertrag v1 + v1.1 (ohne Abhängigkeiten)
  mock/live-agent.mjs      Mock-Erweiterung v1.2 (Quellen, Sitzungen, Agent, Abläufe)
  scripts/                 dev-electron, package, make-icon
  tests/                   Vitest
  assets/                  icon.png / icon.ico (generiert)
```

Die geteilten Komponenten `Toast`, `ConfirmDialog`, `LoadingSpinner` und `cn()`
werden direkt aus `frontend/src` über den Vite-Alias `@` eingebunden; das
Farbschema ist in `src/renderer/styles.css` als Tailwind-v4-`@theme` aus
`frontend/tailwind.config.ts` übernommen.
