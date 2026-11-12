# SpAIder Foldable 0.5: Migration, Portabilität, Umzug auf einen anderen PC

Stand: 2026-11-12. Ergänzt `PORTABLE_AGENT_STATE.md` (0.3) und den Profilabschnitt in
`MODELHUB_ARCHITECTURE.md` (0.4).

## 1. Schema v5 → v6 (automatisch beim ersten Start)

Die Migration ist additiv und läuft in einer Transaktion (`schema_v6.sql`,
`PRAGMA user_version = 6`):

| Neu | Inhalt |
|---|---|
| `agent_workflows` | Abläufe (VerifiedWorkflow als JSON) mit Status, Zählern, Negativprüfung |
| `agent_dependencies` | Abhängigkeiten je Ablauf/Schritt mit Revision, Herkunft, Gültigkeit, Grund |
| `agent_bindings` | Rollenbindungen je Rechner (`host_id`) |
| `agent_workflow_runs` | Entscheidungen des Gates und Ergebnis je Lauf |
| `agent_tasks` | + `success_json`, `outcome`, `outcome_json`, `workflow_id`, `project_id`, `connection_id`, `reuse_json` |
| `agent_actions` | + `call_id`, `origin` |

Bestehende Tabellen werden nicht geändert oder gelöscht; `agent_recipes` (0.3) bleibt.
Beim Start übernimmt der Kern jedes 0.3-Rezept **einmalig** als Kandidaten-Ablauf mit
derselben ID (`origin_kind = migrated`, ohne Lese-/Schreibmengen und Faktenabhängigkeiten,
daher immer „streng“ geprüft). Ein älterer 0.3/0.4-Kern kann die Datenbank weiterhin
öffnen (er kennt die neuen Tabellen nicht und ignoriert sie); `PRAGMA user_version`
steht dann auf 6.

**Geprüft an einer Kopie:** siehe `AGENT_V2_TEST_REPORT.md` („Migration“) – v5-Datenbank mit
Rezept und Aufgabe → v6, `quick_check ok`, Zähler unverändert, Rezept als Kandidat
sichtbar. Das Original wird nie angefasst; die Prüfung läuft auf einer per
SQLite-Backup-API erzeugten Kopie.

## 2. Was portabel ist und was nicht

| Portabel (im Profil) | Rechnergebunden (wird neu aufgelöst) |
|---|---|
| Abläufe mit Parametern, Prüfungen, Quellen (Herkunft), Versionen | Bindungen (Ordner, Fenster, Programme) – `agent_bindings` ist per `host_id` getrennt |
| Abhängigkeiten auf Gedächtnisfakten (Knoten-IDs) und Dateien (Rollenpfade + SHA-256) | Fensterhandles, Elementreferenzen, Bildkoordinaten – nie gespeichert, immer frisch |
| Laufhistorie, Journal, Ergebnisberichte | Freigaben/Umfang – kommen bei jeder Aufgabe aus der Anlage auf diesem PC |
| Tresor (0.4) – unverändert | Hostschlüssel (DPAPI), Browser-Sitzungen, OAuth-Token (nur Sitzung/Host) |
| Modelle, Gedächtnis (ein SQLite, kein zweites Gedächtnis) | Hardwareprofil, Laufzeitoptionen |

## 3. Umzug auf einen anderen PC (manuelle Anleitung, 10 Minuten)

Ein physischer Zwei-PC-Transfer wurde in dieser Lieferung **nicht** durchgeführt
(ein Rechner). Die folgenden Schritte sind das, was automatisiert geprüft ist
(Umzugstests in `tests/test_real_e2e.py` Fall „anderes Laufwerk“ aus 0.3, Bindungstest
`test_binding_resolver_rebinds_roles_on_this_host_only`, Hostwechsel-Tests für
unterbrochene Aufgaben) – bitte beim ersten echten Transfer protokollieren:

1. **Quell-PC:** SpAIder beenden (Tray → Beenden; der Lock wird entfernt). Den gesamten
   Ordner `SpAIder-Foldable-0.5.0-win-x64\SpAIder` (inkl. `data`) auf den USB-Datenträger
   kopieren bzw. dort belassen.
2. **Ziel-PC:** `SpAIder.exe` vom Datenträger starten (SmartScreen „Trotzdem ausführen“).
   Erwartung: Kern bereit, Profil erkannt, Modelle vorhanden, Gedächtnis sichtbar,
   Abläufe unter „Aufgaben → Abläufe“ sichtbar (Status unverändert).
3. **Abläufe → Prüfen:** Für jeden Ablauf zeigt „Prüfen“ die Entscheidung des Gates auf
   diesem Rechner. Typisch: `Rolle „root_…“ ist auf diesem Rechner nicht gebunden`.
   Das ist richtig – Bindungen des alten PCs werden nicht übernommen.
4. **Starten:** Beim ersten Start fragt die Aufgabe je fehlender Rolle nach dem Ziel
   (Ordner wählen, Fenster wählen). Die Antwort wird für diesen Rechner gespeichert und
   bei jedem weiteren Start neu geprüft (Ordner existiert, liegt im freigegebenen
   Umfang).
5. **Erwartetes Verhalten:** Schritte ohne Modell laufen wie zuvor; abgeleitete Schritte
   brauchen einen Modellaufruf; Fakten werden über ihre IDs geprüft (unverändert gültig,
   da das Gedächtnis mitgezogen ist); Werkzeugverträge über Hashes (gleiche Version →
   gültig). Unterbrochene Aufgaben des alten PCs stehen auf „unterbrochen (auf einem
   anderen Rechner)“ und sind dort nicht fortsetzbar – neu anlegen.
6. **Protokoll:** Bitte festhalten: Startzeit bis „bereit“, Anzahl Rückfragen beim ersten
   Ablaufstart, Ergebnis des Laufs, auffällige Unterschiede (Pfadnamen, Fenster). Diese
   Angaben fehlen in der Lieferung und sind der offene Nachweis.

## 4. Datensicherheit bei der Migration

* Vor dem ersten Start einer neuen Version sichert der Kern nichts automatisch; die
  Migration ist additiv und transaktional. Empfohlen: Kopie von `data\profiles\<id>\spaider.db`
  (bei beendeter App) vor dem Update.
* Fehlschlag der Migration → Transaktion wird zurückgerollt, die alte Version bleibt
  lauffähig; der Fehler steht in `data\logs\core.log`.
* Es gibt keine zweite Datenbank und keinen zweiten Gedächtnisstand: Abläufe,
  Abhängigkeiten und Gedächtnis liegen in derselben Datei und werden gemeinsam
  mitgenommen.
