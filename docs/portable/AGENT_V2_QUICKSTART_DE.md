# SpAIder Foldable 0.5 – Einstieg und 20-Minuten-Testanleitung (Agent v2)

Stand: 2026-11-12. Für den Review-Prerelease `foldable-0.5.0-review`. Voraussetzungen:
Windows 11 x64, ZIP entpackt (USB oder Festplatte), kein Docker/Python/Node nötig.
Alles läuft lokal; Cloud-Anbieter nur, wenn du selbst Schlüssel hinterlegst.

## Was ist neu in 0.5 (in einem Absatz)

SpAIder erledigt Computeraufgaben (Dateien, eigener Browser, freigegebene Fenster) und
**prüft das Ergebnis selbst** („erfüllt / nicht erfüllt / ungeklärt“ mit Belegen). Eine
erfolgreich erledigte Aufgabe lässt sich als **Ablauf** speichern: SpAIder übersetzt die
Schritte in einen parametrisierten Plan mit Abhängigkeiten auf Dateien und
Gedächtnisfakten. Beim nächsten Mal läuft der Ablauf **ohne Modellaufrufe** (nur Schritte,
deren Inhalt aus Daten entsteht, brauchen einen Aufruf), **repariert** nur die Schritte,
deren Grundlage sich geändert hat (z. B. neue Projektleitung im Gedächtnis), oder plant
neu. Abläufe und ihre Abhängigkeiten liegen im Profil und wandern mit dem USB-Stick;
Ordner, Fenster und Programme werden auf jedem PC neu gebunden. Zusätzlich: „Ablauf
beibringen“ (Demonstration im eigenen Browser oder in einem Fenster), Eingabesperre
(Nutzereingaben pausieren die Steuerung), Stopp unabhängig vom Modell.

## Fünf Schritte zum Ausprobieren (ca. 20 Minuten)

Lege vorher einen Testordner an, z. B. `C:\SpAIder-Test\Projekt Atlas` mit
`notizen.txt` („Projekt Atlas: Kickoff am 03.12.2026. Budget: 42000 EUR.
Projektleitung: Priya Nair.“) und `kontakte.csv` (`name,rolle,email` +
`Priya Nair,Leitung,priya.nair@beispiel.test`), sowie einen leeren Ordner
`C:\SpAIder-Test\Ausgabe`. Starte `SpAIder\SpAIder.exe`, aktiviere unter Einstellungen →
Modelle ein Chatmodell (Qwen3-4B empfohlen; Qwen3-0.6B reicht für die Mechanik, antwortet
aber schwach).

1. **Aufgabe erledigen.** Aufgaben → Neue Aufgabe. Ziel: „Lies die Projektdateien in
   `C:\SpAIder-Test\Projekt Atlas`, erzeuge `C:\SpAIder-Test\Ausgabe\bericht.md` mit
   Kickoff, Budget und Projektleitung und bereite `C:\SpAIder-Test\Ausgabe\mail_entwurf.txt`
   vor (An: E-Mail der Projektleitung aus kontakte.csv, Betreff „Bericht Atlas“). Kein
   Versand.“ Zugriffe: Dateien (Ordner `Projekt Atlas`, Schreibordner `Ausgabe`),
   Gedächtnis an. Starten. Beobachte „Tatsächliche Schritte“ (Datei gelesen …, Datei
   geschrieben und zurückgelesen …, Ergebnis geprüft: erfüllt) und das Ergebnisfeld mit
   den anklickbaren Belegen (Datei öffnen / im Ordner zeigen).
2. **Als Ablauf speichern.** Im Aufgabendetail „Als Ablauf speichern“, Name
   „Atlas-Bericht“, optional Parameter (z. B. `leitung` = Priya Nair). Unter Aufgaben →
   Abläufe erscheint der Kandidat mit Schritten, abgeleiteten Schritten (Bericht, Mail)
   und Abhängigkeiten (Dateien, Fakten, Werkzeuge).
3. **Mit neuen Daten wiederholen.** Ändere `notizen.txt` (anderes Budget) und starte den
   Ablauf („Starten“, Parameter eintragen, Ordner bleiben). Erwartung: Lese-Schritte ohne
   Modellaufruf, Bericht und Mail werden neu abgeleitet (je ein Aufruf), Ergebnis
   geprüft. Vergleiche „Modellaufrufe“ und Dauer mit Schritt 1.
4. **Projektleitung ändern → Reparatur.** Im Chat schreiben: „Korrektur: Die
   Projektleitung von Atlas ist jetzt Jonas Beispiel (jonas.beispiel@beispiel.test).“
   (oder über einen Gedächtnis-Connector `correct_memory`). Ändere auch die Zeile in
   `kontakte.csv`. Unter Abläufe → „Prüfen“: Das Gate meldet „Fakt … wurde ersetzt“ und
   „wird repariert“ mit den betroffenen Schritten. Starten: Nur die Empfängerzuordnung
   (Gedächtnissuche + Mailentwurf) wird repariert, der Bericht wird nach Prüfung
   wiederverwendet. Der Mailentwurf trägt die neue Adresse.
5. **Ablauf beibringen und Stopp.** Aufgaben → Ablauf beibringen → Quelle Browser, Adresse
   einer eigenen Testseite (z. B. `file:///C:/SpAIder-Test/seite.html` mit einem Formular;
   Ordner als lokale Freigabe), Aufnahme starten, Felder ausfüllen, Knopf klicken, „Fertig“
   → Kandidat; „Prüflauf starten“. Während eines Laufs mit Fensterzugriff: Maus bewegen →
   die Aufgabe pausiert („Nutzereingabe erkannt“), „Weiter“ setzt fort. Stopp-Hotkey
   (Standard Strg+Alt+Rücktaste) beendet sofort, auch während ein Modellaufruf läuft.

## Was du sehen solltest – und was nicht

* „fertig“ gibt es nur mit Prüfung: ein Ergebnis ohne Belege heißt „ungeklärt“ und ist
  so gekennzeichnet; ein widerlegtes Ergebnis heißt „nicht erfüllt“ (Aufgabe
  fehlgeschlagen, Ursache angegeben).
* Kein Ablauf wird automatisch wiederverwendet, wenn Abhängigkeiten ungültig sind, der
  Werkzeugvertrag sich geändert hat oder der Ablauf zurückgestuft wurde.
* Wirkungen nach außen (senden, kaufen, löschen) bleiben Rückfragen; freigegebene
  Routineaktionen (lesen, im Schreibordner schreiben, freigegebene Fenster bedienen)
  fragen nicht nach.
* Ein Ablauf erweitert nie Rechte: Zugriffe kommen immer aus der Aufgabenanlage.

## Entwickler

```
cd foldable/core && pytest tests            # 154 bestanden, 6 übersprungen (ohne Modell)
cd foldable/desktop && npm ci && npm test   # Desktop-Tests
python tests/matrix/run_matrix_v2.py --model <gguf> --catalog-id qwen3-4b-q4_k_m --groups files,browser --mode full
```

Dokumente: `AGENT_V2_ARCHITECTURE.md` (Kern, Abgrenzung), `AGENT_V2_TEST_REPORT.md`
(Messungen, Baseline, drei Vergleichsarme, Pflichttests), `AGENT_V2_MIGRATION.md`
(Schema v6, Umzug), `API_CONTRACT.md` Addendum v1.5, `KNOWN_LIMITATIONS.md` Abschnitt 0.5.
