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
