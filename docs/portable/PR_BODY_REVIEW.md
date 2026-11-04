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

- Review-Build: **wird nach dem Build eingetragen** (Dateiname `SpAIder-Foldable-0.3.0-review-<sha>-win-x64.zip`,
  Größe, SHA-256, Quell-Commit; Prerelease `foldable-review-0.3.0-<sha>` im privaten Repository,
  Zugriff nur für berechtigte Nutzer; die von GitHub erzeugte „Source code (zip)“ ist nicht die App).
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
- [ ] Paketbau aus dem Review-Commit und `verify_package.py` – *(wird eingetragen)*
- [ ] Frisch entpackter Review-Build mit synthetischen Daten (Start ohne Entwicklungs-PATH, Chat, Gedächtniskorrektur + Neustart, Live-Hotkey, Datei-Aufgabe, Stopp/Beenden) – *(wird eingetragen)*
- [ ] Aufgabenmatrix 20 × 3 zusammenhängend gegen den Review-Code (Qwen3-VL-4B, Vulkan) – *(läuft, Ergebnis wird eingetragen)*

**Früher berichtet (0.3-Entwicklung, `AGENT_LIVE_TEST_REPORT.md`, nicht erneut gelaufen):**
Overlay-Latenzen gegen den Dev-Kern (Hotkey → sichtbar 47,8 ms Median, 30 Zyklen ohne Leck),
Matrix-Teilläufe verschiedener Code-Stände (zusammengesetzt 53/60), Qwen3-4B 22/30, Gemma 4 E2B
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
