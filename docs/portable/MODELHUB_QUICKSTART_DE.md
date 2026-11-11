# SpAIder Foldable 0.4: Schnellstart Modellbibliothek, Anbieter und Gedächtnis

Stand: 2026-11-11. Für den ersten Nutzertest nach dem Entpacken des ZIPs
(`SpAIder-Foldable-0.4.0-win-x64.zip`, SHA-256 in `MODELHUB_TEST_REPORT.md`).
Ohne Konto, ohne Docker/Python/Node, ohne Proxy: Der Standard ist ein lokales
Modell auf diesem Rechner.

## Startseite: vier gleichwertige Wege

| Weg | Wann | Wo |
|---|---|---|
| **Lokales Modell** | Standard; keine Daten verlassen den Rechner | Einstellungen → Modelle |
| **Eigener Schlüssel / eigener Server** | Sie haben einen API-Schlüssel oder einen Server im Netz (Ollama, LM Studio, llama.cpp, vLLM …) | Einstellungen → Verbindungen → Anbieter hinzufügen |
| **Bestehendes Konto** | nur ChatGPT (Sign in with ChatGPT), optional | Verbindungen → Karte „ChatGPT-Konto“ |
| **Gedächtnis in anderen Apps** | Claude Desktop/Code, Codex, andere MCP-Clients | Verbindungen → Gedächtnis-Clients |

## 1. Modell wählen (Einstellungen → Modelle)

Vier Bereiche:

- **Für diesen Computer empfohlen** – nach RAM/GPU bewertet („passt“, „passt
  knapp“, „zu groß“).
- **Alle Modelle entdecken** – Live-Suche auf Hugging Face (nur GGUF, Autor,
  Aufgabe, Sortierung, „Mehr laden“). Eine Zeile anklicken → Varianten (Q4/Q5/Q8,
  Teile, Projektor bei Bildmodellen), ehrliche Modellkarte, „Modellseite öffnen“,
  „Variante prüfen und laden“. Offline bleibt der zuletzt geladene Stand sichtbar.
- **Meine Modelle** – installiert, aktivieren, entfernen, **GGUF importieren**
  (eigene Datei; Projektor `mmproj-*.gguf` im selben Ordner wird erkannt).
- **Anbieter & Verbindungen** – Modelle der eingerichteten Verbindungen.

Downloads sind an eine Revision gebunden, werden per SHA-256 geprüft, lassen sich
anhalten/fortsetzen und aktivieren sich erst, wenn alles vollständig ist. Gated
Repositorys (z. B. `meta-llama/*`) zeigen das Lizenz-Gate an; SpAIder akzeptiert
nichts automatisch.

## 2. Verbindung anlegen (Einstellungen → Verbindungen)

1. „Anbieter hinzufügen“ → Karte wählen (jede gleichwertig).
2. Name, Basis-URL (bei eigenen Servern genau so wie der Server sie erwartet,
   kein automatisches `/v1`), Schlüssel und Speicherort (Dieser Rechner / Tresor
   / Sitzung), Ausführungsort, Datenfreigabe (`fragen` ist Standard).
3. **Verbindung testen** → vier Stufen: Server erreicht · Zugang geprüft ·
   Modell gelistet · echte Antwort (nur mit Haken, kann kosten).
4. „Modelle“ → Standardmodell wählen; Fähigkeiten (Text/Bild/Werkzeuge) als
   „gemeldet“, „verifiziert“ oder „unbekannt“.

## 3. Im Chat wechseln

Chat-Kopf → Bindung: lokales Modell oder Verbindung + Modell, Projekt. Der Kopf
zeigt Modell, Verbindung, Ausführungsort, Kontext und Aktivität; **Stopp** bricht
sofort ab, bereits empfangener Text bleibt. Vor der ersten Anfrage an eine
Verbindung mit „fragen“ erscheint der Freigabedialog mit dem, was gesendet würde.
Projekte mit „Nur lokal“ blockieren jede Cloud-/LAN-Verbindung.

## 4. Gedächtnis in Claude/Codex (Verbindungen → Gedächtnis-Clients)

„Client verbinden“ → Typ, Projekte, Rechte (Standard nur lesen) → Schnipsel in
die Client-Konfiguration kopieren → Client neu starten. SpAIder muss laufen.
Details und Fehlersuche: `CONNECT_MEMORY_CLIENTS_DE.md`.

## 5. Profile und Portabilität

Einstellungen → Profile: getrennte Gedächtnisse, Modelle, Verbindungen und
Tresore je Profil (Ordner `SpAIder\data\profiles\<id>`). Beim ersten Start von
0.4 wandern die Daten aus 0.3 automatisch in das erste Profil „Standard“.
Schlüssel „Dieser Rechner“ sind nach einem Umzug auf einen anderen PC nicht
lesbar (gewollt); der portable Tresor fragt nach dem Passwort.

## Erster Nutzertest (5 Schritte)

1. ZIP entpacken, `SpAIder.exe` starten; Einstellungen → Modelle → „Für diesen
   Computer empfohlen“ → Qwen3-4B laden und aktivieren; eine Frage stellen und
   mit Stopp abbrechen.
2. Einstellungen → Modelle → „Alle Modelle entdecken“: `gemma` suchen, eine
   Zeile öffnen, Modellkarte und Varianten ansehen, eine Variante prüfen.
3. Einstellungen → Verbindungen → „Eigener Server“ mit einem laufenden Ollama/
   LM Studio **oder** eine Cloud-Karte mit eigenem Schlüssel (Speicherort Tresor,
   Passwort setzen) → Verbindung testen → im Chat an die Verbindung binden,
   Freigabe erteilen, Antwort erhalten; dann Projekt „Nur lokal“ anlegen und
   prüfen, dass der Versand blockiert wird.
4. Verbindungen → Gedächtnis-Clients → „Claude Code“ verbinden; im Terminal den
   angezeigten `claude mcp add -s local …`-Befehl ausführen, `claude mcp list`
   → „Connected“, in Claude Code nach etwas fragen, das Sie SpAIder vorher
   gesagt haben.
5. Optional: Karte „ChatGPT-Konto“ → „Mit ChatGPT anmelden“ im Browser
   abschließen; Verbindung testen (Modell-Liste), eine kurze Antwort anfordern.
   Dies ist der erste Test gegen OpenAI – Ergebnis bitte in `KNOWN_LIMITATIONS.md`
   nachtragen.
