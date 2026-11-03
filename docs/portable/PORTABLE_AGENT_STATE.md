# Portabler Agentenzustand: Journal, Wiederaufnahme, Gerätebindung, Abläufe

Stand: 2026-11-04 (0.3). Ergänzt `ARCHITECTURE.md` (Zustandsmodell 0.1/0.2).

## Was auf dem Datenträger liegt

| Bereich | Ort | Inhalt |
|---|---|---|
| Wissen | `data/spaider.db` (Tabellen aus v1/v2) | Fakten, Entitäten, Korrekturen, Notizen, Dokumentkopien/-abschnitte, Vektoren |
| Aufgabenjournal | `agent_tasks`, `agent_actions` (v3) | Auftrag, Umfang, Zustand, jede vorgeschlagene/ausgeführte Aktion mit Ergebnis und Beleg |
| Ablaufrezepte | `agent_recipes` (v3) | versionierte, parametrisierte Schritte mit Prüfungen |
| Modelle + Projektoren | `data/models/<id>/` | Gewichte und `mmproj-*.gguf` als Einheit (Manifest `mmproj_rel_path`) |
| Browser | `data/browser/chromium-1248/` (Paket), `data/browser/profile/` (eigenes Profil), `data/browser/downloads/` | nur der Agentenbrowser; nie der persönliche Browser |
| Caches | `data/cache/` (u. a. `comtypes_gen`), Live-Frames **nur im RAM** | keine Bildschirmhistorie auf Platte |
| Einstellungen | `settings` | Hotkeys, Tray, Vorschau, Agentenbudgets |

## Migration

Schema v3 wird beim ersten Start vorwärts angewandt (`schema_migrations`,
`PRAGMA user_version = 3`). Getestet an einer konsistenten Kopie der
Nutzerdaten (siehe AGENT_LIVE_TEST_REPORT). Keine Änderung an v1/v2-Tabellen.

## Journal und Seiteneffekte

Vor jeder Aktion mit Wirkung wird die Absicht persistiert (`state=executing`,
`started_at`), danach das belegte Ergebnis (`done`/`failed`, `result_json` mit
`evidence`). Fällt der Prozess dazwischen aus, steht die Aktion nach dem
Neustart auf `unknown_outcome` und die Aufgabe auf `interrupted`; der Nutzer
sieht das im Aufgabenpanel. Genau-einmal-Ausführung wird für GUI-/Netzaktionen
nicht versprochen.

Belege sind werkzeugspezifisch: Dateien (SHA-256 + Rücklesen), Formularfelder
(zurückgelesener Wert), Fenster (UIA-Wert nach der Eingabe, Struktur danach),
Browser (Seitentitel/URL, erschienener Text). Ein Modelltext ist nie ein Beleg;
`verified` ist nur gesetzt, wenn die letzte erfolgreiche Aktion selbst belegt ist.

## Wiederaufnahme

- `interrupted` (gleicher Rechner): „Weiter“ startet die Aufgabe im Zustand
  `observing` neu – die Umgebung wird erst beobachtet, nichts wird blind
  wiederholt; die Aktionshistorie bleibt sichtbar und fließt in die Planung ein.
- `interrupted` auf einem anderen Rechner (`host_id` weicht ab): nicht
  fortsetzbar; die Aufgabe muss neu angelegt werden (Fenster-IDs,
  Pfade und Berechtigungen sind gerätebezogen).
- `paused` / `waiting_for_user`: Fortsetzen bzw. Antwort über die API; eine
  Antwort kann nicht verloren gehen (die Frage wird erst sichtbar, wenn der
  Kern bereits auf sie wartet).
- Live-Sitzungen werden nie automatisch wiederhergestellt; nach Neustart,
  Sperrung oder Quellenverlust wählt der Nutzer die Quelle erneut.

## Gerätebindung

Hardwareprofil, Monitore/Skalierung, Fensterhandles, Elementreferenzen,
Browserpfad und Freigaben werden nur zur Laufzeit aufgelöst. Fensterquellen
tragen `hwnd:pid`; vor jeder Aktion wird geprüft, dass das Handle noch zum
selben Prozess gehört und das Fenster sichtbar ist. Browseranmeldungen und
OS-gebundene Zugangsdaten sind nicht portabel; der Agentenbrowser nutzt ein
eigenes Profil ohne Cookie-/Passwortimport.

## Ablaufrezepte

Entstehen nur aus `completed` + `ok` + belegten Aktionen (`POST
/api/agent/tasks/{id}/save-recipe`). Felder: `id/version`, `purpose_de`,
`parameters`, `origin` (Task, Zeit), `requires` (Werkzeuge, Apps, Vision),
`preconditions`, `scope` (der damalige Umfang), `steps` (Werkzeug,
Argumentvorlage, Prüfung), `final_checks`, `last_verified_at`,
`known_limits_de`. Beim Start mit `recipe_id` sieht der Planer das Rezept als
Vorschlag; jeder Schritt durchläuft trotzdem Beobachtung, Validierung und
Freigabe. Rezepte ändern keinen Code und erweitern keine Rechte.

Importierte Rezepte, Dokumente, Webseiten und Modellantworten sind Daten;
Fakten für das Gedächtnis entstehen weiterhin nur über den bestehenden
transparenten Weg (Chat/Dokumente mit Herkunft), nicht aus Agentenplänen oder
Screenshot-Zusammenfassungen.
