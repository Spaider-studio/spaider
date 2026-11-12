# SpAIder Foldable 0.5 (Agent v2): Test- und Leistungsbericht

Stand: 2026-11-12. Branch `feature/spaider-foldable-agent-v2`. Hardware: Intel Core Ultra
(Arc 140V, Vulkan), Windows 11, ein Rechner. Modell für alle echten Läufe: Qwen3-4B Q4_K_M
(`qwen3-4b-q4_k_m`, llama.cpp b12032, Vulkan). Alle Zahlen stammen aus den JSON-Dateien
unter `foldable/build/measurements/` bzw. werden dort genannt; **nichts ist geschätzt**.
Mocks und Skripttests sind getrennt ausgewiesen.

Einstufung: **echt** = laufender Kern + echtes Modell + unabhängige Prüfung des Endzustands;
**skriptiert** = Loop/Gate/Compiler mit skriptiertem Planer ohne llama-server;
**Mock** = Oberfläche gegen Mock-Server.

## 1. Baseline vor den Änderungen (reproduzierbar)

Basis-Commit 219828a (PR #60, Agent 0.3/0.4), gleiche Hardware, gleiches Modell, gleiche
Aufgaben (`tests/matrix/tasks.json`, Seed 7), 3 Wiederholungen, Zeitbudget 180 s, Schrittbudget 14.

| Lauf | Datei | Ergebnis |
|---|---|---|
| Datei + Browser (10 Aufgaben × 3) | `baseline-fb-qwen3-4b-219828a.json` | **27/30**, 1 Fehlerfolge-Meldung, 0 Rückfragen, Median 31,6 s, Modellaufrufe = Schritte (jeder Schritt ein Aufruf) |
| Fenster + Bild (10 Aufgaben × 3) | `baseline-wv-qwen3-4b-219828a.json` | PLATZHALTER_BASELINE_WV |

Die Baseline hat keinen Ablauf-Mechanismus außer 0.3-Rezepten; jede Wiederholung ist ein
Planerlauf. Sie ist der Vergleichswert für „unbekannte Aufgabe“ und für den Arm „ohne
Wiederverwendung“.

## 2. Unit- und Integrationstests (skriptiert, ohne Modell)

`cd foldable/core && pytest tests` → **156 bestanden, 7 übersprungen** (die übersprungenen sind Echtläufe, die `SPAIDER_FOLDABLE_REAL_TESTS=1` bzw. den Chromium-Ordner brauchen; mit `SPAIDER_FOLDABLE_BROWSER_DIR` läuft zusätzlich der echte Browser-Recorder-Test). Desktop: `npm test` → **123 bestanden (14 Dateien)**, `npm run typecheck` und `npm run lint` ohne Befund.

Neu in 0.5 (`tests/test_agent_v2.py`, `tests/test_agent_v2_api.py`):

| Pflichttest (Auftrag) | Test | Ergebnis |
|---|---|---|
| Stopp mit ausstehendem Tool-Call | `test_stop_with_pending_native_tool_call_executes_nothing`, `test_cancel_blocks_further_actions` (0.3) | bestanden: kein Aufruf wird nach dem Stopp ausgeführt, Adapter abgebrochen |
| Stopp-Latenz (Sperre neuer Aktionen) | `test_stop_locks_follow_up_actions_fast_p95` (40 Aufgaben) | p95 < 100 ms (In-Prozess; Werte im Testlauf PLATZHALTER_STOP_P95) |
| veralteter Zustand | `test_stale_ui_target_is_not_executed_blindly` + Broker-Frischeprüfung (0.3) | bestanden: Ziel nicht in aktueller Beobachtung → neu lesen → Übergabe, keine Ausführung mit alter ID |
| Profilwechsel | `test_profile_switch_epoch_cancels_everything_and_ignores_late_results` | bestanden: Epoche erhöht, laufende Aktion `unknown_outcome`, keine Folgeplanung |
| Prompt-Injection + harmlose Kontrollaufgabe | `test_injected_instructions_cannot_grant_external_effects`; echt: B5/B12 der Matrix | bestanden: `mail.send` braucht Freigabe (abgelehnt → journaliert), Betrag korrekt geschrieben; Matrix siehe Abschnitt 4 |
| App-Ausfall | `test_app_failure_is_reported_not_crashed` | bestanden: `source_lost` → dreimal gescheitert → Aufgabe sauber `failed`, Ursache sichtbar |
| Neustart | `test_restart_marks_running_tasks_interrupted_and_external_outcomes_unknown`, `test_resume_after_unknown_external_outcome_warns_instead_of_repeating` | bestanden: `interrupted`/`unknown_outcome`; Fortsetzen warnt „nicht blind erneut senden“ |
| Memory-Korrektur | `test_superseded_fact_repairs_only_dependent_steps`, `test_dependency_invalidation_is_transitive_and_keeps_history`; echt: F13/X4/X6/X10 (`fact_change`) | bestanden: ersetzter Fakt → nur abhängige Schritte repariert, Bericht wiederverwendet; Historie bleibt |
| Offline | `tests/test_real_offline.py` (0.3, echt) | PLATZHALTER_OFFLINE |
| CPU-Rückfall | `tests/test_real_gpu_fallback.py` (0.3, echt) | PLATZHALTER_CPU |
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
(Rolle+Name, Commit 333e9cf); die Messreihe lief danach mit diesem Stand.

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

PLATZHALTER_MATRIX_TABELLEN

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

PLATZHALTER_DEMO

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

`SpAIder-Foldable-0.5.0-win-x64.zip`, 402 217 929 Bytes, SHA-256
`23e347829ae8973d1e5558e004d219353be9c727cd1cc296c5dd25083aaa1906`, Build-Commit `a6e2d83`
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
| Sperre weiterer Eingaben nach Stopp p95 ≤ 100 ms | `test_stop_locks_follow_up_actions_fast_p95`, `POST /agent/stop` `lock_ms` | PLATZHALTER_STOP |
| ≥ 50 % weniger Modellaufrufe, ≥ 30 % weniger Dauer bei gleicher Qualität (Wiederholungen) | Abschnitt 4 | PLATZHALTER_ZIELE |

## 9. Nicht verifiziert / nicht durchgeführt

- Zwei-PC-/USB-Transfer physisch (ein Rechner); Ersatz: automatisierte Umzugstests + Anleitung.
- Echte Cloud-Anbieter (0 €): native Tool-Calls nur gegen Fake-Adapter und lokale Probe.
- Browser-Recorder (Beibringen) nur im Dev-Kern mit eigener Testseite geprüft (PLATZHALTER_TEACH).
- Fensteraufgaben liefen unter Idle-Gating (nur bei Inaktivität des Nutzers); Läufe, die durch
  Nutzereingaben pausiert wurden, sind im JSON als solche erkennbar (`events` enthält „pausiert“).
