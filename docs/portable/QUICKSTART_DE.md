# SpAIder Foldable: Schnellstart

Für Windows 11 (x64). Es wird nichts installiert; alles bleibt im SpAIder-Ordner.

## 1. Entpacken

ZIP `SpAIder-Foldable-<version>-win-x64.zip` auf den USB-Datenträger entpacken.
Es entsteht ein Ordner `SpAIder` mit `SpAIder.exe`. Der Ordnername darf
Leerzeichen und Umlaute enthalten. Ein Datenträger mit NTFS oder exFAT wird
empfohlen; auf FAT32 sind Modelldateien über 4 GB nicht möglich (die App warnt).

## 2. Starten

`SpAIder.exe` doppelklicken. Beim ersten Start wird `data/` angelegt. Windows
SmartScreen kann wegen der fehlenden Signatur nachfragen („Trotzdem ausführen“).

## 3. Modell vorbereiten (einmalig, braucht Internet)

Im Einrichtungsbildschirm ein Modell wählen. Die App zeigt Größe, Lizenz und ob
es zu RAM und freiem Speicher dieses Rechners passt. „Herunterladen“ startet
den Download von Hugging Face direkt in `data/models/`; Fortschritt, Abbruch
und Fortsetzen sind in der App. Die Prüfsumme wird nach dem Download geprüft.
Optional: das Embedding-Modell für die semantische Gedächtnissuche (empfohlen,
639 MB). Alternativ eine eigene GGUF-Datei über „Eigene GGUF-Datei importieren“
einspielen; sie wird geprüft und nach `data/models/` kopiert.

Nach dem Download wird das Modell geladen und getestet; danach öffnet sich der
Chat.

## 4. Offline arbeiten

Chatten, Dateien (TXT, Markdown, textbasierte PDF) importieren, Erinnerungen in
den Einstellungen pflegen. Alles bleibt auf dem Datenträger. Nach der
Vorbereitung ist keine Internetverbindung mehr nötig; die App baut von sich aus
keine Verbindung auf.

## 5. Beenden und Datenträger wechseln

In der Seitenleiste „Sicher beenden“ wählen. Die App stoppt laufende Antworten
und Modellprozesse, schließt die Datenbank und beendet sich. Erst wenn das
Fenster geschlossen ist, den Datenträger über „Hardware sicher entfernen“
abmelden und abziehen.

Am anderen Rechner: Datenträger anstecken, `SpAIder.exe` starten. Modell,
Gespräche, importierte Dateien und Erinnerungen sind vorhanden; kein erneuter
Download. Die Geschwindigkeit hängt vom jeweiligen Rechner ab.

## Hinweise

- Zweimal starten: Ein zweiter Start mit demselben Ordner wird abgelehnt,
  solange der erste läuft.
- Nach einem Absturz startet die App normal; eine unterbrochene Antwort wird
  als „abgebrochen“ markiert, unterbrochene Downloads können fortgesetzt werden.
- Logs liegen unter `data/logs/`.

## Neu in Version 0.2

- **Empfehlungen**: Unter Einstellungen → Modelle zeigt „Für diesen Computer
  empfohlen“ bis zu drei Modelle mit Begründung, Downloadgröße, geschätztem
  Speicherbedarf und Prüfstatus. Der Schwerpunkt (Alltag, Dokumente,
  Coding-Fragen) ist umschaltbar. „Weitere Modelle“ sucht auf Hugging Face oder
  prüft eine eingegebene Repository-ID; Treffer sind so lange „ungeprüft“, bis
  der Dateikopf gelesen wurde. „Lokales Modell importieren“ bleibt.
- **Aktivität**: Unter der entstehenden Antwort zeigt eine Zeile den aktuellen
  Schritt (Gedächtnissuche, Kontextverarbeitung in Prozent, Antwort wird
  geschrieben, gespeichert, Nachverarbeitung). „Details“ listet die Ereignisse.
- **Leistung**: Einstellungen → Leistung: „Automatisch“ (Standard), „Leise“,
  „Schnell“. Eine erkannte Vulkan-fähige GPU wird automatisch genutzt; schlägt
  das fehl, läuft dieselbe Sitzung auf der CPU weiter. Keine Thread- oder
  Batch-Einstellungen nötig.
- **Gedächtnis**: Neue Angaben und Korrekturen stehen sofort für die nächste
  Antwort bereit; die Nachverarbeitung (Entitäten, Ersetzungen, Vektoren)
  läuft danach im Hintergrund und wird im Gespräch als „ausstehend“ bzw.
  „aktualisiert“ angezeigt. Chat-Antworten haben Vorrang vor Hintergrundarbeit.
- **Kurz messen**: Für das aktive Modell misst ein kurzer, abbrechbarer Test
  Token/s und Zeit bis zum ersten Token auf diesem Rechner; nur diese echten
  Werte erscheinen in Empfehlungen.
