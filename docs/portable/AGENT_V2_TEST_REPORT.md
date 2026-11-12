# SpAIder Foldable 0.5 (Agent v2): Test- und Leistungsbericht

Stand: 2026-11-12. Branch `feature/spaider-foldable-agent-v2`. Hardware: Intel Core Ultra
(Arc 140V, Vulkan), Windows 11, ein Rechner. Modell für alle echten Läufe: Qwen3-4B Q4_K_M
(`qwen3-4b-q4_k_m`, llama.cpp b12032, Vulkan). Alle Zahlen stammen aus den JSON-Dateien
unter `foldable/build/measurements/` bzw. werden dort genannt; **nichts ist geschätzt**.
Mocks und Skripttests sind getrennt ausgewiesen. Die Messreihe (Abschnitt 4) lief auf dem Stand
725a484 (Dateigruppe 8c06758/02a3606); die danach behobene Reparaturlücke (0e6bdb4, Abschnitt 5)
ist im Paket enthalten, die Tabellen wurden dafür nicht neu gemessen.

Einstufung: **echt** = laufender Kern + echtes Modell + unabhängige Prüfung des Endzustands;
**skriptiert** = Loop/Gate/Compiler mit skriptiertem Planer ohne llama-server;
**Mock** = Oberfläche gegen Mock-Server.

## 1. Baseline vor den Änderungen (reproduzierbar)

Basis-Commit 219828a (PR #60, Agent 0.3/0.4), gleiche Hardware, gleiches Modell, gleiche
Aufgaben (`tests/matrix/tasks.json`, Seed 7), 3 Wiederholungen, Zeitbudget 180 s, Schrittbudget 14.

| Lauf | Datei | Ergebnis |
|---|---|---|
| Datei + Browser (10 Aufgaben × 3) | `baseline-fb-qwen3-4b-219828a.json` | **27/30**, 1 Fehlerfolge-Meldung, 0 Rückfragen, Median 31,6 s, Modellaufrufe = Schritte (jeder Schritt ein Aufruf) |
| Fenster + Bild (5 Fensteraufgaben × 3; Bildaufgaben ohne Lauf) | `baseline-wv-qwen3-4b-219828a.json` | **3/15, nur hinter dem Sperrbildschirm messbar** (12.11., 07:37–07:51): W2 (reines UIA-Lesen) 3/3 mit je 3 Aufrufen, 25–29 s; W1/W3/W4/W5 scheitern an `windows.focus`/`click_at` (Fenster nicht in den Vordergrund zu holen), V1–V5 ohne Lauf (kein Bildmodell). Kein belastbarer Vergleichswert; Teilvergleich W2: 0.5 wiederverwendet mit 1 Aufruf und 7–11 s (`matrix2-full-windows-cross.locked1.json`) |

Die Baseline hat keinen Ablauf-Mechanismus außer 0.3-Rezepten; jede Wiederholung ist ein
Planerlauf. Sie ist der Vergleichswert für „unbekannte Aufgabe“ und für den Arm „ohne
Wiederverwendung“.

## 2. Unit- und Integrationstests (skriptiert, ohne Modell)

`cd foldable/core && pytest tests` → **159 bestanden, 8 übersprungen** (Stand 0e6bdb4, mit `SPAIDER_FOLDABLE_BROWSER_DIR`; ohne Browser-Ordner 158/9) (die übersprungenen sind Echtläufe, die `SPAIDER_FOLDABLE_REAL_TESTS=1` bzw. den Chromium-Ordner brauchen; mit `SPAIDER_FOLDABLE_BROWSER_DIR` läuft zusätzlich der echte Browser-Recorder-Test). Desktop: `npm test` → **123 bestanden (14 Dateien)**, `npm run typecheck` und `npm run lint` ohne Befund.

Neu in 0.5 (`tests/test_agent_v2.py`, `tests/test_agent_v2_api.py`):

| Pflichttest (Auftrag) | Test | Ergebnis |
|---|---|---|
| Stopp mit ausstehendem Tool-Call | `test_stop_with_pending_native_tool_call_executes_nothing`, `test_cancel_blocks_further_actions` (0.3) | bestanden: kein Aufruf wird nach dem Stopp ausgeführt, Adapter abgebrochen |
| Stopp-Latenz (Sperre neuer Aktionen) | `test_stop_locks_follow_up_actions_fast_p95` (40 Aufgaben) | bestanden; gemessen (40 Stopps, In-Prozess): Sperre p50 0,6 ms, p95 1,2 ms, max 1,3 ms; interne Stopp-Latenz p95 0,4 ms |
| veralteter Zustand | `test_stale_ui_target_is_not_executed_blindly` + Broker-Frischeprüfung (0.3) | bestanden: Ziel nicht in aktueller Beobachtung → neu lesen → Übergabe, keine Ausführung mit alter ID |
| Profilwechsel | `test_profile_switch_epoch_cancels_everything_and_ignores_late_results` | bestanden: Epoche erhöht, laufende Aktion `unknown_outcome`, keine Folgeplanung |
| Prompt-Injection + harmlose Kontrollaufgabe | `test_injected_instructions_cannot_grant_external_effects`; echt: B5/B12 der Matrix | bestanden: `mail.send` braucht Freigabe (abgelehnt → journaliert), Betrag korrekt geschrieben; Matrix siehe Abschnitt 4 |
| App-Ausfall | `test_app_failure_is_reported_not_crashed` | bestanden: `source_lost` → dreimal gescheitert → Aufgabe sauber `failed`, Ursache sichtbar |
| Neustart | `test_restart_marks_running_tasks_interrupted_and_external_outcomes_unknown`, `test_resume_after_unknown_external_outcome_warns_instead_of_repeating` | bestanden: `interrupted`/`unknown_outcome`; Fortsetzen warnt „nicht blind erneut senden“ |
| Memory-Korrektur | `test_superseded_fact_repairs_only_dependent_steps`, `test_dependency_invalidation_is_transitive_and_keeps_history`; echt: F13/X4/X6/X10 (`fact_change`) | bestanden: ersetzter Fakt → nur abhängige Schritte repariert, Bericht wiederverwendet; Historie bleibt |
| Offline | `tests/test_real_offline.py` (0.3, echt) | in 0.5 nicht erneut ausgeführt (Modell/GPU während der Messreihe belegt); zuletzt 2026-11-11 auf dem 0.4-Kern bestanden (`MODELHUB_TEST_REPORT.md` T15); der Offline-Code ist in 0.5 unverändert |
| CPU-Rückfall | `tests/test_real_gpu_fallback.py` (0.3, echt) | in 0.5 nicht erneut ausgeführt; zuletzt 2026-11-11 auf dem 0.4-Kern bestanden (T15); Laufzeitstart in 0.5 unverändert |
| Nur-lokal-Sperre | `test_cloud_planner_respects_local_only_and_consent`, `tests/test_cloud_isolation.py` (0.4) | bestanden: 403 `local_only`, 409 `consent_required`; Kanarien-Test weiterhin grün |
| Datenmigration | `test_schema_v6_migrates_v5_database_with_recipes`; Kopie der Nutzerdaten: Abschnitt 6 | bestanden: v5 → v6, Rezept als Kandidat, `quick_check ok` |
| Nutzereingabe pausiert | `test_user_input_pauses_the_task_and_resume_continues` | bestanden: Aktion verworfen, Aufgabe pausiert, Fortsetzen erledigt sie |
| falsche Fertigmeldung | `test_false_done_claim_is_caught_by_verification` | bestanden: fehlende Ausgabedatei → Zurückweisung, dann `not_fulfilled` |
| doppelte Nebenwirkung | `test_workflow_never_repeats_an_identical_side_effect` | bestanden: zweiter identischer Schreibschritt abgelehnt |
| Reparatur-Entscheidungen | `test_reuse_gate_decisions`, `test_unchanged_partial_result_is_skipped_only_after_verification`, `test_binding_resolver_rebinds_roles_on_this_host_only`, `test_compiler_builds_portable_workflow_from_journal`, `test_workflow_lifecycle_promotion_and_demotion` | bestanden |
| native Tool-Calls | `test_native_tool_calls_share_the_same_path_and_continue_the_round` (Fake-Adapter) | bestanden: Ergebnis unter derselben Aufruf-ID, Runde fortgesetzt; **kein echter Cloud-Anbieter** |

Bestehende 0.3/0.4-Regressionstests sind unverändert enthalten (Agentenkern, Werkzeuge,
Live-API, Connectoren, Profile, Cloud-Isolation, Modelle, Dokumente).

## 3. Smoke-Lauf mit echtem Modell (vor der Messreihe)

`smoke2-v2-full.json` (F1, F13, B1, B4; 3 Wiederholungen; Arm „voll“): erste Läufe 3/3 erfolgreich
(F13 0/3 – Modell schrieb den Mailentwurf ohne „An: <E-Mail>“; der Verifier erkannte das zu
diesem Zeitpunkt noch nicht → seitdem zitierte Pflichttexte als Kriterium). Wiederholungen 6/6
erfolgreich mit **1,0 Modellaufrufen im Mittel statt 5,3** (−81 %) und **10,2 s statt 41,5 s**
(−75 %); B1 mit umbenanntem Knopf („Entwurf sichern“) im dritten Lauf erfolgreich mit 2
Aufrufen. Befund daraus: Browser-Ziele wurden noch über Referenzen abgespielt → behoben
(Rolle+Name, Commit 8ad9245); die Messreihe lief danach mit diesem Stand.

## 4. Messreihe: 60 Aufgaben, drei Vergleichsarme (echt)

`tests/matrix/tasks_v2.json`: 20 Dateien/Dokumente, 15 Browser, 15 Windows, 10
anwendungsübergreifend; 40 Lernfälle (Ablauf wird nach dem ersten Erfolg kompiliert und
zweimal mit neuen Daten wiederholt), 20 Testfälle (immer unbekannt). `fact_change`
(F13, X4, X6, X10): vor den Wiederholungen wird die Projektleitung über den
Gedächtnis-Connector korrigiert. `variant` (B1, W6): vor der letzten Wiederholung ändert die
Anwendung ihre Knopfnamen. Parameter sind nur Werte, die ein Nutzer beim Start eingeben würde;
alles aus Dateien/Seiten/Fenstern/Gedächtnis bleibt abgeleitet (ein Modellaufruf je solchem
Schritt).

Arme (gleiche Hardware, gleiche Gewichte, gleicher Seed, gleiche Aufgaben):

| Arm | Bedeutung |
|---|---|
| ohne Wiederverwendung (`none`) | jede Wiederholung ist ein Planerlauf (wie Baseline) |
| einfache Rezepte (`simple`) | kompilierter Ablauf wird wie ein 0.3-Rezept wörtlich abgespielt (keine Abhängigkeiten, keine Nachbedingungen, keine Ableitung) |
| voller Mechanismus (`full`) | ReuseGate: wiederverwenden → reparieren → neu planen, Ableitung, Prüfung jedes Schritts |

### 4.1 Ergebnis in einem Absatz

Bei Datei- und Browseraufgaben erreicht der volle Mechanismus bei Wiederholungen **88 % Erfolg
(37/42) mit 2,0 Modellaufrufen und 19 s je Lauf** gegenüber **90 % (45/50) mit 5,3 Aufrufen und
40 s** ohne Wiederverwendung: **62 % weniger Modellaufrufe, 53 % weniger Dauer** – die Zielmarken
(≥ 50 % / ≥ 30 %) sind erreicht, die Bedingung „mindestens gleiche Qualität“ **nicht ganz**:
zwei Prozentpunkte weniger Erfolg, und 5 der 42 Wiederholungen waren *falsche Wiederverwendung*
(Ergebnis falsch, Kern meldete „erfüllt“), alle in der Browser-Gruppe bei Schritten, deren Wert
das Modell neu ableiten musste. Einfache Rezepte (wörtliche Wiedergabe, 0.3-Semantik) liegen bei
**55 % (24/44) mit 18 falschen Wiederverwendungen** – das ist der Zustand, den der
Abhängigkeitsmechanismus verhindert. Bei den Dateiaufgaben allein ist der volle Mechanismus mit
24/24 Wiederholungen ohne falsche Wiederverwendung sauber; bei Browseraufgaben (13/18) liegt die
Schwäche in der Ableitung einzelner Feldwerte durch Qwen3-4B, nicht im Gate. Bei den
anwendungsübergreifenden Aufgaben zeigt X6 den Reparaturweg wie vorgesehen (Faktenänderung →
2 Aufrufe statt 4, Empfänger erneuert); die Produktdemo X10 deckte eine Lücke im Reparaturweg auf,
die nach der Messreihe behoben wurde (Abschnitt 5).

### 4.2 Tabellen (automatisch aus den JSON-Berichten erzeugt, `report_tables.py`)

Lesehilfe: „unbekannt“ = erster Lauf einer Lernaufgabe (kein Ablauf vorhanden), „Testfälle“ =
immer unbekannt, „Wiederholungen mit Ablauf“ = zweiter/dritter Lauf mit kompiliertem Ablauf im
jeweiligen Arm, „ohne Ablauf“ = Wiederholung, deren erster Lauf scheiterte (dann plant der Planer;
im Arm „ohne“ ist jede Wiederholung ein Planerlauf). ✓/✗ = unabhängige Prüfung des Endzustands,
✗! = Kern meldete „erfüllt“, Prüfung scheiterte (falsche Fertigmeldung); (reu)/(rep) =
Gate-Entscheidung reuse/repair.

#### Gruppen Dateien + Browser (Code 8c06758, 725a484, 02a3606, Modell qwen3-4b-q4_k_m)

| Arm | Läufe | Erfolg | falsche Fertigmeldungen | falsche Wiederverwendung | Rückfragen | Nebenwirkungen | wiederholte Fehler | Modellaufrufe Ø | Dauer Ø |
|---|---|---|---|---|---|---|---|---|---|
| full – unbekannt (Lernfälle, 1. Lauf) | 25 | 21/25 | 2 | 0 | 0 | 0 | 2 | 5.56 | 45.6 s (Median 30.6) |
| full – Testfälle (unbekannt) | 10 | 7/10 | 3 | 0 | 0 | 0 | 0 | 5.20 | 38.9 s (Median 33.4) |
| full – Wiederholungen mit Ablauf (full) | 42 | 37/42 | 5 | 5 | 0 | 0 | 0 | 2.00 | 19.0 s (Median 11.4) |
| full – Wiederholungen ohne Ablauf (1. Lauf gescheitert → Planer) | 8 | 3/8 | 0 | 0 | 0 | 0 | 2 | 8.12 | 67.6 s (Median 57.7) |
| simple – unbekannt (Lernfälle, 1. Lauf) | 25 | 22/25 | 3 | 0 | 0 | 0 | 0 | 5.64 | 38.2 s (Median 29.6) |
| simple – Testfälle (unbekannt) | 10 | 6/10 | 4 | 0 | 0 | 0 | 0 | 4.70 | 28.4 s (Median 29.3) |
| simple – Wiederholungen mit Ablauf (simple) | 44 | 24/44 | 18 | 18 | 0 | 0 | 3 | 2.70 | 22.6 s (Median 0.1) |
| simple – Wiederholungen ohne Ablauf (1. Lauf gescheitert → Planer) | 6 | 2/6 | 3 | 0 | 0 | 0 | 2 | 5.50 | 38.4 s (Median 44.0) |
| none – unbekannt (Lernfälle, 1. Lauf) | 25 | 21/25 | 3 | 0 | 0 | 0 | 1 | 5.28 | 39.9 s (Median 32.2) |
| none – Testfälle (unbekannt) | 10 | 8/10 | 2 | 0 | 0 | 0 | 0 | 4.70 | 34.5 s (Median 28.5) |
| none – Wiederholungen mit Ablauf (none) | 50 | 45/50 | 2 | 0 | 0 | 0 | 4 | 5.32 | 40.1 s (Median 32.2) |

Arm „voll“, Wiederholungen: Gate-Entscheidungen {'reuse': 42}; Gate-Zeit Ø 38 ms; reparierte Schritte gesamt 0; Spitzen-RSS max 11694 MB; durch Nutzereingabe pausierte Läufe 0.
Faktenänderung (fact_change) im Arm „voll“: 1/2 erfolgreich, Entscheidungen ['None'], Modellaufrufe Ø 7.5.
App-Variante (umbenannte Knöpfe) im Arm „voll“: 0/1 erfolgreich, Entscheidungen ['reuse'].

| Aufgabe | Set | Lauf 1 | Lauf 2 | Lauf 3 | Modellaufrufe 1/2/3 | Anmerkung |
|---|---|---|---|---|---|---|
| B1 | learn | ✓ | ✗! (reu) | ✗! (reu) | 9/7/7 | App-Variante |
| B2 | learn | ✓ | ✓ (reu) | ✓ (reu) | 5/1/1 |  |
| B3 | learn | ✓ | ✓ (reu) | ✓ (reu) | 4/0/0 |  |
| B4 | learn | ✓ | ✓ (reu) | ✓ (reu) | 5/2/2 |  |
| B5 | test | ✓ | – | – | 3/–/– |  |
| B6 | learn | ✓ | ✓ (reu) | ✓ (reu) | 9/6/5 |  |
| B7 | learn | ✓ | ✓ (reu) | ✓ (reu) | 6/5/4 |  |
| B8 | learn | ✓ | ✓ (reu) | ✓ (reu) | 5/5/4 |  |
| B9 | test | ✗! | – | – | 6/–/– |  |
| B10 | learn | ✓ | ✗! (reu) | ✓ (reu) | 5/1/1 |  |
| B11 | learn | ✓ | ✗! (reu) | ✗! (reu) | 8/5/4 |  |
| B12 | test | ✗! | – | – | 14/–/– |  |
| B13 | learn | ✗! | ✗ | ✗ | 7/6/7 |  |
| B14 | learn | ✗ | ✗ | ✗ | 14/13/14 |  |
| B15 | test | ✓ | – | – | 4/–/– |  |
| F1 | learn | ✓ | ✓ (reu) | ✓ (reu) | 3/1/1 |  |
| F2 | learn | ✓ | ✓ (reu) | ✓ (reu) | 3/1/1 |  |
| F3 | learn | ✓ | ✓ (reu) | ✓ (reu) | 3/1/1 |  |
| F4 | test | ✓ | – | – | 5/–/– |  |
| F5 | learn | ✓ | ✓ (reu) | ✓ (reu) | 4/1/1 |  |
| F6 | learn | ✓ | ✓ (reu) | ✓ (reu) | 5/1/1 |  |
| F7 | learn | ✓ | ✓ (reu) | ✓ (reu) | 3/1/1 |  |
| F8 | learn | ✓ | ✓ (reu) | ✓ (reu) | 3/1/1 |  |
| F9 | test | ✓ | – | – | 6/–/– |  |
| F10 | learn | ✓ | ✓ (reu) | ✓ (reu) | 3/1/1 |  |
| F11 | learn | ✓ | ✓ (reu) | ✓ (reu) | 4/1/1 |  |
| F12 | test | ✗! | – | – | 3/–/– |  |
| F13 | learn | ✗ | ✗ | ✓ | 13/6/9 | Faktenänderung |
| F14 | learn | ✓ | ✓ (reu) | ✓ (reu) | 4/1/1 |  |
| F15 | test | ✓ | – | – | 3/–/– |  |
| F16 | learn | ✓ | ✓ (reu) | ✓ (reu) | 6/1/1 |  |
| F17 | learn | ✓ | ✓ (reu) | ✓ (reu) | 3/1/1 |  |
| F18 | test | ✓ | – | – | 3/–/– |  |
| F19 | learn | ✗! | ✓ | ✓ | 5/5/5 |  |
| F20 | test | ✓ | – | – | 5/–/– |  |

| Arm | Wiederholungen | Erfolg | Modellaufrufe Ø | Dauer Ø | falsche Wiederverwendung |
|---|---|---|---|---|---|
| full | 42 | 37/42 (88 %) | 2.00 | 19.0 s | 5 |
| simple | 44 | 24/44 (55 %) | 2.70 | 22.6 s | 18 |
| none | 50 | 45/50 (90 %) | 5.32 | 40.1 s | 0 |

voll vs. ohne Wiederverwendung (Wiederholungen): Ersparnis Modellaufrufe 62 %, Ersparnis Dauer 53 %, Erfolg 88 % vs. 90 %, falsche Wiederverwendung 5 vs. 0.

#### Gruppen Fenster + anwendungsübergreifend (nur die sechs Cross-Aufgaben ohne Fenster liefen; Fensteraufgaben übersprungen, Abschnitt 4.3) (Code 8c06758, 725a484, a378452, Modell qwen3-4b-q4_k_m)

| Arm | Läufe | Erfolg | falsche Fertigmeldungen | falsche Wiederverwendung | Rückfragen | Nebenwirkungen | wiederholte Fehler | Modellaufrufe Ø | Dauer Ø |
|---|---|---|---|---|---|---|---|---|---|
| full – unbekannt (Lernfälle, 1. Lauf) | 5 | 1/5 | 2 | 0 | 0 | 0 | 0 | 7.80 | 69.6 s (Median 58.5) |
| full – Testfälle (unbekannt) | 1 | 0/1 | 1 | 0 | 0 | 0 | 0 | 5.00 | 31.7 s (Median 31.7) |
| full – Wiederholungen mit Ablauf (full) | 2 | 2/2 | 0 | 0 | 0 | 0 | 0 | 2.00 | 20.6 s (Median 21.5) |
| full – Wiederholungen ohne Ablauf (1. Lauf gescheitert → Planer) | 8 | 2/8 | 2 | 0 | 1 | 0 | 0 | 9.38 | 86.6 s (Median 84.6) |
| simple – unbekannt (Lernfälle, 1. Lauf) | 5 | 2/5 | 1 | 0 | 1 | 0 | 0 | 8.80 | 102.6 s (Median 79.0) |
| simple – Testfälle (unbekannt) | 1 | 0/1 | 1 | 0 | 0 | 0 | 0 | 5.00 | 37.0 s (Median 37.0) |
| simple – Wiederholungen mit Ablauf (simple) | 4 | 0/4 | 0 | 0 | 0 | 0 | 0 | 10.00 | 156.9 s (Median 252.2) |
| simple – Wiederholungen ohne Ablauf (1. Lauf gescheitert → Planer) | 6 | 0/6 | 2 | 0 | 0 | 0 | 2 | 9.33 | 91.0 s (Median 85.0) |
| none – unbekannt (Lernfälle, 1. Lauf) | 5 | 1/5 | 1 | 0 | 0 | 1 | 1 | 8.80 | 96.2 s (Median 93.3) |
| none – Testfälle (unbekannt) | 1 | 0/1 | 1 | 0 | 0 | 0 | 0 | 5.00 | 32.3 s (Median 32.3) |
| none – Wiederholungen mit Ablauf (none) | 10 | 3/10 | 2 | 0 | 0 | 0 | 1 | 8.30 | 86.9 s (Median 73.4) |

Arm „voll“, Wiederholungen: Gate-Entscheidungen {'repair': 2}; Gate-Zeit Ø 47 ms; reparierte Schritte gesamt 2; Spitzen-RSS max 6800 MB; durch Nutzereingabe pausierte Läufe 0.
Faktenänderung (fact_change) im Arm „voll“: 4/6 erfolgreich, Entscheidungen ['None', 'repair'], Modellaufrufe Ø 6.8.

| Aufgabe | Set | Lauf 1 | Lauf 2 | Lauf 3 | Modellaufrufe 1/2/3 | Anmerkung |
|---|---|---|---|---|---|---|
| W1 | learn | ✗ | ✗ | ✗ | –/–/– |  |
| W2 | learn | ✗ | ✗ | ✗ | –/–/– |  |
| W3 | learn | ✗ | ✗ | ✗ | –/–/– |  |
| W4 | test | ✗ | – | – | –/–/– |  |
| W5 | learn | ✗ | ✗ | ✗ | –/–/– |  |
| W6 | learn | ✗ | ✗ | ✗ | –/–/– |  |
| W7 | learn | ✗ | ✗ | ✗ | –/–/– |  |
| W8 | learn | ✗ | ✗ | ✗ | –/–/– |  |
| W9 | test | ✗ | – | – | –/–/– |  |
| W10 | learn | ✗ | ✗ | ✗ | –/–/– |  |
| W11 | learn | ✗ | ✗ | ✗ | –/–/– |  |
| W12 | test | ✗ | – | – | –/–/– |  |
| W13 | learn | ✗ | ✗ | ✗ | –/–/– |  |
| W14 | learn | ✗ | ✗ | ✗ | –/–/– |  |
| W15 | test | ✗ | – | – | –/–/– |  |
| X1 | learn | ✗ | ✗ | ✗ | –/–/– |  |
| X2 | learn | ✗! | ✗! | ✗! | 8/7/7 |  |
| X3 | test | ✗ | – | – | –/–/– |  |
| X4 | learn | ✗ | ✗ | ✗ | 7/8/7 | Faktenänderung |
| X5 | learn | ✗ | ✗ | ✗ | –/–/– |  |
| X6 | learn | ✓ | ✓ (rep) | ✓ (rep) | 4/2/2 | Faktenänderung |
| X7 | test | ✗ | – | – | –/–/– |  |
| X8 | learn | ✗ | ✗ | ✗ | 9/14/10 |  |
| X9 | test | ✗! | – | – | 5/–/– |  |
| X10 | learn | ✗! | ✓ | ✓ | 11/11/11 | Faktenänderung |

| Arm | Wiederholungen | Erfolg | Modellaufrufe Ø | Dauer Ø | falsche Wiederverwendung |
|---|---|---|---|---|---|
| full | 2 | 2/2 (100 %) | 2.00 | 20.6 s | 0 |
| simple | 4 | 0/4 (0 %) | 10.00 | 156.9 s | 0 |
| none | 10 | 3/10 (30 %) | 8.30 | 86.9 s | 0 |

voll vs. ohne Wiederverwendung (Wiederholungen): Ersparnis Modellaufrufe 76 %, Ersparnis Dauer 76 %, Erfolg 100 % vs. 30 %, falsche Wiederverwendung 0 vs. 0.

Die Dauer-Mediane des Arms „einfach“ (0,1 s) zeigen die wörtliche Wiedergabe ohne jede Prüfung;
sie ist schnell und in 18 von 44 Fällen falsch.

### 4.3 Fenster und anwendungsübergreifend

Die Sitzung war während der Nachtmessung gesperrt (Abschnitt 9). Gemessen werden konnten die
UI-Automation-Aufgaben vor der Sperr-Erkennung (`matrix2-full-windows-cross.locked1.json`,
22 Läufe, Code a378452 ohne Sperr-Erkennung, nur Arm „voll“):

| Aufgabe | Lauf 1 (unbekannt) | Lauf 2 | Lauf 3 | Modellaufrufe 1/2/3 | Dauer 1/2/3 |
|---|---|---|---|---|---|
| W2 Notizen-Feld lesen → Datei | ✓ | ✓ reuse | ✓ reuse | 3/1/1 | 27,0 / 11,3 / 7,2 s |
| W3 „Leeren“ klicken, Status prüfen → Datei | ✓ | ✓ reuse | ✓ reuse | 7/0/0 | 82,9 / 5,8 / 4,7 s |
| W5 Name setzen, Speichern, Datei prüfen | ✓ | ✓ repair | ✓ repair | 5/4/1 | 65,7 / 63,4 / 22,7 s |
| W7 Status-Label → Datei | ✓ | ✓ reuse | ✓ reuse | 3/1/1 | 22,5 / 8,4 / 5,5 s |
| W1, W4, W6, W8 (Vordergrund/Tastatur nötig) | ✗ | ✗ | ✗ | – | Sperrbildschirm: `windows.focus`/`click_at` scheitern, Aufgabe ehrlich `not_fulfilled` |

Die sechs Cross-Aufgaben ohne Fenster (Dateien, Browser, Gedächtnis) liefen in allen drei Armen
(voll: Code 725a484; einfach: a378452; ohne: 8c06758 – für diese Aufgaben mechanisch gleich):

| Aufgabe | voll (Lauf 1/2/3, Aufrufe) | einfache Rezepte | ohne Wiederverwendung | Befund |
|---|---|---|---|---|
| X2 Datei → Browserformular (3 Felder) + Entwurf | ✗! ✗! ✗! (8/7/7) | ✗! ✗! ✗! (8/8/9) | ✗! ✗! ✗! (7/7/7) | in allen Armen falsche Fertigmeldung: Bestätigungstext erschien, ein Feldwert war falsch (Modell); kein Ablauf kompiliert |
| X4 Gedächtnis → Browserfeld → Datei (Faktenänderung) | ✗ ✗ ✗ (7/8/7) | ✗ ✗ ✗ (8/9/8) | ✗ ✗ ✗ (9/9/7) | ehrlich `not_fulfilled` in allen Armen (Modell fand den Weg Gedächtnis → Feld nicht); kein Ablauf |
| X6 Dateien + Gedächtnis → Mailentwurf (Faktenänderung) | ✓ ✓rep ✓rep (4/2/2; 30 / 21,5 / 19,8 s) | ✓ ✗ ✗ (5/5/7) | ✓ ✓ ✓ (4/4/4; 36 / 29 / 32 s) | **Reparaturweg wie vorgesehen**: nur der Mailschritt repariert, neuer Empfänger im Entwurf; wörtliche Wiedergabe liefert den alten Empfänger |
| X8 Datei → Browser-Auswahlfeld → Datei | ✗ ✗ ✗ (9/14/10) | ✗ ✗ ✗ (10/9/13) | ✗ ✗ ✗ (10/9/12) | Modell scheitert am Auswahlfeld in allen Armen; kein Ablauf |
| X9 Browsertabelle → CSV (Testfall) | ✗! (5) | ✗! (5) | ✗! (5) | falsche Fertigmeldung in allen Armen: CSV unvollständig, Auftrag nennt keinen prüfbaren Inhalt |
| X10 Produktdemo (Faktenänderung) | ✗! ✓ ✓ (11/11/11) | ✓ ✗ ✗ (13/14/14) | ✗ ✓ ✗ (14/10/14) | erster Lauf im Arm „voll“ falsche Fertigmeldung (Mailentwurf ohne E-Mail-Adresse; der Auftrag zitiert nur den Betreff) → kein Ablauf, Wiederholungen als Planerläufe; Details und Nachlauf in Abschnitt 5 |

Im Arm „voll“ gab es bei den Cross-Aufgaben 0 falsche Wiederverwendungen; die Fehlschläge sind
Modellfehler beim ersten (unbekannten) Lauf, die alle Arme gleichermaßen treffen. X1, X3, X5, X7
(Fenster) wurden übersprungen.

### 4.4 Verschlechterungen und Grenzen (ausdrücklich)

- Falsche Wiederverwendung im Arm „voll“ (5/42): B1 (abgeleiteter Feldwert: das Modell kopierte
  die ganze Quelldatei bzw. „Beschriftung: Wert“ in das Feld; die Nachbedingung erkannte es, die
  Reparatur durch den Planer übersprang das Feld; die Endprüfung kannte nur den Bestätigungstext),
  B10 (ein abgeleitetes Statuswort einmal falsch), B11 (Klick-Nachbedingung scheiterte, Planer
  schrieb falsche Bewertung). Alle drei: Ergebnisprüfung nur so stark wie die Kriterien des
  Auftrags; ohne zitierte Pflichttexte bleibt ein inhaltlich falsches Ergebnis „erfüllt“.
- Produktdemo X10: Reparatur eines `memory.search`-Schritts nach Faktenänderung ging an den Planer
  und scheiterte am Budget (Abschnitt 5). Nach der Messreihe behoben (Commit 0e6bdb4: rein lesende
  Schritte werden erneut gelesen, abgeleitete neu abgeleitet, Zwischendateien sind keine Eingaben);
  die Tabellen oben stammen vom Stand davor und wurden **nicht** komplett neu gemessen – nur X10
  und X6 liefen auf dem behobenen Stand erneut (Abschnitt 5).
- Nach einem gescheiterten Reparaturlauf wird der Ablauf zurückgestuft (keine automatische
  Wiederverwendung mehr); das ist beabsichtigt konservativ, kostet aber nach einem einzigen
  Modellfehler alle weiteren Ersparnisse, bis der Nutzer den Ablauf wieder freigibt.
- Die Ersparnis entsteht fast vollständig durch wegfallende Beobachtungs-/Planungsrunden; Schritte,
  deren Inhalt aus Daten stammt, kosten weiterhin je einen Aufruf. Aufgaben mit mehreren
  abgeleiteten Schritten (F13, X10: Bericht + Mail) sparen entsprechend weniger.
- Wiederholte identische Fehler traten im Arm „voll“ bei Wiederholungen nicht auf (0), im Arm
  „ohne“ viermal (Planer wiederholt gescheiterte Aufrufe).
- Verwaltungs-Overhead: Gate 20–54 ms je Lauf, Schrittprüfungen 10–300 ms; Spitzen-RSS
  (Kern + llama-server) 11,7–13,2 GB in allen Armen gleich (Modellgewichte dominieren).
- Die Harness-Seiten B8/B11 waren in den ersten Läufen durch ein Escaping-Problem kaputt; die
  Browser-Gruppe wurde deshalb auf dem Endstand komplett neu gemessen (Arme voll/ohne), die
  Dateigruppe stammt aus dem Lauf davor (Code 8c06758 bzw. 02a3606, mechanisch identisch).

Gemessene Größen je Lauf: Erstversuchserfolg (unabhängige Prüfung), falsche Fertigmeldungen,
Eingriffe (Rückfragen), Nebenwirkungen (unerwartete Dateien, Aktionen nach außen),
Laufzeit, Modellaufrufe, Spitzen-RSS von Kern + llama-server, Gate-Zeit, reparierte/
übersprungene Schritte, falsche Wiederverwendung (Wiederverwendung mit falschem Ergebnis),
wiederholte identische Fehler. **Auch Verschlechterungen sind unten aufgeführt.**

## 5. Produktdemo (echt)

X10 der Matrix im Arm „voll“: Projektdateien lesen → `bericht.md` erzeugen → Inhalt prüfen
(Rücklesen) → `mail_entwurf.txt` vorbereiten (An: E-Mail der Projektleitung laut Gedächtnis)
→ Ablauf speichern → Wiederholung mit neuen Daten → Projektleitung im Gedächtnis korrigiert
(Connector `correct_memory`) → betroffene Empfängerzuordnung repariert, Bericht nach Prüfung
wiederverwendet. Kein Versand, nur Testdateien/-konten.

**Messreihe (Code 725a484, `matrix2-full-windows-cross.json`):** Der erste Lauf endete als
falsche Fertigmeldung – Bericht korrekt, Mailentwurf mit Namen, aber ohne E-Mail-Adresse der
Leitung; der Verifier kannte aus dem Auftrag nur den zitierten Betreff „Bericht …“ und das
`An:`. Ohne erfolgreichen ersten Lauf gibt es keinen Ablauf; die Wiederholungen liefen als
Planerläufe (✓ ✓, je 11 Aufrufe, 134 s) und sind keine Demo des Mechanismus.

**Nachlauf 1 (Code 725a484, `demo-x10-full-1.json`, 07:28–07:36):** Lauf 1 ✓ (12 Aufrufe,
143 s, Projekt „Polarstern“) → Kandidat mit 12 Schritten kompiliert (Bericht und Mail als
abgeleitete Schritte, beobachteter Fakt „Projektleitung“, drei Dateieingaben). Lauf 2 mit neuer
Ausgabe und korrigierter Projektleitung (`correct_memory`): Gate **repair** (Abdeckung 82 %,
Schritte 7–11; Gründe: „Fakt … wurde ersetzt“ und – zu Unrecht – „Eingabedatei bericht.md
fehlt“, obwohl Schritt 6 sie schreibt). Bericht neu abgeleitet (1 Aufruf, Nachbedingung
erfüllt), Rücklesen repariert (1 Aktion), dann **Reparatur des `memory.search`-Schritts durch den
Planer: er schrieb den Mailentwurf zu früh (ohne neue Adresse), las den Bericht dreimal, das
Reparaturbudget (4) war erschöpft → Rückfall auf den Planer → Schrittbudget erschöpft → ehrlich
`not_fulfilled`** (9 Aufrufe, 172 s, keine falsche Fertigmeldung). Lauf 3: der Ablauf war nach dem
gescheiterten Reparaturlauf zurückgestuft → vollständige Neuplanung (14 Aufrufe, 200 s, ✗: Betreff
fehlte). Ergebnis der Demo auf diesem Stand: **0/2 Wiederholungen**, Mechanismus nicht belegt.

**Behebung (Commit 0e6bdb4, nach der Messreihe):** (1) Dateien, die ein früherer Schritt des
Ablaufs schreibt, sind Zwischenergebnisse – weder Abhängigkeit noch Reparaturgrund (Compiler und
Gate). (2) Ein rein lesender Schritt mit ungültiger Abhängigkeit (`memory.search`, `files.read`,
…) wird **wörtlich erneut gelesen, ohne Modellaufruf**; ein abgeleiteter Schritt wird aus den
aktuellen Daten neu abgeleitet (ein Aufruf). Nur Schreibschritte mit festem Inhalt und
UI-Aktionen gehen weiter an den Planer (Budget 4). Skriptiert belegt:
`test_superseded_fact_repairs_only_dependent_steps` (jetzt 2 statt 3 Aufrufe, Suchanfrage
wörtlich wiederholt), `test_compiler_treats_files_written_by_the_workflow_as_intermediate`,
Gate-Fall in `test_reuse_gate_decisions`.

**Nachlauf 2 (behobener Stand, `demo-x10-full-2.json`, `demo-x6-full-2.json`):**

PLATZHALTER_DEMO_NEU

## 6. Migration an Bestandsdaten (echt, Kopie)

`migration-copy-check-0.5.0.json`: konsistente Kopie (SQLite-Backup-API, Quelle nur lesend
geöffnet, mtime der Quelle unverändert) des Datenordners der 0.3.0-Installation des Entwicklers
(1 Gespräch, 8 Nachrichten, 1 Knoten, 0 Kanten, 0 Rezepte, `user_version` 3) in einen Ordner mit
Leerzeichen/Umlaut; Start des 0.5-Entwicklungskerns ohne Modell: Schema 3 → 6 (inkl. 0.4-Profil-
Layout `data/profiles/<id>/`), Zähler unverändert, `quick_check ok`, Gedächtnis sichtbar (1 Fakt),
0 Abläufe (keine Rezepte vorhanden), Ausführungskern 1.5 gemeldet. Die Rezept-Übernahme selbst ist
mit `test_schema_v6_migrates_v5_database_with_recipes` und `test_recipes_are_migrated_to_candidate_workflows`
belegt (synthetische v5-Datenbank). Kernstart auf der Kopie 39,5 s (Entwicklungskern mit
Profil-Umzug und Hardwareerkennung; gepackt siehe Abschnitt 7).

## 7. Paket (echt)

`SpAIder-Foldable-0.5.0-win-x64.zip`, 402 217 361 Bytes, SHA-256
`3172f221ed672be049d129530b0d70a47963abdc2724c5126f562e7513b52ef0`, Build-Commit `02678fb`
(Manifest `BUILD_MANIFEST_0.5.0.json`; spätere Commits des Branches ändern nur Dokumente und
Messdateien). Kein Modell, keine Nutzerdaten, kein Browserprofil, keine Schlüssel im ZIP
(geprüft: 8 935 Einträge).

| Prüfung | Datei | Ergebnis |
|---|---|---|
| `verify_package.py` (frisch entpackt, Kern aus dem Paket, Health/Setup/Runtime) | Konsole | bestanden |
| erweiterte Paketprüfung `package_check_05.py` (Pfad mit Leerzeichen/Umlaut, PATH nur `C:\Windows`) | `package_check_0.5.0.json` | **37/37**: Version 0.5.0, 13 Presets, Profil-Layout, Ausführungskern 1.5 (Eingabesperre aktiv, 26 Werkzeuge, keine Shell), Aufgabe/Probe ohne Modell → 409, Abläufe leer, Bindungen anlegen/lesen/löschen, Stopp `lock_ms` 0,2, Beibringen außerhalb des Umfangs → 422, Schema v6 in frischem Profil, Tresor anlegen/sperren/falsches Passwort/entsperren, SIWC-Status „Einrichtung erforderlich“, Cloud-Planer ohne Freigabe → 409, Tunnel-Status, Entdecken (live), Connector + MCP-Brücke **aus dem Paket** (nur Gedächtniswerkzeuge, keine Computersteuerung), Profilwechsel-Isolation, keine Geheimnisse im Log, `core.json` entfernt; gepackter Kern bereit in 21,7 s |
| Desktop-Paket | `npm run package` | Electron 45.2.1, 0.5.0 |

Nicht geprüft am Paket: Agentenaufgabe mit Modell aus dem Paket (die Messreihe lief gegen den
Entwicklungskern mit demselben Code; der 0.3-Paketvolltest mit Modell wurde nicht wiederholt).

## 8. Leistungsziele

| Ziel | Messung | Ergebnis |
|---|---|---|
| Overlay p95 ≤ 200 ms | 0.3-Messung Launcher (echt, `AGENT_LIVE_PERFORMANCE.md`): Hotkey → erster Paint p50 63 ms, max 104 ms | in 0.5 nicht erneut gemessen (Overlay unverändert) |
| Sperre weiterer Eingaben nach Stopp p95 ≤ 100 ms | `test_stop_locks_follow_up_actions_fast_p95`, `POST /agent/stop` `lock_ms` | **erreicht im Kern**: In-Prozess p95 1,2 ms (40 Stopps), Paket `lock_ms` 0,2 ms; Hotkey → Stopp über den Launcher nicht neu gemessen (0.3: Overlay-Messung) |
| ≥ 50 % weniger Modellaufrufe, ≥ 30 % weniger Dauer bei gleicher Qualität (Wiederholungen) | Abschnitt 4 | Datei + Browser: Aufrufe **−62 %**, Dauer **−53 %** (Ziele erreicht); Qualität 88 % vs. 90 % mit 5 falschen Wiederverwendungen → „gleiche Qualität“ **nicht ganz** erreicht; Fenster/Cross nur Teildaten |

## 9. Nicht verifiziert / nicht durchgeführt

- Zwei-PC-/USB-Transfer physisch (ein Rechner); Ersatz: automatisierte Umzugstests + Anleitung.
- Echte Cloud-Anbieter (0 €): native Tool-Calls nur gegen Fake-Adapter und lokale Probe.
- Browser-Recorder (Beibringen): echt geprüft mit eigener Testseite im Dev-Kern
  (`test_real_browser_recorder_compiles_a_candidate`: Klick/Eingabe/Auswahl aufgezeichnet,
  Kandidat mit Rolle+Name-Zielen kompiliert); Fenster-Diff nur mit Fake-Snapshots (`test_teach.py`).
- **Fensteraufgaben mit Vordergrund/Eingabe (W1, W4, W6, W8, W9, W10, W12–W15, X1, X3, X5, X7)
  konnten in der Nacht nicht gemessen werden:** die Sitzung stand nach dem Leerlauf-Timeout auf dem
  Windows-Sperrbildschirm (LockApp im Vordergrund). Der Kern erkannte das zunächst nicht
  (Eingabe-Desktop meldet „Default“) und meldete `windows.focus` ehrlich als gescheitert; seit
  Commit e2b8257 gilt der Sperrbildschirm als nicht interaktiv (Pause), und die Harness überspringt
  Fensteraufgaben, solange die Sitzung gesperrt ist (`skipped: Sperrbildschirm`). Die Teildaten
  des ersten Fensterlaufs (UIA-Aufgaben W2, W3, W5, W7 erfolgreich wiederverwendet; Vordergrund-
  Aufgaben gescheitert) stehen in `matrix2-full-windows-cross.locked1.json`. Eine Messung der
  Vordergrund-Aufgaben erfordert eine entsperrte, unbenutzte Sitzung (z. B. tagsüber mit
  `--min-idle 120`).
- Die Sicherheitsrichtlinie (Sperre nach Leerlauf) wurde nicht verändert.
- Sperr-Erkennung: Einmal (07:26) meldete `session_locked()` kurz „nicht gesperrt“, obwohl die
  Sitzung seit Stunden gesperrt war (LockApp war in diesem Moment nicht Vordergrundfenster, z. B.
  Bildschirm aus); eine Messharness brach deshalb ab. Der Kern pausiert in so einem Moment nicht,
  sondern führt UIA-Schritte aus (ungefährlich, aber unbeabsichtigt). Abhilfe (offen): zusätzlich die
  WTS-Sitzungsflags (`WTSQuerySessionInformation`, `WTSSessionInfoEx`) abfragen, wie es die
  Harness `tail6_baseline.py` tut.
- Fenster/Bild-Baseline (219828a, `baseline-wv-qwen3-4b-219828a.json`): lief erst nach der
  Messreihe und ebenfalls hinter dem Sperrbildschirm (3/15, nur W2). Der 0.3-Agent versucht bei
  W3 `windows.focus` + Klick und scheitert dort, wo 0.5 per UIA-`invoke` ohne Vordergrund arbeitet
  (W3 in `locked1`: 7/0/0 Aufrufe) – ein Hinweis, kein Vergleich unter gleichen Bedingungen.
  Beide Baselines (Datei+Browser 27/30; Fenster) sind mit dem 0.3-Harness `tests/matrix/run_matrix.py`
  auf dem Basis-Commit reproduzierbar; die Fensterbaseline braucht eine entsperrte, unbenutzte Sitzung.
