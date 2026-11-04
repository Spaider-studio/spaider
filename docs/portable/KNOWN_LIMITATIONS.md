# SpAIder Foldable: Bekannte Einschränkungen (V1)

Stand: 2026-11-02. Ehrliche Liste dessen, was V1 nicht oder nur eingeschränkt
kann. Offene Fehler stehen am Ende.

## Plattform und Paket

- Nur Windows 11 x64. macOS/Linux sind nicht gebaut; die Pfad-, Lock- und
  Runtime-Logik ist vorbereitet, aber ungetestet dort.
- Nur CPU-Inferenz (llama.cpp CPU-Build). Eine iGPU/dGPU wird nicht genutzt.
  Vulkan-/CUDA-Builds sind als `--variant` in `fetch_runtime.py` vorgesehen,
  aber in V1 nicht gebündelt oder getestet.
- Das Paket ist nicht signiert. Windows SmartScreen zeigt beim ersten Start eine
  Warnung. Unternehmens-Richtlinien (AppLocker, Wechseldatenträger-Sperre) können
  den Start verhindern; das liegt außerhalb der App.
- Der Build-Rechner hat keinen C-Compiler. Alle nativen Teile (llama.cpp,
  Python-Wheels, Electron) sind vorgebaute, gepinnte Binaries.
- Paketgröße: Electron (~200 MB) + Python-Kern mit litellm/pdfplumber (~310 MB)
  + Runtime (~45 MB). Modelle kommen beim Nutzer hinzu.

## Modelle und Inferenz

- Getestet (echte Inferenz) wurde nur `Qwen/Qwen3-0.6B-GGUF` (Q8_0) als Chat-
  und `Qwen/Qwen3-Embedding-0.6B-GGUF` (Q8_0) als Embedding-Modell. Alle anderen
  Katalogeinträge (Qwen3 1.7B/4B/8B, Qwen3.8-27B) sind nur „dokumentiert“:
  Architektur vom gepinnten Build gelistet, Prüfsumme und Lizenz geprüft, nicht
  ausgeführt.
- Qwen3.8: Qwen veröffentlicht keine GGUF-Datei. Der Katalog führt eine
  Community-Konvertierung (unsloth, UD-Q4_K_M, 16,5 GB) mit Kennzeichnung. Auf
  reiner CPU ist ein 27B-Modell sehr langsam; ohne 20+ GB RAM meldet die App
  „passt nicht“. `Qwen3.8-Flash-Next` (Lizenz „other“) ist nicht aufgenommen.
- Das kleine Testmodell liefert schwache Extraktionen: Es kopiert gelegentlich
  Beispieltext aus dem Extraktionsprompt („Electric vehicle manufacturer“). Ein
  Guard verwirft solche nicht durch den Eingabetext gestützten Attribute und
  stuft sie als „unsicher“ ein; die Entität bleibt. Mit 4B+ Modellen ist die
  Qualität deutlich besser, wurde hier aber nicht gemessen.
- Thinking-Modus (Qwen3) ist standardmäßig aus; mit 0.6B verbraucht das Denken
  das gesamte Antwortbudget. Thinking-Text wird als eigenes SSE-Ereignis
  gestreamt und nicht gespeichert.
- Kontextfenster: Standard 8192 Tokens (Einstellung bis 131072, aber RAM/Zeit
  wachsen entsprechend; das Modellmaximum wird nicht automatisch erzwungen).
  Kein „unbegrenzter“ Kontext: Verlauf und Gedächtnisblock werden auf das Budget
  gekürzt.
- Geschwindigkeit ist hardwareabhängig und wird nicht garantiert; gemessene
  Werte des Entwicklungsrechners stehen in `TEST_REPORT.md`.
- Ein Chat-Slot: Während eine Antwort läuft, wartet die Gedächtnis-Extraktion;
  während die Extraktion läuft, wartet die nächste Antwort kurz. Zwei
  Gespräche können nicht gleichzeitig generieren.

## Gedächtnis

- Supersession (Korrekturen) hängt am lokalen Modell als Richter. Mit 0.6B ist
  das nicht reproduzierbar: In einem E2E-Lauf wurde die Projektleiter-Korrektur
  als Ersetzung erkannt, in zwei weiteren Läufen nicht. Nicht erkannte
  Korrekturen bleiben als zwei „aktuelle“ Fakten sichtbar. Gegenmaßnahme: jeder
  Fakt trägt im Kontext seinen Speicherzeitpunkt, die neuesten stehen zuerst,
  und der Systemprompt sagt „neuere Angabe gilt“. Mit dem 0.6B-Modell antwortete
  Chat B damit korrekt (Priya Nair), ein späteres Gespräch nach dem Umzug nannte
  dennoch einmal den alten Namen (Temperatur 0,7). Größere Modelle (4B+) sind
  hier deutlich zuverlässiger, wurden aber nicht gemessen.
- Vektorsuche nur für Graphknoten (Fakten/Entitäten), nicht für ganze
  Dokumentabschnitte (dort nur Volltext/BM25).
- Keine automatische Konsolidierung (Hibernation/REM-Sleep aus SpAIder) und
  keine Hebbian-Verstärkung durch Feedback in V1; `utility_weight` wird
  gespeichert, aber nicht verändert.
- Löschen eines Gesprächs oder Dokuments entfernt nicht die daraus extrahierten
  Fakten (sie bleiben mit Herkunft „gelöscht“ markiert). Ein „Gedächtnis
  bereinigen“-Werkzeug fehlt.
- Dokumente: nur TXT, Markdown, textbasierte PDF (max. 50 MB). Gescannte PDFs
  ohne Textebene werden erkannt und abgelehnt (kein OCR). DOCX/HTML sind im
  SpAIder-Parser vorhanden, in V1 aber nicht freigeschaltet.

## Zustand und Portabilität

- Der Test „Umzug“ ist eine Simulation auf demselben Rechner (Verschieben in
  einen Pfad mit Leerzeichen/Umlauten und neuer Laufwerksbuchstabe per
  `subst`). Ein echter Zwei-PC-Test mit USB-SSD ist NICHT VERIFIZIERT; eine
  Anleitung steht in `TEST_REPORT.md`.
- „Sicheres Auswerfen“: Die App gibt nach „Sicher beenden“ ihre Ressourcen frei
  und meldet das; das Betriebssystem-Auswerfen muss der Nutzer selbst machen.
  Betriebssystem-Swap, Virenscanner oder Suchindizierung können Dateien auf dem
  Datenträger kurz offen halten; das liegt außerhalb der App.
- Spuren auf dem Host sind minimiert, nicht null: Windows legt für jedes
  gestartete Programm Prefetch-/Ereignisdaten an; Electron hält
  Cache/Session unter `data/electron/` (also auf dem Datenträger).
- FAT32-Datenträger: Dateien über 4 GB unmöglich; die App erkennt FAT32 und
  blockiert solche Downloads.
- Gleichzeitiger Zugriff von zwei Rechnern auf dasselbe Datenverzeichnis
  (Netzlaufwerk) ist nicht vorgesehen.

## Sicherheit

- Das Sitzungs-Token wird vom Launcher pro Start erzeugt und per Umgebungs-
  variable übergeben; lokale Prozesse mit denselben Rechten könnten es im
  Prinzip auslesen (gleiches Vertrauensniveau wie der Nutzer selbst).
- Keine Verschlüsselung der Daten auf dem Datenträger. Wer den Stick hat, hat
  die Daten; BitLocker To Go o. ä. ist Sache des Nutzers.

## Agenten (spätere Phasen)

- Keine Werkzeuge, kein Coding-Agent, keine Computersteuerung. Nur die
  Verträge in `spaider_foldable/tools/contracts.py` (host-gebundene Grants).

## Offene Fehler / Beobachtungen

- Kleine Modelle erzeugen gelegentlich englische Entitätsbeschreibungen bei
  deutschem Eingabetext (Extraktionsprompt ist englisch).
- Die Titelgenerierung nutzt die ersten 60 Zeichen der ersten Nachricht (kein
  LLM-Aufruf), was bei „Merk dir bitte: …“ unschöne Titel ergibt.
- Nach einem harten Absturz des Kerns (nicht der App) bleiben
  `llama-server`-Prozesse dank Job-Object nicht übrig; ein Absturz des
  Launchers selbst wurde nicht getestet.

## Ergänzungen 0.2 (Stand 2026-11-02)

- **GPU**: Nur Vulkan ist gebündelt; verifiziert ausschließlich auf einer
  Intel Arc 140V. NVIDIA/AMD über Vulkan sind plausibel, aber nicht gemessen;
  CUDA/ROCm-Varianten fehlen. Teilweises Offload (Schichtenanteil) ist
  implementiert, aber nicht ausgeführt. Der Vulkan-Shader-Cache des Treibers
  liegt im Hostprofil (außerhalb der App).
- **Planer**: Es gibt weiterhin nur einen Modell-Slot. Während einer Antwort
  wartet die Nachverarbeitung; während einer Nachverarbeitung muss eine neue
  Antwort erst die Unterbrechung abwarten (gemessen: Sekundenbruchteile bis
  wenige Sekunden, abhängig vom Server). Zwei Gespräche können nicht
  gleichzeitig generieren.
- **Extraktion auf kleinen Modellen**: Die SpAIder-Extraktion verlangt JSON;
  0.6B–1B-Modelle liefern es oft nicht oder mit kopiertem Beispieltext. Dann
  bleibt nur der rohe Fakt (Volltext/Vektor) ohne Entitäten. Ersetzungen
  durch den LLM-Richter fehlen dann; die deterministische Korrekturschicht
  greift nur bei expliziten Korrekturmarkern und ausreichender Wortüberlappung
  (eine implizite Korrektur wie „Mara Muster leitet Polarstern.“ ohne Marker
  bleibt dem Richter bzw. der Chronologie-Regel überlassen). Ein späterer
  Modellwechsel repariert vorhandene Extraktionen nicht automatisch (keine
  Wiederaufarbeitung in 0.2).
- **Reine Fragen** werden nicht als Fakten gespeichert (Heuristik: alle Sätze
  enden mit „?“ und keine Merk-Aufforderung). Eine Frage, die nebenbei eine
  Information enthält („Wusstest du, dass X?“), geht so verloren; mit
  „Merk dir …“ wird sie gespeichert.
- **Empfehlungen**: Qualitätsbelege stammen aus Modellkarten; echte
  Leistungswerte nur nach „Kurz messen“. Die Größenklasse ist ein Proxy, kein
  Qualitätsmaß. Der Qualitäts-Pick kann auf diesem Rechner ein 27B-Modell
  sein, das nur auf der CPU läuft (≈ 20 GB RAM, nicht ausgeführt); eine
  Antwortzeit dafür wurde nicht gemessen, die Karte sagt nur „längere
  Antwortzeit“.
- **Suche**: Hugging-Face-Suchtreffer sind ungeprüft; die Auflösung liest nur
  den GGUF-Header. Zugangsbeschränkte Repositories werden gemeldet, nicht
  umgangen. Ohne Netz liefert die Suche gecachte Ergebnisse oder nichts.
- **Katalog aktualisieren** prüft nur die eingebauten Einträge gegen ihre
  Repositories; es gibt keinen Online-Katalogdienst von SpAIder.
- **Offline-Test** bleibt eine Simulation (Proxy-Blackhole + Verbindungs-
  stichproben), siehe TEST_REPORT.
- **Zeitziele auf reiner CPU**: Mit einem 4B-Modell verarbeitet diese CPU
  50–75 Prompt-Tokens/s; jede Nachricht bringt 300–600 neue Tokens, also 5–10 s
  bis zum ersten Token und 7–11 Token/s. Erreicht werden dort nur Stopp-Latenz
  und Leerlauf. Auf der Arc 140V (Vulkan) liegen kurze Fragen bei 0,45–1,3 s,
  lange Verläufe (> 2 000 Tokens) bei 4–8 s (PERFORMANCE_REPORT).
- **Prompt-Cache**: Eine Hintergrund-Extraktion zwischen zwei Antworten
  überschreibt den Chat-Präfix im einzigen Server-Slot; die nächste Antwort
  verarbeitet den Prompt dann vollständig neu. Der Gedächtnisblock in der
  letzten Nutzernachricht verhindert außerdem, dass die vorige Runde im Cache
  wiederverwendet wird (nur der Präfix davor). Beides ist dokumentiert, nicht
  behoben (Vorschläge im PERFORMANCE_REPORT).
- **Extraktion sehr langer Eingaben** (≈ 1 500 Tokens) dauert im Hintergrund
  8 min (GPU) bis 13 min (CPU); sie blockiert keine Antwort, hält aber den
  Rechner beschäftigt. Für lange Texte ist der Dokumentimport der bessere Weg.

## Ergänzungen 0.3 (Agent und Live, Stand 2026-11-04)

- **Kein Autopilot.** Unterstützt sind: Dateien in freigegebenen Ordnern
  (lesen/suchen/schreiben von Textformaten), der eigene Browser (öffnen,
  Struktur lesen, klicken, füllen, auswählen, Tasten, scrollen, warten) und
  Windows-Fenster mit UI-Automation-Controls (lesen, Fokus, auslösen, Wert
  setzen, Tippen, erlaubte Tastenkombinationen, Klick an Position, Start von
  Notepad/Rechner/Explorer). Anwendungen ohne zugängliche Controls gehen nur
  über Bild + Klickposition und sind unzuverlässiger.
- **Folgenreiche Aktionen** erkennt der Kern über die Wirkungsklasse des
  Werkzeugs und einen Namensfilter (senden, kaufen, löschen, veröffentlichen
  …). Das ist konservativ, aber keine Garantie: ein anders beschrifteter Button
  mit Außenwirkung wird nicht erkannt. `external_effects` bleibt standardmäßig
  aus.
- **Erfolgsbelege** sind werkzeugabhängig; für Klicks auf gezeichnete
  Oberflächen gibt es nur die anschließende Beobachtung, keinen Rückkanal.
- **Modellqualität**: Mit 4B-Modellen dauern Planungsaufrufe 5–10 s; sie
  fragen gelegentlich nach bereits Erlaubtem oder brauchen mehrere
  Beobachtungen, bis sie ein Ergebnis erkennen. Die Aufgabenmatrix zeigt die
  tatsächlichen Erfolgsquoten; 20 Aufgaben beweisen keine allgemeine
  PC-Kompetenz.
- **Bildmodell**: Nur Qwen3-VL-4B wurde für Bildfragen und Grounding geprüft
  (Koordinaten normiert 0–1000, kalibriert). Ohne Projektor gibt es keine
  Bildfragen; der Live-Modus zeigt das an. Vision-Encoder-Offload wurde nur auf
  der Arc 140V gemessen; ohne Offload dauert eine Bildfrage ~11 s, auf CPU ~21 s.
- **Aufnahme**: PrintWindow liefert bei manchen Anwendungen (GPU-exklusive
  Oberflächen, geschützte Inhalte) schwarze Bilder; dann wird auf einen
  Bildschirmausschnitt zurückgegriffen, der verdeckte Teile nicht zeigt.
  Vollbildquellen nehmen alles auf diesem Monitor auf (außer SpAIder).
  Passwortfelder werden nur über UIA-Merkmale ausgenommen, nicht im Bild.
- **UIPI/UAC**: Fenster erhöhter Prozesse, Sperrbildschirm und sicherer
  Desktop können nicht bedient werden; das wird gemeldet, nicht umgangen.
- **Browser**: Der eigene Browser läuft sichtbar (headed). Normale, bereits
  offene Tabs des persönlichen Browsers werden nicht angebunden (nur Live-
  Beobachtung des Fensters). Downloads landen in `data/browser/downloads`.
  Anmeldungen im Agentenbrowser sind nicht portabel.
- **Nicht enthalten**: Mikrofon/Sprache, virtueller Zweitdesktop, IDE-
  Autonomie, freie Shell, MCP-Server, DXGI-Aufnahme, native Tool-Calls (der
  Planer nutzt Grammatik-JSON; `native_tool_calls` bleibt „ungetestet“).
- **Wiederaufnahme** nach Absturz: unterbrochene Aktionen sind `unknown_outcome`;
  es wird nichts automatisch wiederholt.
- **Rezepte** stammen aus genau einem belegten Lauf: Die Schritte werden mit
  den aufgezeichneten Argumenten wiedergegeben; nur als Parameter markierte
  Werte (`{name}`) werden ersetzt. Inhalte, die im Originallauf aus gelesenen
  Daten entstanden (z. B. ein geschriebener Zusammenfassungstext), werden
  **nicht** neu abgeleitet – solche Aufgaben eignen sich nicht als Rezept.
  Element-Kennungen (UIA-`element_id`, Browser-`ref`) müssen in der frischen
  Beobachtung wieder vorkommen, sonst übernimmt der Planer. Freigaberegeln
  gelten unverändert; der Abschluss ohne Modellaufruf setzt einen belegten
  letzten Schritt voraus.
- **Browserseite je Aufgabe**: Browserwerkzeuge wirken nur auf eine Seite, die
  dieselbe Aufgabe mit `browser.open` geöffnet hat (eine Seite aus einer
  früheren Aufgabe wird weder beobachtet noch bedient). Folgeaufgaben müssen
  die Adresse erneut öffnen.
- **Erfolgsmeldung ohne Beleg**: Meldet das Modell direkt nach einem Klick,
  Tastendruck oder `invoke` Erfolg, verlangt der Kern einmal eine Prüfung
  (zusätzlicher Modellaufruf). Besteht das Modell darauf, endet die Aufgabe
  als `completed` mit `verified=false` – die Oberfläche zeigt „nicht geprüft“.
- **Vordergrund**: Um ein Zielfenster zu aktivieren, nutzt der Kern die
  dokumentierten Wege gegen die Vordergrund-Sperre (Eingabewarteschlange
  anbinden, kurzer Modifier-Tastendruck). Während einer Automatisierung sollte
  der Nutzer die Hände von Tastatur und Maus lassen; manuelle Eingaben
  pausieren die Aufgabe.
- **Bild-Grounding**: `vision.locate` liefert bei Qwen3-VL-4B in der Regel
  brauchbare Mittelpunkte großer Schaltflächen; kleine oder ähnliche Ziele
  werden verfehlt. Die Koordinatenkonvention wird je Antwort auf Plausibilität
  geprüft; ein Punkt außerhalb des Bildes wird in die andere Konvention
  umgedeutet und als unverifiziert gekennzeichnet. `windows.click_at` meldet,
  ob sich die Oberfläche verändert hat, beweist aber nicht den Treffer.
- **Overlay ohne Bildwahl**: Das Live-Fenster fragt immer mit `use_image: auto`
  (Bild, wenn ein Bildmodell aktiv ist). Eine reine Strukturfrage ist über die
  API möglich, in der Oberfläche noch nicht wählbar.
- **Modellqualität je Familie**: Gemma 4 E2B erreicht in der Teilmenge 13/16
  (nach dem Verwerfen leerer Rückfragen), bleibt bei Windows-Aufgaben aber
  schwach (1/4) und ohne Grounding-Kalibrierung; Qwen3-VL-4B ist die geprüfte
  Standardwahl.
- **Hotkeys**: Der Standard `Strg+Alt+Leertaste` kann von anderen Programmen
  belegt sein (auf dem Entwicklungsrechner ist er es); SpAIder meldet das und
  bleibt ohne Hotkey, bis unter Einstellungen → Live & Agent eine freie
  Kombination geprüft und gespeichert wird. Hotkeys gelten nur, solange SpAIder
  läuft (Tray-Modus empfohlen); kein Autostart.
- **Host-Spuren**: comtypes (UI Automation) legt beim ersten Start einen
  kleinen Wrapper-Cache unter `%TEMP%\comtypes_cache\spaider-foldable-core-312`
  an; der Agentenbrowser schreibt sein Profil nur in `data/browser/profile`.

