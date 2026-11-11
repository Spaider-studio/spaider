# SpAIder Foldable 0.4.0: universelle Modellbibliothek, offene Anbieter-Anbindung, portables Gedächtnis

Entwurf (Draft-PR, privates Repository `Useebay/spaider-foldable-private`).
Basis: `feature/spaider-foldable-review` @ `84f59c4`. Kein Push nach
`Spaider-studio/spaider`.

## Was dieser Branch liefert

- **Anbieterunabhängiger Kern**: Chat, Gedächtnis und Agent laufen weiter ohne
  jedes Konto mit dem eingebetteten llama.cpp. Jede Anbindung ist optional.
- **Vier gleichwertige Wege**: lokale Modelle (Katalog + Live-Suche auf Hugging
  Face + GGUF-Import), eigene Schlüssel/Server (13 Presets, eigene per
  `providers.local.json`), bestehendes Konto nur als „Sign in with ChatGPT“
  (Skelett, nicht gegen OpenAI geprüft), dasselbe Gedächtnis in Claude
  Desktop/Claude Code/Codex/anderen MCP-Clients (stdio-Brücke).
- **Datenverträge**: Events (`GenerationEvent`), Verbindungen, Fähigkeitsprofile,
  Projekte, Freigaben, Bindung je Gespräch, Connectoren – `API_CONTRACT.md`
  Addenda v1.3/v1.4, Schema v4/v5.
- **Gemeldeter Leerantwort-Fehler**: Ursache (llama.cpp `ErrorOutOfDeviceMemory`
  als Stream-Fehlerobjekt, vom Client ignoriert) behoben: Fehler-Event mit
  nächstem Schritt, CPU-Rückfall beim nächsten Laden, Regressionstests.
- **Profile**: getrennte Gedächtnisse/Modelle/Verbindungen/Tresore je Profil,
  Migration des 0.3-Layouts beim ersten Start, Wechsel ohne Neustart.
- **Schlüssel**: DPAPI (Rechner), portabler Tresor (Argon2id + AES-256-GCM),
  Sitzung; nie in Logs/Argumenten/URLs.
- **Datenschutzregeln**: „Nur lokal“-Projekte blockieren Cloud/LAN; Freigabe je
  Verbindung × Projekt mit Vorschau; Erinnerungen aus „Nur lokal“-Projekten
  werden für Cloud-Anfragen gar nicht abgerufen (Kanarien-Test).
- **UI**: Verbindungen (Schlüsselspeicher, Anbieterkarten, vier Teststufen,
  Modelle mit Fähigkeits-Chips, Freigaben, Gedächtnis-Clients), Modelle in vier
  Bereichen, Chat-Kopf mit Bindung, Freigabedialog, ehrliche Fehlerzustände.

## Was wirklich geprüft wurde

Siehe `MODELHUB_TEST_REPORT.md` (T1–T17 mit Einstufung echt / Testserver /
Metadaten / blockiert). Kurz: echte Läufe mit Qwen3-0.6B (Download aus dem
Katalog) und Gemma 4 E2B (Import), eigener OpenAI-kompatibler Server, stdio-
Brücke mit SDK-Client und mit Claude Code 2.1.322 („Connected“), Codex-CLI
(Konfigurationsebene), Profilwechsel, Kanarien-Test. **Kein Cloud-Anbieter mit
echtem Konto** (0 € Vorgabe); SIWC nur gegen Fake-Issuer.

## Nicht enthalten / offen

`KNOWN_LIMITATIONS.md` (0.4.0-Abschnitt): HTTP-Transport für Connectoren,
ChatGPT-Tunnel, echte Prüfung aller Cloud-Presets und von SIWC, Tool-Ausführung
für Cloud-Modelle, 27B-Wiederholungslauf.

## Prüfen

```
cd foldable/core  && pytest tests            # 129 bestanden, 6 übersprungen (ohne Modell)
cd foldable/desktop && npm test -- --run     # 104 Tests
python foldable/build/verify_package.py foldable/build/out/SpAIder-Foldable-0.4.0-win-x64.zip
```

🤖 Generated with [Claude Code](https://claude.com/claude-code)
