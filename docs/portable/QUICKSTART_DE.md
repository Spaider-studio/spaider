# SpAIder Foldable: Schnellstart

Für Windows 11 (x64). Es wird nichts installiert; alles bleibt im SpAIder-Ordner.

## 1. Entpacken

ZIP `SpAIder-Foldable-<version>-win-x64.zip` auf den USB-Datenträger entpacken.
Es entsteht ein Ordner `SpAIder` mit `SpAIder.exe`. Der Ordnername darf
Leerzeichen und Umlaute enthalten. Ein Datenträger mit NTFS oder exFAT wird
empfohlen; auf FAT32 sind Modelldateien über 4 GB nicht möglich (die App warnt).

**Pfadlänge.** Entpacke in einen kurzen Pfad (z. B. `D:\SpAIder` oder
`C:\Users\<Name>\SpAIder`). Das Paket enthält tief verschachtelte Dateien; in
sehr langen Zielpfaden (über ≈ 100 Zeichen) bricht das Entpacken unter
Windows ohne Long-Path-Unterstützung mit „Datei nicht gefunden“ ab.

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
  Eine Korrektur wirkt am zuverlässigsten mit einem klaren Marker, z. B.
  „Korrektur: Die Projektleiterin ist jetzt Mara Muster, nicht mehr Lena
  Beispiel.“ Dann wird die alte Angabe unabhängig vom Modell sofort als
  ersetzt markiert. Reine Fragen werden nicht als Fakten gespeichert.
- **Kurz messen**: Für das aktive Modell misst ein kurzer, abbrechbarer Test
  Token/s und Zeit bis zum ersten Token auf diesem Rechner; nur diese echten
  Werte erscheinen in Empfehlungen.

## Neu in Version 0.3: Live-Bildschirmhilfe und Agent

**Live-Fenster (Hotkey).** `Strg+Alt+Leertaste` (änderbar unter Einstellungen →
Live & Agent; ist die Kombination schon belegt, zeigt „Prüfen“ das an – dann
z. B. `Strg+Alt+F9` wählen) öffnet ein kleines Fenster über der aktuellen Anwendung. Es schlägt
das Fenster vor, das gerade im Vordergrund war; beim ersten Mal bestätigst du die
Quelle (oder wählst ein anderes Fenster bzw. einen Bildschirm). Dann fragst du
direkt: „Was steht hier?“, „Was bedeutet dieser Fehler?“, „Nutze unser
Projektwissen für einen Entwurf“. Die Antwort nutzt die lesbare Struktur des
Fensters und – wenn ein Bildmodell aktiv ist – ein aktuelles Bild. Beobachten
löst keine Aktionen aus. `Esc` schließt das Fenster; die Antworten stehen auch im
Hauptfenster im Gespräch „Live: …“. SpAIder-eigene Fenster werden nie aufgenommen.

**Bildmodell.** Für Bildfragen und den Agenten ist „Qwen3-VL 4B Instruct
(Q4_K_M + Bild-Projektor Q8_0)“ die geprüfte Wahl (Text + Bilder, ≈ 2,8 GB).
Die Startseite „Für diesen Computer empfohlen“ schlägt reine Textmodelle vor;
das Bildmodell findest du unter **„Weitere Modelle“** – laden, aktivieren,
fertig (Gewichte und Projektor werden zusammen installiert). Mit einem reinen
Textmodell (z. B. Qwen3 4B) funktionieren Live-Fragen nur über die
Fensterstruktur, und `vision.*`-Werkzeuge fehlen dem Agenten; das wird im
Live-Fenster angezeigt.

**Aufgaben (Agent).** Unter „Aufgaben“ beschreibst du, was erledigt werden
soll, und gibst den Umfang frei: Ordner (lesen) und Schreibordner, eigener
Browser, Fenster (Auswahl) – mehr darf der Agent nicht. Er arbeitet Schritt für
Schritt mit sichtbarem Fortschritt, prüft jedes Ergebnis (Datei zurückgelesen,
Feldwert, Dialogzustand) und fragt nur bei folgenreichen Wirkungen (senden,
veröffentlichen, kaufen, löschen) nach. „Stopp“ sperrt sofort weitere Aktionen.
Erfolgreiche Aufgaben kannst du als Ablauf speichern und später mit anderen
Parametern starten.

**Eigener Browser.** Für Web-Aufgaben lädt SpAIder einmalig ein eigenes,
geprüftes Chromium-Paket (≈ 207 MB) in den Datenordner; dein normaler Browser,
seine Anmeldungen und Profile bleiben unberührt.

**Stoppen und Beenden.** `Strg+Alt+Rücktaste` stoppt alles sofort (Antwort,
Aufgabe, Aufnahme). „Sicher beenden“ bricht laufende Aufgaben an einer sicheren
Grenze ab, schließt Browser und Modell und gibt den Datenträger frei; mit
Tray-Modus bleibt SpAIder im Infobereich.
