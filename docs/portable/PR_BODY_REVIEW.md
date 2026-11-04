<!-- Draft-PR-Text für die Review-Runde. Verwendung: gh pr create --draft --body-file docs/portable/PR_BODY_REVIEW.md -->

## Summary

Foldable ergänzt SpAIder um eine portable Windows-Anwendung. Nutzer starten die App aus einem
Ordner, laden ein unterstütztes lokales Modell und arbeiten mit Gesprächen, Dokumenten und
gespeichertem Kontext weiter. Hinzu kommen eine Live-Bildschirmhilfe (globaler Hotkey, Frage zum
freigegebenen Fenster mit UI-Struktur und Fensterbild) und ein Agentenmodus für freigegebene
Aufgaben in Ordnern, im eigenen Browser und in Windows-Fenstern. Dieser Draft dient der
gemeinsamen Prüfung von Architektur, Bedienung und Zuverlässigkeit vor einer Übernahme.

**Draft zur gemeinsamen Prüfung; kein Merge-Auftrag.**

Die Begutachtung findet zunächst im privaten Entwicklungsrepository statt. Der Basisbranch
`review/spaider-base-73762b3` entspricht dem dokumentierten Stand des gemeinsamen
SpAIder-Repositorys (`Spaider-studio/spaider`, `main` bei `73762b3`, seit der Foldable-Basis
unverändert). Die Übernahme dorthin folgt separat nach Review.

- Einstieg und Testanleitung: `docs/portable/REVIEW_START_HERE_DE.md`
- Vergleich: `feature/spaider-foldable-review` (Head) gegen `review/spaider-base-73762b3` (Base)
- Herkunft: die 66 Produktcommits `73762b3..f281d78` der privaten Entwicklung (0.1 `acc3ce1`,
  0.2 `debc9be`, 0.3 `f281d78`), unverändert übernommen; dazu Review-Dokumente und das
  Build-Manifest. Keine Sandbox-Commits, keine Änderungen an `.github/`, `backend/`, `frontend/`,
  `cli/`, `sdk/`.

Closes # (kein Issue; Review-Draft)

## Was sich ändert (nur Ergänzungen)

| Bereich | Inhalt |
|---|---|
| `foldable/core/` | Python-Kern `spaider_foldable`: FastAPI auf Loopback mit Token, SQLite-Gedächtnis (FTS5 + Vektoren, Migrationen bis Schema 3), Modellkatalog/-registry/Downloader, `llama-server`-Verwaltung (CPU/Vulkan, Rückfall), Chat-Service, Beobachtung (Win32/UIA/Aufnahme), Live-Sitzungen, Agent (Zustandsmaschine, Broker, Werkzeuge, Rezepte); Tests |
| `foldable/desktop/` | Electron-Launcher + React-Oberfläche: Prozessstart, Token, Overlay-Fenster, globale Hotkeys, Tray, Aufgabenpanel, Einstellungen; Mock-Kern für UI-Tests; vitest |
| `foldable/build/` | Pinning der llama.cpp-Runtime (b12032, SHA-256), PyInstaller-Spec, Paket-Assemblierung, Paketverifikation, Messdaten (synthetisch) |
| `docs/portable/` | Architektur, API-Vertrag, Prüf- und Performanceberichte, Modellkompatibilität, bekannte Grenzen, Quickstart, Handoff, Worklog, Review-Einstieg |
| `.gitignore` | Build-Ausgaben, Runtime, Dev-Daten, `*.gguf` |

`backend/app` wird als Bibliothek wiederverwendet (Parser, Schemas, Kompressor, Entity-Resolver,
Supersession, Query-Fusion, litellm-Retry, Settings); der Server-Code bleibt unverändert. Zwei
Adapter-Eingriffe ohne Upstream-Änderung sind in `ARCHITECTURE.md` §2 dokumentiert (Vorschläge:
Kompressor-Cache injizierbar machen, `_attach_fact_node` in ein Service-Modul verschieben).

## Testpaket

- Review-Build: `SpAIder-Foldable-0.3.0-review-cd50a04-win-x64.zip`, 395 922 585 Bytes,
  SHA-256 `20b4096516796956b76b4b2503b29a258bbaf71de46ade6c2a04e1f28cd6b5ff`, gebaut aus
  `cd50a042046acee034e74383087851b58acd04f0` (Kern-Code `371a503`, Launcher `a85dca6`, llama.cpp
  b12032). Fundort: Prerelease `foldable-review-0.3.0-cd50a04` im privaten Repository
  (Zugriff nur für berechtigte Nutzer; die von GitHub erzeugte „Source code (zip)“ ist nicht die
  App). Manifest: `foldable/build/measurements/review-build-cd50a04.md`.
- Benötigt: Windows 11 x64, kurzer Entpackpfad, beim ersten Start Internet für den Modell-Download.

## Pre-PR checklist

- [x] Basis = `upstream/main` (`73762b3`), Historie linear, keine Konflikte
- [ ] Issue claim – entfällt (Review-Draft ohne Issue)
- [x] Kein anderer offener PR berührt dieselben Dateien (`foldable/`, `docs/portable/` sind neu)
- [ ] `make lint test` – nicht anwendbar: der Diff berührt `backend/`, `frontend/`, `cli/`, `sdk/` nicht; stattdessen Foldable-Prüfungen (unten)
- [ ] `docker compose up` – nicht anwendbar (Foldable läuft ohne Compose-Stack)
- [x] Keine doppelten Request/Response-Klassen in `backend/app` (unverändert)
- [x] Alle neuen `async`-Helfer werden an jeder Aufrufstelle `await`ed (Kern-Tests, Lint)
- [ ] Konnektor – nicht anwendbar
- [x] Tests für Happy Path und Fehlerpfade (Scope-Ausbrüche, ungültige Argumente, Abbruch, Konflikte, leere Rückfragen)

## Test plan

**In dieser Review-Runde ausgeführt (Commit f281d78-Code im Worktree des Review-Branches, Windows 11 26200, Intel Core Ultra 7 268V, 32 GB, Intel Arc 140V/Vulkan):**

- [x] Kern: `pytest` – 73 bestanden, 6 übersprungen (Echtlauf-Tests ohne Umgebungsvariable)
- [x] Desktop: `npm run typecheck`, `npm run lint`, `npm test` – 88 Tests bestanden
- [x] Paketbau aus `cd50a04` (PyInstaller-Kern, Electron-Launcher, ZIP) und `verify_package.py`: Layout ok, Kern 10,5 s bereit in einem Pfad mit Umlaut/Leerzeichen, Token-Pflicht, Exit 0, Launcher-Screenshot
- [x] Frisch entpackter Review-Build mit synthetischen Daten über die Kern-API (`review-package-check-cd50a04.json`): keine Modelle/Daten im ZIP; Start mit PATH nur aus Windows-Ordnern; Modell aus lokalem Bestand auf Vulkan; Chat erstes Token 0.64 s (GPU geteilt mit der Matrix); „Merke dir … Max Weber“ → „Korrektur … Priya Nair“ → neues Gespräch antwortet „Priya Nair“ → Kern-Neustart → erneut „Priya Nair“; Live-Bildschirmsitzung mit Snapshot, Strukturfrage (0.56 s) und Bildfrage (9.55 s); Datei-Aufgabe `completed`/belegt (3 Modellaufrufe, 45.2 s); Abbruch → `cancelled`, keine Aktion/Datei; Beenden Exit 0 ohne Restprozesse; 65 Netstat-Stichproben ohne externe Verbindung
- [ ] Live-Hotkey/Quellenauswahl/Stopp über die echte Oberfläche des Review-Builds – *(wird eingetragen)*
- [x] Aufgabenmatrix 20 × 3 zusammenhängend gegen den Review-Code (Qwen3-VL-4B Q4_K_M + Projektor, Vulkan, Intel Arc 140V): **55/60** (Dateien 12/15, Browser 15/15, Windows/UIA 14/15, Bild 14/15), 4 Fehlerfolge-Meldungen (F4 ×3 Dateiname fehlt, V3 ×1), 0 Rückfragen, Median 30 s je Aufgabe; Rohdaten `foldable/build/measurements/matrix-qwen3-vl-4b-review-full.json`

**Früher berichtet (0.3-Entwicklung, `AGENT_LIVE_TEST_REPORT.md`, nicht erneut gelaufen):**
Overlay-Latenzen gegen den Dev-Kern (Hotkey → sichtbar 47,8 ms Median, 30 Zyklen ohne Leck),
Matrix-Teilläufe verschiedener Code-Stände (zusammengesetzt 53/60; jetzt durch den zusammenhängenden 55/60-Lauf oben ersetzt), Qwen3-4B 22/30, Gemma 4 E2B
13/16, Rezept-A/B (0 Modellaufrufe, 0,4 s vs. 71 s), Paket-Volltest des 0.3.0-Pakets (Chat-TTFT
0,25 s, CPU-Rückfall, 22 Netstat-Stichproben ohne externe Verbindung), Migration einer
0.1-Datenkopie mit dem gepackten Kern.

**Nicht ausgeführt / offen:** Zwei-PC- oder USB-SSD-Umzug, andere GPUs, echte Netztrennung,
erzwungener Absturz während einer Aktion, gesperrter Desktop, Overlay-Messung gegen das gepackte
Paket, Migration echter 0.2-Daten, native Tool-Calls, Gemma-Grounding.

## Screenshots / recording

Screenshots des Overlays und der Aufgabenansicht aus den Testläufen liegen nicht im Repository
(synthetische Inhalte, aber Entwicklungsrechner-Pfade); bei Bedarf nachreichen.

## Notes for the reviewer

Review-Fragen:
1. Soll Foldable als eigener Paketbaum `foldable/` im Monorepo bleiben (jetziger Stand) oder in ein eigenes Repository mit `backend/app` als Abhängigkeit?
2. Wiederverwendung: Sind die beiden Adapter-Eingriffe (Kompressor-Cache, `_attach_fact_node`) als Upstream-Änderung gewünscht?
3. Produktentscheidung: Reicht die Namensfilter-/Wirkungsklassen-Erkennung folgenreicher Aktionen für eine Vorschau, oder soll jede Browser-/Windows-Aktion anfangs bestätigt werden?
4. Modellpolitik: Qwen3-VL-4B als Standard für Bild/Agent, Textmodelle für Chat – oder nur ein Modell empfehlen?
5. Soll der Rezeptmechanismus (Wiedergabe aufgezeichneter Schritte) in die Vorschau, oder zunächst deaktiviert bleiben?

Integrationsschritte nach Feedback: Review-Feedback einarbeiten → Branch gegen `Spaider-studio/spaider` `main` als Draft öffnen (Befehle in `INTEGRATION_HANDOFF.md`) → CI/CLA prüfen → gemeinsame Merge-Entscheidung. Kein Auto-Merge.
