# PR-Entwurf (noch nicht eröffnet)

**Titel:** `feat(foldable): portable local SpAIder chat (Windows, llama.cpp, SQLite memory)`

**Beschreibung:**

## Was

SpAIder Foldable: eine portable Betriebsart von SpAIder als eigenständige
Windows-App (Electron + Python-Kern + gebündeltes llama.cpp), die vollständig
von einem USB-Datenträger läuft. Chat mit lokalem offenem Modell (Qwen3-GGUF
aus dem Katalog oder eigene GGUF-Datei), Dokumentimport (TXT/MD/PDF),
dauerhaftes Gedächtnis über Gespräche hinweg (SpAIder-Pipeline auf SQLite),
Modellverwaltung mit verlustfreiem Wechsel. Keine Installation, keine
Cloud-Abhängigkeit, keine Docker-/Ollama-Pflicht.

## Warum

Persönliche, mitnehmbare SpAIder-Instanz: Modell, Gespräche, Quellen und
Erinnerungen reisen mit dem Datenträger und stehen auf jedem unterstützten PC
unverändert zur Verfügung. Zugleich Grundlage für spätere lokale Agenten
(Werkzeugverträge sind definiert, V1 führt nichts aus).

## Wie

- Neues Top-Level-Verzeichnis `foldable/` (Kern `foldable/core`, Launcher/UI
  `foldable/desktop`, Build `foldable/build`), Doku unter `docs/portable/`.
- Der Kern nutzt `backend/app` als Bibliothek: Parser, Schemas, Kompressor
  (Prompt + Parser + Ontologie), Entity-Resolver, Supersession-Judge,
  Kontextaufbau, litellm-Retry. Neo4j/Redis/Kafka/Postgres/ClickHouse werden
  durch lokale Adapter ersetzt (SQLite + FTS5 + numpy); Ollama/Cloud durch den
  gebündelten `llama-server` (b12032, gepinnt, SHA-256).
- Keine Änderung an bestehenden Modulen; `.gitignore` erweitert.

## Tests

- `foldable/core`: 33 Unit-Tests (Pfade/Lock/DB, GGUF, Katalog-Fit, Downloader
  mit Resume/Checksumme, Memory-Store, Resolver-Wiederverwendung, Supersession,
  Dokumente, Tool-Grants, API) plus ein echter End-to-End-Lauf mit
  Qwen3-0.6B (opt-in).
- `foldable/desktop`: vitest (SSE-Parser, API-Client, Markdown ohne Roh-HTML),
  `tsc`, Vite-Build, Packaging.
- Ergebnisse und Messwerte: `docs/portable/TEST_REPORT.md`.

## Nicht enthalten / bekannt

Siehe `docs/portable/KNOWN_LIMITATIONS.md` (u. a. nur Windows x64, CPU-Runtime,
keine Signatur, Qwen3.8 nur als Community-GGUF dokumentiert, nicht ausgeführt).

## Checkliste

- [ ] Review durch Cofounder (Architektur, Lizenz-/CLA-Fragen für AGPL-Core)
- [ ] CI-Jobs für `foldable/` ergänzen (siehe INTEGRATION_HANDOFF.md)
- [ ] Zwei-PC-Test mit echtem USB-Datenträger (siehe TEST_REPORT.md)

🤖 Generated with [Claude Code](https://claude.com/claude-code)

---

## Nachtrag: Update 0.2 (Performance, Live-Aktivität, offene Modelle)

**Titel:** `feat(foldable): live activity, chat-first scheduling, GPU backend, multi-family model catalog`

**Zusätzlich zum V1-Umfang:**
- Aktivitätszeile mit echten Ereignissen (Vertrag `docs/portable/ACTIVITY_EVENTS.md`),
  globaler Ereigniskanal, Timings je Nachricht.
- Planer: Chat verdrängt Hintergrund-Gedächtnisarbeit; sofort sichtbare Rohfakten;
  Prompt-Cache-stabile Nachrichtenfolge; Extraktionsbudget begrenzt.
- Hardwareprofil, automatische GPU-Nutzung (Vulkan) mit CPU-Rückfall, Leistungsmodi.
- Katalog v2 mit mehreren Familien, Suche/Auflösung über Hugging Face,
  Empfehlungen für den jeweiligen Rechner, Kalibrierung.
- Messwerkzeug `foldable/build/measure.py`, Berichte `PERFORMANCE_REPORT.md`,
  `MODEL_COMPATIBILITY.md`, `MODEL_RECOMMENDATIONS.md`.

---

## Nachtrag: Update 0.3 (lokaler Agent, Live-Bildschirmhilfe)

**Titel:** `feat(foldable): live screen help (hotkey overlay), scoped local agent with file/browser/windows tools, vision models`

**Zusätzlich zum 0.2-Umfang:**
- Live-Sitzungen (Quelle = Fenster/Bildschirm, UIA-Struktur + Bild, keine Dauerschleife, Overlay
  per Hotkey, geteiltes Gespräch).
- Agent: persistente Zustandsmaschine, Broker mit Scopes/Validierung/Journal/Abbruch,
  Werkzeuge für Dateien, Gedächtnis, Windows (UIA/SendInput), Bild (Beschreiben, Grounding mit
  Kalibrierung) und eigenen Browser (Playwright + gepinnter Chromium-Download), Rezepte.
- Modelle: Projektor-Paarung, Fähigkeiten text/vision/grounding/tools getrennt; Qwen3-VL-4B
  als Standard für Bildfragen.
- Berichte: `AGENT_LIVE_ARCHITECTURE.md`, `AGENT_LIVE_PERFORMANCE.md`, `AGENT_LIVE_TEST_REPORT.md`,
  `PORTABLE_AGENT_STATE.md`.
- Aus der 20er-Aufgabenmatrix (Qwen3-VL-4B ×3, Qwen3-4B ×2, Gemma 4 E2B ×2) behoben: Scope-Verlust
  nach `windows.launch`, Browserseite der Voraufgabe als Beobachtung, Vordergrund-Sperre beim
  Aktivieren von Zielfenstern, Erfolgsmeldung ohne Lesen; Rezepte werden jetzt tatsächlich
  deterministisch wiedergegeben (Rückfall auf den Planer). Ergebnisse je Aufgabe und Modell im
  Prüfbericht; „alles grün“ gibt es nicht – Windows-/Bildaufgaben bleiben mit 4B-Modellen unsicher.

