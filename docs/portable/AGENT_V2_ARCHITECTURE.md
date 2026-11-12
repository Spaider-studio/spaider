# SpAIder Foldable 0.5: Ausführungskern (Agent v2) – Architektur

Stand: 2026-11-12. Branch `feature/spaider-foldable-agent-v2`. Ergänzt
`AGENT_LIVE_ARCHITECTURE.md` (0.3) und `MODELHUB_ARCHITECTURE.md` (0.4); beide bleiben
gültig. Dieses Dokument beschreibt, **was im Code vorhanden ist**, nicht, was geplant ist.
Was nur teilweise oder gar nicht umgesetzt ist, steht in Abschnitt 9 und in
`KNOWN_LIMITATIONS.md`.

## 1. Ziel und Forschungshypothese

SpAIder soll Computeraufgaben ausführen, deren Ergebnis selbst überprüfen und bewährte
Abläufe mit dem persönlichen Gedächtnis zwischen PCs mitnehmen.

**Hypothese (zu prüfen, nicht zugesichert):** Persönliche Abläufe lassen sich trotz
Faktenänderungen und Rechnerwechsel mit weniger erneuter Modellarbeit korrekt ausführen,
wenn Faktenrevisionen, Aufgaben, Schritte, lokale Bindungen und Ergebnisbelege durch
**explizite Abhängigkeiten** verbunden sind und ein Gate nur dann wiederverwendet, wenn
diese Abhängigkeiten nachweislich gültig sind. Die Messungen dazu stehen in
`AGENT_V2_TEST_REPORT.md`; sie zeigen Ersparnisse bei Datei- und Browseraufgaben und
Grenzen bei Fensteraufgaben. Ein Graph allein beweist nichts; entscheidend ist, dass
jede Wiederverwendung an eine aktuelle Zustandsprüfung gebunden ist.

## 2. Komponenten (alle in `foldable/core/spaider_foldable/agent/`)

```
TaskSpec ──► AgentManager (loop.py)  ◄── Planner (planner.py): Grammatik-JSON | native Tool-Calls
                │
                ├─ ReuseGate (reuse.py)          Entscheidung: wiederverwenden → reparieren → neu planen
                ├─ BindingResolver (bindings.py) Rollen → Ziele auf diesem Rechner (host_id)
                ├─ DependencyStore (deps.py)     Fakten/Dateien/Bindungen/Apps/Werkzeugverträge mit Revision
                ├─ WorkflowCompiler (compiler.py) Journal → VerifiedWorkflow (deklarativ, parametrisiert)
                ├─ OutcomeVerifier (verifier.py)  Erfüllt / nicht erfüllt / ungeklärt – jetzt, nicht damals
                ├─ Broker (broker.py)            Schema, Umfang, Frische, Freigabe, Abbruchgeneration
                ├─ InputGuard (inputguard.py)    eine Eingabesperre je Desktop, Nutzereingabe → Pause
                └─ TeachManager (teach.py)       Demonstration → Kandidat
Persistenz: agent_workflows, agent_dependencies, agent_bindings, agent_workflow_runs,
            agent_tasks(+outcome, success_json, reuse_json), agent_actions(+call_id, origin)  – Schema v6
```

### 2.1 DependencyStore

Tabelle `agent_dependencies` im Profil-SQLite (keine neue Graph-Datenbank). Eine Zeile =
(Ablauf, optional Schritt, `kind`, `key`, `revision`, `origin`, `scope`, `valid`, Grund).

| kind | key | revision | gültig, wenn |
|---|---|---|---|
| fact | `nodes.id` eines FACT-Knotens | `updated_at` + superseded-Flag | Knoten existiert, nicht ersetzt/vergessen, Revision unverändert |
| file | `{rolle}/relativer/pfad` | SHA-256 | Datei existiert; geänderte Eingabe = „geändert“ (kein Reparaturgrund, aber kein Überspringen) |
| binding | Rollenname | `host_id|ziel` | Rolle auf diesem Rechner gebunden |
| app | Prozessname | – | im Umfang der Aufgabe startbar/freigegeben |
| tool_contract | Werkzeugname | Hash des Argumentschemas | Werkzeug vorhanden, Schema unverändert |
| workflow | Ablauf-ID | – | abhängiger Ablauf ist Kandidat oder geprüft |

`origin` unterscheidet **observed** (memory.search hat den Fakt geliefert, Datei wurde
gelesen), **declared** (vom Compiler/Nutzer deklariert) und **inferred** (ein
Parameterwert entspricht einem gespeicherten Fakt – vermutete Beziehung). Vermutete
Beziehungen zählen nie als Nachweis: sie senken die Abdeckung und schalten das Gate auf
„streng“.

Invalidierung (`invalidate_facts`) läuft transitiv über `workflow`-Abhängigkeiten
(Tiefe ≤ 6), löscht nichts und trägt `invalidated_by`/`reason` ein. Auslöser:
`LocalMemoryStore.mark_superseded`, `apply_explicit_correction` (Korrekturschicht im
Chat), Connector-Werkzeuge `correct_memory`/`forget_memory`. Der globale Kanal meldet
`event: workflows {stale, facts, by}`.

### 2.2 WorkflowCompiler

Eingabe: eine **erfolgreich belegte** Aufgabe (Journal, Werkzeugdaten, Elementnamen,
Fensterhinweise), Parameter (Name → Wert), Erfolgskriterien. Ausgabe:
`VerifiedWorkflow` (siehe `contracts.py`):

* **Rollen statt Pfade:** jede freigegebene Wurzel wird `root_<ordnername>`, der
  Schreibordner `output_dir`, Fenster `window_<prozess>` (Hinweis: Prozess|Titel),
  gestartete Programme `app_<name>`. Argumente werden zu Vorlagen mit `{rolle}` und
  `{parameter}`.
* **Lese-/Schreibmengen** je Schritt (`file:{rolle}/…`, `page`, `window:{rolle}`,
  `memory`, `fact:<id>`), **Vorbedingungen** (`exists:…`, `tool:…`, `binding:…`) und
  **Nachbedingungen** (Datei existiert/enthält Parameterwerte, Feld trägt Wert, Text
  erschien) – aus den Belegen des Originallaufs.
* **Abgeleitete Schritte** (`derive`): Argumente, deren Inhaltswörter aus zuvor gelesenen
  Daten stammen (Bericht aus Dateiinhalten), werden markiert. Sie werden bei jeder
  Wiederverwendung mit **einem** begrenzten Modellaufruf neu abgeleitet (Vorlage als
  Beispiel, aktuelle Daten als Eingabe, Zielort fest). Alles andere läuft ohne Modell.
* **UI-Ziele** (`target {role, name}`) ersetzen Snapshot-Referenzen (b3, e12); sie werden
  bei jedem Lauf gegen eine frische Beobachtung aufgelöst, nie blind wiederverwendet.
* Es entsteht **kein ausführbarer Code**: nur Werkzeugnamen, Argumentvorlagen und
  Prüfungen, die alle wieder durch Broker (Schema, Umfang, Freigabe) laufen.

### 2.3 ReuseGate

Eingabe: Ablauf, Parameter, aktuell geprüfte Abhängigkeiten, gebundene/fehlende Rollen,
verfügbare Werkzeuge. Reihenfolge der Regeln:

1. `demoted`/`retired` → **replan** (nie automatisch).
2. fehlende Pflichtparameter, fehlende Werkzeuge, geänderter Werkzeugvertrag → **replan**.
3. ungültige Fakten → betroffene Schritte (gleiche `depends_on`, `read_set`,
   `derive_from`) in die Reparaturmenge; bei vermuteten Beziehungen alle abgeleiteten
   Schritte. Fehlende Eingabedateien → lesende Schritte reparieren. Fehlende Programme →
   Startschritt reparieren. Dateien, die ein früherer Schritt desselben Ablaufs schreibt,
   sind Zwischenergebnisse: keine Abhängigkeit, kein Reparaturgrund (Compiler und Gate).
4. Reparaturmenge enthält eine Wirkung nach außen oder mehr als die Hälfte der Schritte
   → **replan**. Sonst → **repair**: ein abgeleiteter Schritt wird aus den aktuellen Daten
   neu abgeleitet (ein Modellaufruf), ein rein lesender Schritt (`memory.search`,
   `files.read`, `windows.read` ohne Elementziel …) wird wörtlich erneut gelesen (kein
   Aufruf); nur Schreibschritte mit festem Inhalt und UI-Aktionen gehen an den Planer, mit
   Reparaturbudget (4 Planeraktionen je Schritt). Schlägt auch das fehl, übernimmt der
   Planer den Rest (Rückfall), und ein gescheiterter Reparaturlauf stuft den Ablauf zurück.
5. sonst → **reuse**. `strict` (jede Nachbedingung prüfen, UI-Schritte neu beobachten)
   gilt für Kandidaten, bei Abdeckung < 0,8, bei unbekannten/vermuteten Abhängigkeiten
   und bei jeder ungültigen Abhängigkeit.

Die Negativprüfung (`negative_check`) macht alle Faktenabhängigkeiten ungültig und
entfernt alle Bindungen; das Gate muss dann reuse verweigern. Ohne bestandene
Negativprüfung wird kein Kandidat „geprüft“.

### 2.4 BindingResolver

Rollen werden nur aus dem **Umfang der aktuellen Aufgabe** auf diesem Rechner gebunden:
Schreibordner → `output_dir`; Wurzeln nach Ordnernamen; genau eine Wurzel für genau eine
Eingaberolle; gespeicherte Bindungen (`agent_bindings`, `host_id`-gebunden) nur, wenn das
Ziel existiert **und** innerhalb der freigegebenen Ordner liegt; Fenster nach Prozess/
Titelhinweis innerhalb der freigegebenen Fenster; Programme nach Prozess-Allowlist.
Fehlt eine Pflichtrolle, fragt die Aufgabe (`pending_question.binding`); die Antwort
wird gespeichert und bei jedem Start erneut geprüft. Koordinaten, Fensterhandles,
Sitzungen und Rechte werden nie übernommen; Freigaben prüft weiterhin der Broker.

### 2.5 OutcomeVerifier

„Fertig“ braucht Belege des **aktuellen** Zustands. Kriterien kommen aus dem Ablauf
(`checks`, Nachbedingungen), aus ausdrücklichen Nutzerkriterien oder konservativ aus
dem Auftrag (im Auftrag genannte Ausgabedateien → existiert & nicht leer; „prüfe, dass
„…“ erscheint“ → Seiten-/Fenstertext). Ohne ableitbare Kriterien gilt
`action_evidence`: die letzte erfolgreiche Aktion muss selbst belegt sein (Rücklesen,
Feldwert). Status: `fulfilled` | `not_fulfilled` | `unclear`. Ein `done` des Modells wird
bei `not_fulfilled`/`unclear` einmal mit den Ursachen zurückgegeben; danach endet die
Aufgabe mit dem geprüften Ergebnis (`result.ok` nur bei nicht widerlegtem Erfolg; eine
`unclear`-Meldung ist sichtbar „ungeprüft“). Gibt das Modell auf, obwohl reale
Zustandsprüfungen erfüllt sind, gilt die Aufgabe als erreicht.

## 3. Verträge (`contracts.py`)

`TaskSpec` (Ziel, Profil/Projekt, Ressourcen = Scopes, Freigaben, Budget,
Erfolgskriterien, Parameter, Ablauf, Verbindung), `Observation` (Rechner/Sitzung,
Quelle, Zeitpunkt, Zustandsversion = Hash der Struktur, Element-IDs/Namen, Bildreferenz),
`ActionProposal` (Aufruf-ID, Werkzeug, Argumente, Ziel, erwarteter Zustand, benötigte
Rechte, Herkunft planner | workflow | repair | teach), `ActionReceipt` (tatsächliche
Aktion, Belege, Fehler, Nebenwirkungsklasse, Dauer, Artefakte), `VerifiedWorkflow`
(Parameter, Voraussetzungen, Bindungen, Schritte, Prüfungen, Abhängigkeiten, Version).
Die 0.3-Wire-Modelle `Task`/`Action`/`Recipe` bleiben die API-Form und wurden um
`outcome`, `reuse`, `call_id`, `origin` ergänzt (API_CONTRACT Addendum v1.5).

## 4. Ein Ausführungskern für alle Modelle (`planner.py`)

* **Lokale Laufzeit:** grammatikerzwungenes JSON (`response_format: json_schema`) wie
  0.3; das JSON wird erst nach Ende des Streams geparst – teilweise gestreamtes JSON
  wird nie ausgeführt.
* **Anbieterverbindung mit nativen Tool-Calls:** jedes Broker-Werkzeug plus `ask_user`
  und `done` werden als Funktionen angeboten (Namen `files__read`, da Punkte nicht
  erlaubt sind); die Adapter liefern `tool_call`-Ereignisse erst, wenn der Aufruf
  vollständig ist. Ergebnisse gehen unter derselben `call_id` als `tool`-Nachricht
  zurück, die Modellrunde wird also tatsächlich fortgesetzt (Test
  `test_native_tool_calls_share_the_same_path_and_continue_the_round`). Mehrere Aufrufe
  je Runde: nur der erste wird ausgeführt, die übrigen bekommen „nicht ausgeführt“.
* Beide Pfade liefern dieselbe Entscheidung; Argumente werden vollständig vom Broker
  validiert; Anbieterfehler werden als Aufgabenfehler sichtbar (nie als leeres „fertig“).
* Fähigkeiten werden getrennt geprüft (`POST /agent/capabilities/probe`): Text-Chat,
  Grammatik-Entscheidung, native Tool-Calls, Bildeingabe, Grounding – ohne Sonderpfad
  nach Modellnamen. Ergebnis je Modell gespeichert (`agent_checks`).
* Planung über eine Cloud-Verbindung nur mit Projektfreigabe und nie für „Nur lokal“;
  gilt auch für Abläufe im Hintergrund. MCP-Gedächtniszugriff (Connectoren) enthält kein
  Werkzeug, das Aufgaben startet oder Computer steuert.

## 5. Computerbedienung und Kontrolle

* Reihenfolge der Mittel: Datei-/Programmschnittstelle → Browser-DOM / Windows-UIA →
  Bild (`vision.*`) nur, wenn Struktur nicht genügt. Ein zusätzlicher HCU-Adapter wurde
  nicht gebaut (kein messbarer Bedarf in den Aufgaben; siehe Abschnitt 9).
* Vor jeder Aktion: Profil/Sitzung (Kontext), Freigabe (Broker), Ziel (hwnd+pid,
  sichtbar, Vordergrund), Beobachtungszustand (Elementreferenz aus der aktuellen
  Beobachtung, sonst „stale“).
* **Eine Eingabesperre je Desktop** (`InputGuard.lock`): nur eine Eingabe injizierende
  Aktion zur Zeit. **Nutzereingaben** (GetLastInputInfo, nicht von uns) innerhalb von
  0,9 s → Aktion wird nicht ausgeführt, Aufgabe pausiert („Nutzereingabe erkannt“);
  unerwarteter Fokuswechsel nach Tastatureingabe → Pause. Kein konkurrierender
  Mausagent.
* **Stopp ohne Modell:** `cancel()` erhöht die Abbruchgeneration und sperrt Folgeaktionen
  (gemessen p95 < 100 ms im Test), löst gehaltene Modifier-Tasten, beendet
  Live-Sitzungen (Aufnahme) und Demonstrationen. Eine **Epoche** (`AgentManager.epoch`)
  wird bei Profilwechsel/globalem Stopp erhöht; späte Werkzeugergebnisse der alten Epoche
  starten keine Folgeaktion. Sperrbildschirm → Pause (automatische Fortsetzung, sobald
  der Desktop wieder interaktiv ist); Datenträgerverlust → `interrupted`.
* Aktionsjournal: Absicht vor der Wirkung, Ergebnis danach; nach Absturz
  `unknown_outcome`. Beim Fortsetzen nach einem unklaren **externen** Ergebnis wird der
  Planer ausdrücklich angewiesen, nicht blind erneut zu senden. Innerhalb eines Laufs
  wird ein identischer Aufruf mit Wirkung nie wiederholt (Journal-Wächter; keine
  Cache-Erlaubnis für Nebenwirkungen).
* Alle Wege (Planer, Ablauf, Reparatur, Demonstration, Verifier) gehen durch
  `Broker.validate` + `requires_approval`. Es gibt keine Shell, keinen generierten Code.
  Seiten, Screenshots, Dateiinhalte und Werkzeugantworten sind Daten (Systemprompt-Regel
  + Test mit eingebetteter Anweisung „an admin@ senden“: Freigabe nötig, abgelehnt,
  Kontrollaufgabe erfüllt).
* Folgenreiche Aktionen (senden, kaufen, löschen, Zielnamen mit Signalwörtern) werden als
  konkrete Entscheidung vorbereitet (`pending_question.action`), freigegebene
  Routineaktionen laufen ohne Rückfrage.

## 6. Ergebnisprüfung und Lernen

* Kandidat → geprüft nur nach ≥ 2 erfolgreichen Läufen mit ≥ 2 Parametersätzen **und**
  bestandener Negativprüfung; ein fehlgeschlagener Wiederverwendungslauf stuft zurück.
* Reparatur nur dort, wo Abschluss prüfbar ist (Schritt hat Nachbedingungen,
  Abhängigkeiten oder ist abgeleitet); bloße 0.3-Rezeptschritte gehen an den Planer.
  Wo die Reparatur mechanisch ist (erneut lesen, neu ableiten), läuft sie ohne Planer –
  Befund der Produktdemo (Testbericht Abschnitt 5).
* Unveränderte Teilergebnisse werden nur übersprungen, wenn alle gelesenen Dateien die
  aufgezeichnete Revision haben **und** die Nachbedingungen jetzt erfüllt sind (journaliert
  als „bereits erfüllt (geprüft)“). Faktenbasierte und abgeleitete Schritte werden nicht
  stillschweigend behalten.
* Demonstration (`teach.py`): nur die gewählte Quelle. Browser: Recorder-Skript auf der
  eigenen Seite (Klick/Eingabe/Auswahl mit Rolle+Name). Fenster: „Schritt erfassen“ =
  UIA-Diff der Feldwerte, Knöpfe benennt der Nutzer. Kein globaler Tastatur-Hook, keine
  Bildschirmaufzeichnung. Ergebnis ist ein Kandidat, der erst durch einen Prüflauf belegt
  wird.
* Gelernte Abläufe erweitern keine Rechte: Umfang kommt immer aus der Aufgabenanlage.

## 7. Portabilität

Abläufe, Abhängigkeiten, Läufe und Bindungen liegen im Profil-SQLite neben dem
Gedächtnis (ein Gedächtnis, keine zweite Datenbank). Bindungen sind host-gebunden; auf
einem anderen PC werden Rollen neu gebunden (Umfang der Aufgabe, Rückfrage), Fakten über
ihre Knoten-IDs geprüft, Werkzeugverträge über Hashes. Migration v5 → v6 ist additiv
(ALTER/CREATE), 0.3-Rezepte werden beim Start als Kandidaten übernommen (Tabelle
`agent_recipes` bleibt). Siehe `AGENT_V2_MIGRATION.md`.

## 8. Abgrenzung zu vorhandenen Ansätzen

Diese Arbeiten sind Vorläufer und Inspiration, **keine Belege** für die Überlegenheit
unseres Entwurfs; die Nummern verweisen auf die im Auftrag genannten Fassungen.

| Ansatz | Was wir übernommen haben (Wissen) | Was hier anders ist (eigener Mechanismus) |
|---|---|---|
| Agent S4 (arXiv 2610.28601) | hierarchische Planung mit Erfahrungsspeicher; Beobachtung vor jeder Aktion | kein narrativer Erfahrungsspeicher: Wiederverwendung nur über deklarative Pläne mit expliziten, geprüften Abhängigkeiten; keine Modell-Selbstbewertung als Abschluss |
| ScienceCUA (arXiv 2611.03238) | Trennung von Beobachtung, Aktion und Verifikation; Werkzeugerst-Zugriff vor GUI | lokale Modelle, ein Profil-SQLite, host-gebundene Bindungen; Verifikation durch deterministische Zustandsprüfungen, nicht durch LLM-Richter |
| Dependency-Guided Rollback Repair v2 (arXiv 2608.10502v2) | Abhängigkeitsgraph zur Eingrenzung von Reparaturen | kein Rollback-Versprechen für GUI/Netz: bereits erfolgte Wirkungen werden offen angezeigt; Reparatur nur mit Prüfbarkeit, Budget und Rückfall auf Neuplanung; Nebenwirkungen werden nie aus einem Cache wiederholt |
| GRADE v3 (arXiv 2606.22741v3) | Bewertung über Fehlerwiederholung und Abdeckung | Abdeckung = beobachtete+deklarierte Abhängigkeiten / alle; vermutete Beziehungen senken sie, statt sie als Kanten zu zählen; Fehlerwiederholung wird je Lauf gezählt (`repeated_failures`) |
| Microsoft UFO / Hybrid Computer Use, UI-TARS Desktop, Playwright MCP | UIA-first, Browser über DOM/Playwright, Bild nur als Rückfall | kein Framework übernommen; Adapter aus 0.3 bleiben, ein HCU-Adapter wurde mangels messbarem Nutzen nicht eingebaut |

**Ungeprüfte Hypothesen (ausdrücklich offen):** (a) dass die Abhängigkeitsabdeckung
falsche Wiederverwendung in anderen Domänen als den 60 synthetischen Aufgaben zuverlässig
verhindert; (b) dass selektive Reparatur bei UI-Änderungen in echten Anwendungen weniger
Fehler wiederholt als Neuplanung (in den Messungen nur mit eigenen Testfenstern/-seiten
geprüft); (c) dass die Ersparnis an Modellaufrufen bei größeren Modellen und Cloud-
Anbietern in gleicher Höhe anfällt (nur lokal mit Qwen3-4B gemessen).

## 9. Umsetzungsstand und Lücken

| Bereich | Stand |
|---|---|
| Datei-/Dokumentabläufe durch den Kern | vollständig (reuse/repair/replan, Ableitung, Überspringen nach Prüfung) |
| Browserabläufe durch den Kern | vollständig für die eigene Chromium-Instanz; Ziele über Rolle+Name, Seite wird je Lauf neu gelesen |
| Windows-Abläufe | Schritte mit UIA-Zielen (Name) werden aufgelöst; Rückfall auf Reparatur/Planer; Eingabesperre und Nutzer-Pause aktiv |
| Bild (vision.*) | unverändert 0.3 (Beschreiben, Lokalisieren nach Kalibrierung); keine Wiederverwendung von Bildkoordinaten |
| Native Tool-Calls | Pfad implementiert und mit Fake-Adapter getestet; gegen einen echten Cloud-Anbieter **nicht** ausgeführt (0 € Vorgabe); lokale llama-server-Tool-Calls über die Probe prüfbar |
| HCU-Adapter | nicht gebaut |
| Beibringen | Browser-Recorder und Fenster-Diff implementiert; Browser-Recorder nur im Dev-Kern mit eigener Seite geprüft (siehe Testbericht) |
| Zwei-PC-/USB-Transfer | nicht physisch durchgeführt; automatisierte Umzugstests + Anleitung in `AGENT_V2_MIGRATION.md` |
