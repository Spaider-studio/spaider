# SpAIder Foldable – Desktop-Shell (Electron + React)

Portable, vollständig lokale Chat-Oberfläche für den SpAIder-Foldable-Kern
(`foldable/core`). Dieses Paket enthält den Electron-Hauptprozess (Start/Stopp
des Kerns, Portabilität, Sicherheit), die Preload-Bridge `window.spaider` und
die React-Oberfläche (deutsch, dunkles SpAIder-Design).

Der HTTP-Vertrag zum Kern steht in `docs/portable/API_CONTRACT.md` (v1 und
Addendum v1.1); die Aktivitätsereignisse in `docs/portable/ACTIVITY_EVENTS.md`.
Version 0.2.0 der Shell setzt das Addendum v1.1 um (Aktivitätszeile,
Hintergrundkanal, Denktext, Empfehlungen/Suche/Auflösen, Leistungsmodus).

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
| `npm run dev:mock` | Startet den Mock-Kern (`mock/server.mjs`, Port 8765, Token `mock-token`), Vite und Electron im Attach-Modus. Damit lässt sich jede Oberfläche ohne Python/Modelle durchspielen. |
| `npm run mock` | Nur den Mock-Kern starten (z. B. für die paketierte EXE). Optionen: `--state ready` (Modell bereits aktiv), `--offline`, `--no-gpu`, `--fat32`, `--exit-on-shutdown`. |
| `npm run build` | `tsc` für `src/main` + `src/preload` nach `dist/main`, `dist/preload` (CommonJS) und `vite build` nach `dist/renderer` (relative Pfade, läuft über `file://`). |
| `npm start` | Electron mit dem gebauten Renderer (ohne Vite), `--app-root=dev-data`. |
| `npm run typecheck` | Typprüfung beider Projekte (`tsconfig.main.json`, `tsconfig.renderer.json`). |
| `npm test` | Vitest: SSE-Parser, API-Client-Fehler, Markdown-Renderer, Byteformatierung, Store-Reducer, Aktivitäts-Reducer, Token-Batching + Reconnect-Backoff, Empfehlungskarten/Auflösungsergebnis, Fehlerzuordnung (gated/split/Import-Codes). |
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
App über den normalen Shutdown-Weg.

Hinweis: Im **paketierten** Modus ist der App-Root das Verzeichnis der EXE
(`out/SpAIder-win32-x64/`), die Daten liegen in `.../data/`. Nur wenn
`SPAIDER_FOLDABLE_ROOT` gesetzt ist, wird stattdessen dieser Ordner benutzt;
der Attach-Modus (beide `SPAIDER_FOLDABLE_CORE_*`-Variablen) gilt ebenfalls
paketiert. Endnutzer setzen diese Variablen nie (siehe NOTES.md).

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
  pro Sitzung, Reconnect mit Backoff 1 s … 30 s, `src/renderer/lib/activityChannel.ts`):
  `activity` mit `conversation_id` aktualisiert den Badge des Gesprächs
  (`postprocessing_pending`) und die eingeklappte Zeile der betroffenen
  Nachricht; `scheduler` zeigt im Sidebar-Fuß „Gedächtnis wird im Hintergrund
  aktualisiert · n ausstehend“, solange `background.kind` gesetzt ist;
  `runtime` aktualisiert den Laufzeitzustand und lädt `setup/status` nach.
- **Denktext des Modells**: eingeklappter Abschnitt über der Antwort, nur wenn
  das aktive Modell `capabilities.reasoning_toggle` hat **und** `thinking`-
  Ereignisse ankommen. Er wird nie als Aktionen beschriftet.
- Kein erfundener Fortschritt: Die Oberfläche rendert ausschließlich, was die
  Ereignisse sagen; fehlt ein Messpunkt, fehlt die Zeile.

## Leistung beim Streaming

- Token- und Denktext werden gesammelt und höchstens alle 40 ms in den Store
  geschrieben (`src/renderer/lib/batch.ts`).
- Während des Streams wird die Antwort als reiner Text (Zeilenumbrüche,
  Cursor) gerendert; vollständiges Markdown mit Hervorhebung erst nach `done`.
  Fertige Nachrichten sind memoisiert und tragen `content-visibility: auto`.
- Dev-Messhilfe `window.__spaiderPerf` (nur unter `vite dev` oder wenn die
  Seite mit `?perf=1` geladen wird): Der Store startet die Messung beim
  Stream-Beginn und stoppt sie am Ende; `window.__spaiderPerf.last` enthält
  `maxGapMs` (größte Lücke zwischen zwei Frames), `meanGapMs`, `frames`,
  `gapsOver50ms`, `durationMs`. `start()`/`stop()`/`report()` manuell nutzbar.

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

## Mock-Kern (`mock/server.mjs`)

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

## Pfade und Portabilität

- `appRoot`: paketiert `dirname(SpAIder.exe)`; dev `--app-root=<dir>`,
  `SPAIDER_FOLDABLE_ROOT` oder `foldable/desktop/dev-data`.
- `dataDir = <appRoot>/data`. Vor `app.whenReady()` werden `userData`,
  `sessionData`, `temp` und `logs` nach `data/electron/...` bzw. `data/logs`
  umgebogen; Fensterposition in `data/electron/window.json`.
- Logs: `data/logs/launcher.log` (Shell), `data/logs/core-stderr.log`
  (stderr des Kerns, Rotation bei 5 MB).
- Der Kern wird paketiert aus `resources/core/spaider-foldable-core.exe` mit
  `--app-root <appRoot>` gestartet, Umgebungsvariablen
  `SPAIDER_FOLDABLE_TOKEN` (32 Byte Hex) und `SPAIDER_FOLDABLE_RUNTIME_DIR`
  (`resources/runtime/llama`).

## Sicheres Beenden

Fenster schließen, „Sicher beenden“ oder `before-quit` lösen dieselbe Sequenz
aus: Overlay in der Oberfläche → `POST /api/system/shutdown` → bis 20 s auf den
Prozess-Exit warten → notfalls `taskkill /PID <pid> /T /F` → erst dann wird das
Fenster zerstört. Der gewählte Weg steht im `launcher.log`.

## Sicherheit

`contextIsolation`, `sandbox`, kein `nodeIntegration`, `webSecurity`, strikte
CSP in `index.html` (nur `connect-src http://127.0.0.1:*`), `window.open` wird
verweigert, Navigation blockiert, keine Berechtigungen außer Zwischenablage.
Markdown wird ohne Roh-HTML gerendert; Links öffnen nur über die Allowlist
(`https://huggingface.co/`, `https://github.com/ggml-org/llama.cpp`). Der
Denktext wird als reiner Text gerendert.

## Struktur

```
foldable/desktop/
  index.html               Renderer-Einstieg mit CSP
  src/main/                Electron-Hauptprozess (paths, core, window-state, log, index)
  src/preload/index.ts     window.spaider-Bridge (contextBridge)
  src/shared-types/        Bridge-Typen, IPC-Kanalnamen, Allowlist
  src/renderer/
    api/                   types (v1 + v1.1), client, sse
    store/                 useAppStore (Streaming, Aktivität, Kanal), reducers, activity (Reducer)
    lib/                   batch, perf, activityChannel, modelErrors, format, labels, jobs, notify
    components/chat/       ChatArea, MessageBubble (StreamingText), ActivityLine, ThinkingBlock, Markdown, …
    components/models/     ModelPaths, RecommendationsPanel/Card, ModelSearchPanel, InstalledModelRow,
                           HardwareSummary, ModelBadges, CatalogCard, JobProgress, useModelJobs
    components/settings/   ModelsTab, PreferencesTab (Leistung), …
    screens/               BootScreen, SetupScreen, MainScreen
  mock/server.mjs          Mock-Kern nach API-Vertrag v1 + v1.1 (ohne Abhängigkeiten)
  scripts/                 dev-electron, package, make-icon
  tests/                   Vitest
  assets/                  icon.png / icon.ico (generiert)
```

Die geteilten Komponenten `Toast`, `ConfirmDialog`, `LoadingSpinner` und `cn()`
werden direkt aus `frontend/src` über den Vite-Alias `@` eingebunden; das
Farbschema ist in `src/renderer/styles.css` als Tailwind-v4-`@theme` aus
`frontend/tailwind.config.ts` übernommen.
