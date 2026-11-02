# SpAIder Foldable – Desktop-Shell (Electron + React)

Portable, vollständig lokale Chat-Oberfläche für den SpAIder-Foldable-Kern
(`foldable/core`). Dieses Paket enthält den Electron-Hauptprozess (Start/Stopp
des Kerns, Portabilität, Sicherheit), das Preload-Bridge `window.spaider` und
die React-Oberfläche (deutsch, dunkles SpAIder-Design).

Der HTTP-Vertrag zum Kern steht in `docs/portable/API_CONTRACT.md`.

## Voraussetzungen

- Node 24 und npm 11 (keine nativen Module, kein Compiler nötig; Electron und
  TypeScript laden fertige Binärdateien).
- Im Bash-Tool: `export PATH="/c/Users/mucbay/AppData/Roaming/fnm/node-versions/v24.21.0/installation:$PATH"`.
- `npm install` (lädt Electron 45 von GitHub in den npm-Cache).

## Skripte

| Skript | Zweck |
| --- | --- |
| `npm run dev` | Vite-Devserver + Electron (`--app-root=dev-data`). Erwartet einen laufenden Kern im **Attach-Modus** (siehe unten) – sonst versucht Electron, `python -m spaider_foldable` aus `../core` zu starten. |
| `npm run dev:mock` | Startet den Mock-Kern (`mock/server.mjs`, Port 8765, Token `mock-token`), Vite und Electron im Attach-Modus. Damit lässt sich jede Oberfläche ohne Python/Modelle durchspielen. |
| `npm run mock` | Nur den Mock-Kern starten (z. B. für die paketierte EXE). |
| `npm run build` | `tsc` für `src/main` + `src/preload` nach `dist/main`, `dist/preload` (CommonJS) und `vite build` nach `dist/renderer` (relative Pfade, läuft über `file://`). |
| `npm start` | Electron mit dem gebauten Renderer (ohne Vite), `--app-root=dev-data`. |
| `npm run typecheck` | Typprüfung beider Projekte (`tsconfig.main.json`, `tsconfig.renderer.json`). |
| `npm test` | Vitest (SSE-Parser, API-Client-Fehler, Markdown-Renderer, Byteformatierung, Store-Reducer). |
| `npm run lint` | ESLint (Flat Config, typescript-eslint, react-hooks). |
| `npm run icon` | Erzeugt `assets/icon.png` und `assets/icon.ico` rein in JS (kein Download, keine nativen Module). |
| `npm run package` | Build + `@electron/packager` → `out/SpAIder-win32-x64/SpAIder.exe` (asar, `node_modules` ausgeschlossen, Icon + Versionsinfos per rcedit). |

## Attach-Modus (Entwicklung und Tests)

Statt den Kern selbst zu starten, verbindet sich die Shell mit einem bereits
laufenden Kern, wenn **beide** Variablen gesetzt sind (nur wenn nicht paketiert):

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
npm run mock                     # in einem zweiten Terminal
$env:SPAIDER_FOLDABLE_ROOT = "C:\tmp\spaider-root"      # wird im paketierten Modus ignoriert, siehe unten
$env:SPAIDER_FOLDABLE_CORE_URL = "http://127.0.0.1:8765"
$env:SPAIDER_FOLDABLE_CORE_TOKEN = "mock-token"
$env:SPAIDER_FOLDABLE_SCREENSHOT = "C:\tmp\spaider.png"   # optional: Screenshot nach 3 s, dann beenden
.\out\SpAIder-win32-x64\SpAIder.exe
```

Hinweis: Im **paketierten** Modus ist der App-Root das Verzeichnis der EXE
(`out/SpAIder-win32-x64/`), die Daten liegen in `.../data/`. Nur wenn
`SPAIDER_FOLDABLE_ROOT` gesetzt ist, wird stattdessen dieser Ordner benutzt;
der Attach-Modus (beide `SPAIDER_FOLDABLE_CORE_*`-Variablen) gilt ebenfalls
paketiert. Endnutzer setzen diese Variablen nie (siehe NOTES.md).

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
(`https://huggingface.co/`, `https://github.com/ggml-org/llama.cpp`).

## Struktur

```
foldable/desktop/
  index.html               Renderer-Einstieg mit CSP
  src/main/                Electron-Hauptprozess (paths, core, window-state, log, index)
  src/preload/index.ts     window.spaider-Bridge (contextBridge)
  src/shared-types/        Bridge-Typen, IPC-Kanalnamen, Allowlist
  src/renderer/            React-App (api/, store/, components/, screens/, lib/)
  mock/server.mjs          Mock-Kern nach API-Vertrag (ohne Abhängigkeiten)
  scripts/                 dev-electron, package, make-icon
  tests/                   Vitest
  assets/                  icon.png / icon.ico (generiert)
```

Die geteilten Komponenten `Toast`, `ConfirmDialog`, `LoadingSpinner` und `cn()`
werden direkt aus `frontend/src` über den Vite-Alias `@` eingebunden; das
Farbschema ist in `src/renderer/styles.css` als Tailwind-v4-`@theme` aus
`frontend/tailwind.config.ts` übernommen.
