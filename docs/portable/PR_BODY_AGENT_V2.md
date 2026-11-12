## SpAIder Foldable 0.5 – Agent v2: Ausführungskern mit geprüfter Wiederverwendung (Review, Entwurf)

**Basis dieses PRs:** `feature/spaider-foldable-0.4-review` (PR #60, nicht gemerged). Dieser Branch setzt darauf auf; bitte zuerst #60 lesen. Kein Merge, kein Auto-Merge; Draft bis zum Review. Alle Änderungen liegen unter `foldable/` und `docs/portable/`.

### Problem

0.3/0.4 konnten Aufgaben ausführen und Rezepte wörtlich abspielen, aber: „fertig“ kam vom Modell, Rezepte wussten nichts über ihre Grundlagen (welche Datei, welcher Gedächtnisfakt, welcher Rechner) und scheiterten bei jeder Änderung komplett; Wiederholungen kosteten jedes Mal die volle Modellarbeit; Nutzereingaben während einer Fensteraktion konkurrierten mit dem Agenten.

### Änderungen

- **Fünf Komponenten** (`agent/deps.py`, `compiler.py`, `reuse.py`, `bindings.py`, `verifier.py`): Abhängigkeiten mit Revision/Herkunft/Gültigkeit im Profil-SQLite (Schema v6, keine Graph-DB), Compiler Journal → deklarativer Ablauf (Rollen, Lese-/Schreibmengen, Vor-/Nachbedingungen, abgeleitete Schritte, beobachtete vs. vermutete Faktenbeziehungen), Gate wiederverwenden → reparieren → neu planen, host-gebundene Bindungen, Ergebnisprüfung des aktuellen Zustands vor jedem „fertig“.
- **Ein Ausführungskern** (`agent/planner.py`, `loop.py`): Grammatik-JSON (lokal) und native Tool-Calls (Verbindungen) auf demselben Pfad, vollständige Aufrufe mit Aufruf-ID, Runde wird fortgesetzt; Fähigkeiten getrennt geprüft (`POST /agent/capabilities/probe`).
- **Kontrolle** (`agent/inputguard.py`): eine Eingabesperre je Desktop, Nutzereingabe/Fokuswechsel pausiert, Stopp löst gehaltene Tasten und beendet Aufnahmen, Epoche gegen späte Aktionen, Sperrbildschirm/Datenträgerverlust, Journal-Wächter gegen doppelte Nebenwirkungen, Fortsetzen nach unklarem externem Ergebnis warnt.
- **Lernen** (`agent/workflows.py`, `teach.py`): Kandidat → geprüft (zwei Parametersätze + Negativprüfung) → zurückgestuft; Beibringen per Demonstration (Browser-Recorder / Fenster-Diff, keine globale Aufzeichnung); 0.3-Rezepte werden migriert.
- **Oberfläche** (Desktop): Aufgabe erledigen mit tatsächlichen Schritten, Ergebnisprüfung und anklickbaren Belegen; Abläufe (Status, Abhängigkeiten, Bindungen, Prüfen, Negativprüfung, Starten mit Parametern, Vergleichslauf); Ablauf beibringen; Bindungsrückfragen mit Ordner-/Fensterwahl.
- **Messharness** `tests/matrix/run_matrix_v2.py` + `tasks_v2.json` (60 Aufgaben, Lern-/Testfälle, Faktenänderung, App-Variante, drei Arme).
- **Nach der Messreihe behoben** (Commit „repair: read-only and derived steps …“): rein lesende Schritte mit ungültiger Abhängigkeit werden erneut gelesen (kein Modellaufruf), abgeleitete neu abgeleitet; vom Ablauf selbst geschriebene Dateien sind keine Eingaben. Gefunden durch die Produktdemo.

### Paket

Review-Prerelease `foldable-0.5.0-review` (https://github.com/Spaider-studio/spaider/releases/tag/foldable-0.5.0-review): `SpAIder-Foldable-0.5.0-win-x64.zip`,402 220 402 Bytes, SHA-256 `7bb5b8e86d2d316420e3f999207c604a226e680abf8b050a05ff0f3799187fc9`, Build-Commit `e11400e` (`BUILD_INFO.txt` im ZIP; `BUILD_MANIFEST_0.5.0.json` und `SHA256SUMS-0.5.0.txt` als Assets und unter `foldable/build/measurements/`). Ohne Modellgewichte, Nutzerdaten, Browserprofile, Schlüssel (8 935 Einträge geprüft). Frisch entpackt mit PATH ohne Entwicklerwerkzeuge geprüft (`verify_package.py` + Paketprüfung 37/37). Die Assets von `foldable-0.4.0-review` bleiben unverändert.

### Testanleitung (20 Minuten)

`docs/portable/AGENT_V2_QUICKSTART_DE.md` – fünf Schritte: Aufgabe erledigen → als Ablauf speichern → mit neuen Daten wiederholen → Projektleitung ändern (Reparatur nur der Empfängerzuordnung) → Ablauf beibringen / Stopp.

### Echte Resultate (Qwen3-4B Q4_K_M, Vulkan, ein Rechner; Details `docs/portable/AGENT_V2_TEST_REPORT.md`)

- Datei + Browser, 35 Aufgaben × 3, drei Arme: Wiederholungen mit vollem Mechanismus **37/42 (88 %) bei 2,0 Modellaufrufen und 19 s** gegenüber **45/50 (90 %) bei 5,3 Aufrufen und 40 s** ohne Wiederverwendung → **−62 % Aufrufe, −53 % Dauer**, aber 2 Punkte weniger Erfolg und **5 falsche Wiederverwendungen** (Browser-Feldwerte, Modell). Einfache Rezepte (0.3-Semantik): 24/44 mit 18 falschen Wiederverwendungen. Dateigruppe allein: 24/24 sauber.
- Faktenänderung: X6 repariert nur den Mailschritt (2 statt 4 Aufrufe, neuer Empfänger). Die Produktdemo X10 scheiterte auf dem Messstand an der Reparatur eines `memory.search`-Schritts → behoben; Nachlauf auf dem behobenen Stand: Wiederholung 1 korrekt mit **4 statt 11 Aufrufen** (Gedächtnis ohne Modellaufruf neu gelesen, nur Mailentwurf neu abgeleitet), Wiederholung 2 eine falsche Wiederverwendung (Modell ließ die neue E-Mail weg; die Prüfung kennt Faktenwerte nicht) – Testbericht Abschnitt 5.
- Fensteraufgaben: nur Teildaten (Sitzung war nachts gesperrt): UIA-Aufgaben W2/W3/W5/W7 wiederverwendet (3→1, 7→0, 5→4→1, 3→1 Aufrufe), Vordergrund-Aufgaben nicht messbar.
- Skriptiert: Kernsuite 159 bestanden / 8 übersprungen (Echtläufe), Desktop 123 Tests; Pflichttests (Stopp mit ausstehendem Tool-Call, Stopp-Sperre p95 1,2 ms, veralteter Zustand, Profilwechsel, Prompt-Injection, App-Ausfall, Neustart, Memory-Korrektur, Nur-lokal, Migration, Nutzereingabe, falsche Fertigmeldung, doppelte Nebenwirkung) grün.
- Paket frisch entpackt ohne Entwickler-PATH geprüft (37/37), Migration auf Kopie der 0.3-Daten geprüft.

### Offene Grenzen (ehrlich)

- Die Hypothese „weniger Modellarbeit bei gleicher Qualität“ ist nur für die synthetischen Aufgaben mit einem lokalen 4B-Modell gemessen; „gleiche Qualität“ ist knapp verfehlt (falsche Wiederverwendung bei abgeleiteten Browser-Feldwerten). Keine Behauptung einer Überlegenheit.
- Nicht gemessen: Fensteraufgaben mit Vordergrund (gesperrte Sitzung), Fenster/Bild-Baseline nur hinter dem Sperrbildschirm, echte Cloud-Anbieter (0 €; native Tool-Calls gegen Fake-Adapter), physischer Zwei-PC-Transfer, Offline-/CPU-Echtläufe auf 0.5 (zuletzt auf 0.4 grün).
- Reparatur: nach einem gescheiterten Reparaturlauf wird der Ablauf zurückgestuft (bewusst); lesende Schritte mit Elementziel gehen weiter an den Planer. Sperr-Erkennung nur über das Vordergrundfenster (WTS-Flags offen).
- Befund für #60: `tests/matrix/run_matrix.py` enthält dort ein Backspace-Byte im Standardpfad (durch die Pfad-Neutralisierung); hier repariert.

### Review-Reihenfolge

1. `docs/portable/AGENT_V2_ARCHITECTURE.md` (Kern, Entscheidungsweg, Abgrenzung zu Agent S4 / ScienceCUA / DGRR / GRADE, offene Hypothesen)
2. `foldable/core/spaider_foldable/agent/` – `contracts.py`, `deps.py`, `compiler.py`, `reuse.py`, `verifier.py`, `loop.py`
3. `foldable/core/tests/test_agent_v2.py` (Pflichttests), `docs/portable/AGENT_V2_TEST_REPORT.md`, `KNOWN_LIMITATIONS.md` Abschnitt 0.5
4. Desktop: `foldable/desktop/src/renderer/components/tasks/`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
