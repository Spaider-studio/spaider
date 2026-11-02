# NOTES – Annahmen, Abweichungen, bekannte Lücken

Stand: 2026-11-02. Bezugsdokument: `docs/portable/API_CONTRACT.md` (v1).

## Annahmen und Interpretationen des Vertrags

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
  Ein eventuell noch laufender Embedding-Download läuft serverseitig weiter und
  ist unter Einstellungen → Modelle sichtbar.
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
- **`setup/status`** wird alle 15 s neu geladen (nur bei sichtbarem Fenster),
  um Laufzeitzustand und Modellnamen im Sidebar-Footer aktuell zu halten.
- **Rollback-Hinweis**: Bei fehlgeschlagener Aktivierung zeigt die Job-Anzeige
  immer „Vorheriges Modell bleibt aktiv.“ – entsprechend der Vertragszusage.

## Abweichungen von der Aufgabenstellung

- **TypeScript 5.9.3 statt 7.0.2.** Das npm-Paket `typescript@7.0.2` enthält
  nur die native Go-Binärdatei (365 kB, kein `lib/typescript.js`).
  `typescript-eslint` 8.72 verlangt `typescript <6.1` **und** lädt die
  JS-API; npm kann die Peer-Abhängigkeit nicht verschachteln (ERESOLVE). Daher
  ist `typescript` auf 5.9.3 gepinnt – derselbe Code kompiliert identisch
  (`module: nodenext` für main/preload, `moduleResolution: bundler` für den
  Renderer). Wechsel auf 7.x, sobald typescript-eslint es unterstützt.
- **Umgebungs-Overrides auch paketiert**: `SPAIDER_FOLDABLE_ROOT` (App-Root)
  und `SPAIDER_FOLDABLE_CORE_URL`/`_TOKEN` (Attach-Modus) werden auch in der
  paketierten EXE beachtet, weil der Verifikationsschritt die EXE genau so mit
  dem Mock testet. Ohne diese Variablen gilt exakt die Vorgabe: App-Root =
  Verzeichnis der EXE, Kern wird aus `resources/core` gestartet. Endnutzer
  setzen diese Variablen nie.
- **Attach-Modus und Shutdown**: Beim Beenden wird dem externen Kern
  `POST /api/system/shutdown` geschickt (3 s Timeout), ohne auf einen
  Prozess-Exit zu warten – der Prozess wurde nicht von uns gestartet. Der
  Mock-Kern ignoriert den Shutdown, sofern er nicht mit `--exit-on-shutdown`
  läuft.
- **CSP im Dev-Modus**: Nur unter `vite dev` wird die Meta-CSP um
  `script-src 'unsafe-inline'` (React-Refresh-Preamble) und
  `ws://localhost:5173` (HMR) erweitert (Plugin `spaider-dev-csp` in
  `vite.config.mts`). Die gebaute `index.html` behält die strikte CSP.
- **`dependencies` vs. Paketinhalt**: Die Renderer-Bibliotheken stehen in
  `dependencies`, werden aber von Vite gebündelt. Der Packager schließt
  `node_modules` komplett aus (Hauptprozess nutzt nur Node/Electron-APIs),
  damit `app.asar` klein bleibt.
- **Hervorhebungs-Theme**: Statt eines highlight.js-Stylesheets sind die
  Token-Farben in `styles.css` in den SpAIder-Akzentfarben definiert.
- **Schriften**: Inter und JetBrains Mono werden aus
  `@fontsource-variable/*` lokal gebündelt (kein Netzwerk zur Laufzeit). Es
  werden alle Subsets emittiert (unicode-range lädt nur Latin).
- **Geteilte Frontend-Komponenten**: `Toast`, `ConfirmDialog`,
  `LoadingSpinner`, `cn()` werden direkt über den Alias `@` aus
  `frontend/src` importiert (keine Kopien). Die englischen Standardtexte von
  `ConfirmDialog` werden immer durch deutsche Labels überschrieben; `loading`
  (zeigt „Processing...“) wird nicht verwendet.

## Bekannte Lücken

- **Kein echter Kern getestet**: Alle Abläufe wurden gegen `mock/server.mjs`
  geprüft. Abweichungen des echten Kerns (z. B. andere Fehlercodes, andere
  Reihenfolge der SSE-Ereignisse) müssen beim ersten Integrationslauf
  nachgezogen werden.
- **Python-Spawn im Dev-Modus** (`python -m spaider_foldable` aus `../core`)
  ist implementiert, aber ungetestet, da der Kern noch nicht existiert.
- **ICO**: `assets/icon.ico` enthält 16/32/48 px als BMP und 256 px als PNG;
  ob rcedit das Icon ohne Adminrechte einbetten kann, steht im Abschnitt
  „Paketierung“ unten.
- **Dokumente** haben keine eigene Textansicht (`GET /documents/{id}/text`
  wird nicht genutzt).
- **`GET /api/memory/facts`** und `GET /api/system/logs` werden von der
  Oberfläche nicht angezeigt (nur Suche, Notizen, Überblick).
- **`system_prompt_extra`, `max_answer_tokens`, `threads`** sind bewusst nicht
  in der Oberfläche (Formulare klein halten); `PATCH /api/settings` würde sie
  unterstützen.
- **Fenster-Icon im Dev-Modus** kommt aus `assets/icon.png`; paketiert aus der
  EXE-Ressource.
- Lint läuft mit typescript-eslint ohne typgestützte Regeln (schnell, keine
  Projektreferenzen nötig).

## Paketierung (Ergebnis 2026-11-02)

- `npm run package` läuft ohne Adminrechte durch: Electron 45.2.1 wird aus dem
  npm-/Electron-Cache genommen, **rcedit hat Icon und Versionsinfos gesetzt**
  (geprüft per `Get-Item … .VersionInfo`: ProductName „SpAIder Foldable“,
  FileVersion 0.1.0, LegalCopyright „SpAIder - AGPL-3.0-only“; Icon per
  `ExtractAssociatedIcon` extrahierbar). Der Fallback ohne Metadaten in
  `scripts/package.mjs` wurde nicht gebraucht.
- Ergebnis: `out/SpAIder-win32-x64/SpAIder.exe` (≈ 250 MB Electron-Stub),
  `resources/app.asar` ≈ 1,1 MB (nur `dist/`, `assets/`, `package.json`).
- Die EXE wurde im Attach-Modus gegen den Mock-Kern gestartet
  (`SPAIDER_FOLDABLE_CORE_URL`/`_TOKEN`, `SPAIDER_FOLDABLE_ROOT`,
  `SPAIDER_FOLDABLE_SCREENSHOT`): Setup-Screen wird angezeigt, Screenshot
  geschrieben, Shutdown-Request erreicht den Mock, Fenster schließt.
- **Stolperstein in Agent-/VS-Code-Terminals:** Ist `ELECTRON_RUN_AS_NODE=1`
  in der Umgebung gesetzt, startet jede Electron-Binärdatei (auch
  `SpAIder.exe`) als reines Node und beendet sich sofort ohne Fenster.
  `scripts/dev-electron.mjs` entfernt die Variable deshalb; beim manuellen Start
  der EXE aus solchen Terminals vorher `unset ELECTRON_RUN_AS_NODE`.
- Zusätzlich wurde die Oberfläche mit einem Electron-Skript (außerhalb des
  Repos) gegen den Mock durchgeklickt: Katalog → Download → automatische
  Aktivierung → Chat mit Streaming/Markdown/Quellen → Fehler-Stream → Stopp →
  Datei-Import mit Indexierungs-Toast → alle Einstellungs-Tabs → Notiz +
  Suche → `insufficient_space` → fehlgeschlagener Download mit „Fortsetzen“
  → GGUF-Import → „Sicher beenden“ mit Overlay. Keine Konsolenfehler, keine
  CSP-Verstöße.
