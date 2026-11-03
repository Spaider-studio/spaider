# SpAIder Foldable 0.3: Agent und Live-Bildschirmhilfe – Architektur

Stand: 2026-11-04. Branch `feature/spaider-foldable-agent-live`. Dieses Dokument
beschreibt Datenfluss, Verträge, Zustandsmaschine und Prozess-/Rechtegrenzen
der neuen Funktionen. Die 0.1/0.2-Architektur (`ARCHITECTURE.md`) bleibt
unverändert darunter liegen; es gibt keinen zweiten Gedächtnisstack und
keinen zweiten Inferenzserver.

## 1. Komponenten und Prozessgrenzen

```
Electron-Launcher (SpAIder.exe)
 ├─ Hauptfenster (Renderer, sandboxed)      Chat, Aufgabenpanel, Einstellungen
 ├─ Overlay-Fenster (Renderer, #/overlay)   Live-Frage, Quelle, Status, Stopp
 ├─ Hotkeys (globalShortcut), Tray          Ctrl+Alt+Space / Ctrl+Alt+Backspace
 └─ CoreManager → startet und beendet den Kern
Python-Kern (spaider-foldable-core.exe, Loopback-API mit Token)
 ├─ observe/   Fenster/Monitore (Win32), Aufnahme (PrintWindow/mss), UIA (pywinauto, eigener COM-Thread), Snapshots
 ├─ live/      Live-Sitzungen (eine Quelle je Sitzung), Beobachtung für Chat-Antworten
 ├─ agent/     Aufgaben-Zustandsmaschine, Broker (Scopes/Validierung/Journal/Abbruch), Werkzeuge, Rezepte
 │   └─ tools/ files, memory, windows (UIA/SendInput), vision (Bildmodell), browser (Playwright)
 ├─ inference/ llama-server (ein Prozess, ein Slot) + optional Projektor (mmproj) für Bilder
 ├─ memory/    unverändert (SQLite/FTS5/Vektoren, Korrekturschicht)
 └─ scheduler  Prioritäten: Stopp > Nutzerfrage/Chat > Agentenschritt > Hintergrund (Extraktion)
Eigener Browser (Chromium for Testing, Playwright-Treiber im Kern) – nur für Aufgaben, eigenes Profil unter data/browser
```

Der Renderer hat keine Node-/Dateisystemrechte und spricht nur über die enge
Bridge (`window.spaider`) und die Token-geschützte Loopback-API. Der Kern
führt alle Beobachtungen und Aktionen aus; Electron zeigt nur Vorschaubilder,
die der Kern liefert. Bildverarbeitung, UIA-/COM-Aufrufe und Browsersteuerung
laufen in Threads bzw. Hilfsprozessen des Kerns, nie im Electron-Main oder
Renderer.

## 2. Live-Sitzung (Beobachten)

1. Hotkey → Launcher ruft `POST /api/live/foreground` **vor** dem Anzeigen des
   Overlays auf. Der Kern liest das Vordergrundfenster (hwnd + pid + Prozess +
   Titel) und schließt eigene Fenster (`is_self`) aus.
2. Overlay zeigt die vorgeschlagene Quelle; erste Aufnahme erst nach sichtbarer
   Bestätigung (oder bewusster Auswahl aus `GET /api/live/sources`).
3. `POST /api/live/sessions` bindet genau diese Quelle. Ein Wechsel in eine
   andere Anwendung erweitert die Sitzung nicht; `PATCH …/sessions/{id}` wechselt
   bewusst.
4. Frage (`POST …/ask`): Snapshot auf Abruf (keine Dauerschleife) =
   Fensteridentität neu prüfen → UIA-Struktur (begrenzt: Tiefe 12, 250
   Elemente, 4 000 Zeichen, 4 s) → optional Bild (PrintWindow, verkleinert auf
   max. 1 280 px lange Seite, Skalierungsfaktor gemerkt) → Beobachtung wird als
   Datenblock in die letzte Nutzernachricht gesetzt, das Bild als
   `image_url`-Teil, wenn das aktive Modell einen Projektor hat. Antwort über den
   normalen Chatpfad (Gedächtnissuche, Prompt-Cache, Aktivitätsereignisse,
   zusätzlich `capture`/`observe`/`image_encode` und `event: observation`).
5. Hauptchat und Overlay teilen das Gespräch der Sitzung.
6. Ende der Sitzung gibt Frames frei (nur die letzten zwei im RAM, nie auf
   Platte). Nach Neustart/Sperre wird keine Aufnahme automatisch wieder aktiv.

Das Overlay ist über `setContentProtection(true)` (WDA_EXCLUDEFROMCAPTURE) von
Aufnahmen ausgenommen; eigene SpAIder-Fenster erscheinen nie in der Quellenliste.

## 3. Snapshot-Vertrag

`snapshot_id`, `session_id`, `captured_at` (monotone Basis für `age_ms`),
`source_kind` (window|screen), `target` (hwnd, pid, process, title, rect,
monitor_id, scale), `quality` (ok|black|minimized|occluded|lost|error),
`text_excerpt`, `elements[]` (id, role, name, value, rect in physischen
Pixeln, enabled, focused, path), `elements_total`, `truncated`, `image`
(Breite/Höhe/Bytes, optional PNG/Vorschau), `transform` (origin_x/y, scale,
model_width/height). Gekürzte Snapshots sind als gekürzt markiert.

Koordinaten: physische Pixel (Prozess ist Per-Monitor-DPI-v2-bewusst), negative
Monitorursprünge zulässig; Modellkoordinaten → Bild → Bildschirm über
`observe.capture.model_to_screen` (getestet). Das Bildmodell kann seine
Koordinaten normiert (0–1000) oder absolut liefern; `GroundingCalibrator`
bestimmt die Konvention je Modell mit einem synthetischen Bild bekannter
Zielposition, bevor `vision.locate` verwertbare Punkte liefert.

## 4. Agent: Zustandsmaschine und Loop

```
created → observing → planning → validating → executing → verifying → (weiterer Schritt) → completed
                   ↘ waiting_for_user ↗            ↘ failed | cancelled | paused | interrupted
```

Je Schritt: (1) frische, begrenzte Beobachtung der Quellen im Umfang (UIA je
Fenster, Browserseite), (2) ein Modellaufruf mit grammatikerzwungenem JSON
(`response_format: json_schema`; Schema aus den verfügbaren Werkzeugen gebaut,
`decision ∈ {action, ask_user, done}`), (3) Prüfung außerhalb des Modells
(Werkzeug bekannt, im Umfang, Argumentschema, Elementreferenz aus der aktuellen
Beobachtung), (4) Freigabeprüfung, (5) Ausführung mit Zeitlimit und
Abbruchgeneration vor und nach dem Aufruf, (6) Ergebnis mit Belegen ins Journal;
`done` wird nur angenommen, wenn mindestens eine Aktion ausgeführt wurde;
`verified` gilt nur, wenn die letzte erfolgreiche Aktion selbst belegt ist
(Rücklesen, Dateiinhalt, Feldwert).

Budgets (Kern, nicht Modell): max. Schritte (Standard 20), Gesamtzeit (600 s),
Werkzeug-Timeouts, höchstens zwei Reparaturversuche bei ungültigem JSON, Stopp
bei dreimal gleicher gescheiterter Aktion, Stopp bei wiederholten Erfolgs-
behauptungen ohne Aktion oder wiederholten Rückfragen zu bereits erteilten
Freigaben. Schreibende UI-Aktionen laufen seriell.

## 5. Werkzeuge und Berechtigungen

Scopes kommen ausschließlich aus der Aufgabe (UI): `files.roots`/`write_dir`,
`browser.enabled`/`allowed_origins`, `windows.source_ids`/`allowed_processes`,
`memory`, `external_effects`. Das Modell sieht nur Werkzeuge, deren Scope
freigegeben ist.

| Werkzeug | Wirkung | Prüfung/Beleg |
|---|---|---|
| files.list/search/read | keine | kanonische Pfade (realpath, kein UNC/Gerätepfad), unter den Wurzeln |
| files.write | lokal | nur Schreibordner, nur Textformate, atomar, Rücklesen + SHA-256, Konfliktprüfung über `if_sha256` |
| memory.search | keine | bestehendes Retrieval mit Quellen |
| windows.inspect/read | keine | Fenster im Umfang, Prozess-Allowlist |
| windows.focus/invoke/set_value/type_text/send_keys/click_at/launch | lokal | Ziel neu validiert (hwnd+pid, sichtbar, Vordergrund für Eingaben), Element aus frischer Beobachtung, erlaubte Tasten, Klick nur im Fensterrechteck, Start nur für notepad/rechner/explorer **und** nur wenn in `allowed_processes` |
| vision.describe/locate | keine | nur mit Projektor; Grounding nur nach Kalibrierung „verified“ |
| browser.open/snapshot/click/fill/select/press/scroll/wait_for_text/read/back | lokal | eigener Chromium, eigenes Profil, Playwright-Auto-Wait, kein `force`, Refs nur bis zur nächsten Navigation, private/Loopback-Hosts gesperrt außer freigegebene Origins, `file:` nur aus freigegebenen Ordnern |

Freigaben: `side_effect=external` oder ein Ziel mit folgenreichem Namen
(senden/kaufen/löschen/veröffentlichen …) ohne `external_effects` → Aufgabe
wartet mit `pending_question` (approval). Der Namensfilter ist eine
konservative Heuristik, keine harte Garantie für beliebige GUI-Aktionen (siehe
KNOWN_LIMITATIONS). Rückfragen des Modells nach bereits erlaubtem Lesen/Auflisten
werden vom Kern deterministisch bejaht und protokolliert.

Abbruch: `POST …/cancel` oder globaler Stopp erhöht die Abbruchgeneration,
sperrt neue Aktionen sofort (ohne auf das Modell zu warten), bricht Runner und
Modellaufruf ab; eine gerade laufende Betriebssystemaktion wird als
`unknown_outcome` journaliert, nicht als rückgängig gemacht behauptet.

## 6. Persistenz und Portabilität

`agent_tasks`, `agent_actions` (Absicht vor dem Seiteneffekt, Ergebnis
danach), `agent_recipes` (Schema v3). Tasks sind host-gebunden (`host_id`);
nach einem Neustart werden laufende Tasks `interrupted`, laufende Aktionen
`unknown_outcome`. Rezepte entstehen nur aus erfolgreich belegten Läufen und
tragen Voraussetzungen, Schritte mit Prüfungen und bekannte Grenzen; sie
ändern keinen Code und erweitern keine Rechte. Details: `PORTABLE_AGENT_STATE.md`.

## 7. Modelle

Fähigkeiten je Modell: `text_chat`, `vision_input` (Projektor vorhanden),
`image_grounding` (none|untested|verified), `tool_call_format` (json_schema),
`native_tool_calls` (untested bis geprüft), `structured_output`, Backends,
Messwerte. Ein Textmodell arbeitet mit UIA/DOM; Bildfragen und `vision.*`
sind dann nicht verfügbar und werden so angezeigt. Siehe
`MODEL_COMPATIBILITY.md`.
