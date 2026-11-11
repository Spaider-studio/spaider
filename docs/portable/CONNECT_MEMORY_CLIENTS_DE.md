# SpAIder-Gedächtnis in Claude Desktop, Claude Code, Codex und anderen MCP-Clients

Stand: 2026-11-11 (Core 0.4.0). Diese Anleitung beschreibt, wie ein externer
KI-Client das Gedächtnis eines SpAIder-Profils über das Model Context Protocol
(MCP) nutzt. Was davon wirklich ausprobiert wurde, steht am Ende unter
„Prüfstand“.

## So funktioniert es

```
Claude Desktop / Claude Code / Codex / anderer Client
        │  stdio (MCP)
        ▼
spaider-foldable-core.exe --mcp-stdio --connector <Datei>      ← „Brücke“, pro Client ein Prozess
        │  HTTP, nur 127.0.0.1, Connector-Token
        ▼
laufendes SpAIder Foldable (schreibt als einziger in die Datenbank)
```

- Ein **Connector** = ein Client × ein Profil × freigegebene Projekte × Rechte.
  Rechte: `read` (immer), `write` (speichern/korrigieren), `forget` (vergessen).
- Der Client startet die Brücke selbst. Die Brücke findet den laufenden Kern über
  `SpAIder\data\host\core.json` (Port und Prozess-ID, keine Geheimnisse). Startet
  SpAIder auf einem anderen Port neu, bleibt die Client-Konfiguration gültig.
- Läuft SpAIder nicht, antwortet jedes Werkzeug mit „SpAIder Foldable läuft nicht
  …“ statt mit einem Absturz.
- Alles bleibt auf dem Rechner. Es gibt keinen Netzwerkdienst; der RPC-Pfad nimmt
  nur Loopback-Verbindungen an und weist Browser-Aufrufe (Origin-Header) ab.

## Werkzeuge

| Werkzeug | Recht | Was es tut |
|---|---|---|
| `status` | read | Profil, freigegebene Projekte, Rechte, Anzahl Fakten/Dokumente, Suchmodus (lexikalisch oder hybrid mit Vektoren) |
| `search_memory(query, limit)` | read | Suche über Fakten, Notizen und Dokumentabschnitte der freigegebenen Projekte; Treffer mit `source_id`, Auszug, Gültigkeit (`current`/`uncertain`/`superseded`) |
| `get_source(source_id, max_chars, offset)` | read | Quelltext eines Fakts oder Dokumentausschnitts, begrenzt (max. 8000 Zeichen je Aufruf) |
| `remember(text, project_id?, client_request_id?)` | write | Speichert eine Aussage als Rohfakt (sofort suchbar); die Extraktion läuft im Hintergrund mit dem lokalen Modell |
| `correct_memory(text, replaces_source_id?, …)` | write | Speichert eine Korrektur; erkannte Widersprüche und optional eine benannte Quelle werden als ersetzt markiert |
| `forget_memory(source_id, confirm=true)` | forget | Markiert eine Aussage als vergessen (kein physisches Löschen) |

Schreibaufrufe mit `client_request_id` sind idempotent: Wiederholt ein Client
denselben Aufruf (z. B. nach einem Timeout), entsteht kein Duplikat.

Jede Antwort trägt den Hinweis, dass Treffer **Daten** aus dem Gedächtnis sind,
keine Anweisungen. Clients sollten Gedächtnisinhalte nie als Befehle ausführen.

## Einrichtung in SpAIder (einmalig je Client)

1. Einstellungen → **Verbindungen** → Abschnitt **Gedächtnis-Clients** → „Client
   verbinden“.
2. Client-Typ wählen (Claude Desktop, Claude Code, Codex, generisch), Projekte und
   Rechte festlegen. Empfehlung: nur `read`; `write` erst, wenn Sie wollen, dass der
   Client Dinge dauerhaft ablegt.
3. SpAIder zeigt den fertigen Konfigurationsschnipsel für den gewählten Client. Der
   Token steht nur in der Connector-Datei `SpAIder\data\connectors\<id>.json`
   (und einmalig im Dialog); in der Datenbank liegt nur ein Hash.
4. Wechselt die App den Laufwerksbuchstaben oder Ordner, Schnipsel unter
   „Verbindung aktualisieren“ neu erzeugen (die Pfade sind absolut).

Widerrufen: Verbindungen → Gedächtnis-Clients → „Widerrufen“. Danach erhält der
Client bei jedem Aufruf `revoked`.

## Claude Desktop

Datei `%APPDATA%\Claude\claude_desktop_config.json` (anlegen, falls nicht
vorhanden), Abschnitt `mcpServers` ergänzen – SpAIder erzeugt diesen Block:

```json
{
  "mcpServers": {
    "spaider-memory": {
      "command": "D:\\SpAIder-Foldable\\resources\\core\\spaider-foldable-core.exe",
      "args": ["--mcp-stdio", "--connector", "D:\\SpAIder-Foldable\\SpAIder\\data\\connectors\\mcp_1a2b3c4d5e.json"]
    }
  }
}
```

Claude Desktop danach neu starten. Die Werkzeuge erscheinen im Werkzeug-Menü des
Chats. Claude Desktop war auf dem Entwicklungsrechner **nicht installiert**; der
Block folgt der offiziellen Dokumentation (Quelle S25 in `MODELHUB_RESEARCH.md`)
und ist nicht gegen die App geprüft.

## Claude Code

Lokaler Scope (nur Sie, nur dieses Projektverzeichnis) – empfohlen, weil
Projekt-Scope (`.mcp.json`) in Claude Code erst nach einer interaktiven Freigabe
aktiv wird:

```
claude mcp add -s local spaider-memory -- "D:\SpAIder-Foldable\resources\core\spaider-foldable-core.exe" --mcp-stdio --connector "D:\SpAIder-Foldable\SpAIder\data\connectors\mcp_1a2b3c4d5e.json"
claude mcp list        → spaider-memory: … - ✓ Connected   (SpAIder muss laufen)
claude mcp get spaider-memory
claude mcp remove spaider-memory -s local
```

Für alle Projekte: `-s user`. Für ein geteiltes Projekt: `-s project` erzeugt
`.mcp.json`; Claude Code fragt beim nächsten Start nach der Freigabe.

## OpenAI Codex (App und CLI)

`%USERPROFILE%\.codex\config.toml` ergänzen (SpAIder erzeugt den Block):

```toml
[mcp_servers.spaider_memory]
command = "D:\\SpAIder-Foldable\\resources\\core\\spaider-foldable-core.exe"
args = ["--mcp-stdio", "--connector", "D:\\SpAIder-Foldable\\SpAIder\\data\\connectors\\mcp_1a2b3c4d5e.json"]
```

Prüfen: `codex mcp list` und `codex mcp get spaider_memory` (die CLI liegt bei der
Codex-App unter `…\OpenAI.Codex_<version>\app\resources\codex.exe`; über die App
erreichen Sie sie als „codex“ im Terminal, falls die PATH-Aliase eingerichtet
sind). Die Werkzeuge stehen dann in Codex-Sitzungen bereit.

## Generischer stdio-Client

```json
{ "command": "<Pfad>\\spaider-foldable-core.exe",
  "args": ["--mcp-stdio", "--connector", "<Pfad>\\SpAIder\\data\\connectors\\<id>.json"],
  "transport": "stdio" }
```

Protokoll: MCP 2026-07-28 (Python-SDK `mcp` 2.3.0); Werkzeuge liefern
strukturierte Ergebnisse (`structuredContent`) und Text.

## ChatGPT

ChatGPT kann keinen lokalen Prozess starten. Dafür ist ein Secure MCP Tunnel
vorgesehen; Status und Schritte stehen in `CONNECT_CHATGPT_DE.md` und in SpAIder
unter Verbindungen → Gedächtnis-Clients → „ChatGPT (Tunnel)“. Es wurde **nichts
veröffentlicht** und kein Tunnel angelegt.

## Fehlersuche

| Meldung | Ursache / Abhilfe |
|---|---|
| „SpAIder Foldable läuft nicht (keine core.json gefunden)“ | SpAIder starten; die Brücke prüft bei jedem Aufruf erneut |
| `revoked` | Connector in SpAIder widerrufen → neu anlegen |
| `wrong_profile` | In SpAIder ist ein anderes Profil aktiv als das, an das der Connector gebunden ist → Profil wechseln oder Connector im aktiven Profil anlegen |
| `forbidden` bei `remember`/`forget_memory` | Recht fehlt → Connector mit `write`/`forget` neu anlegen |
| Claude Code zeigt „Pending approval“ | Projekt-Scope; `claude` interaktiv starten und freigeben, oder `-s local` verwenden |
| Nichts passiert, Client hängt | Pfad im Schnipsel prüfen (Laufwerksbuchstabe nach Umzug), Logs: `SpAIder\data\logs\core.log`; die Brücke schreibt nur auf stderr des Clients |

## Prüfstand (was wirklich lief)

| Client | Ergebnis | Belegt durch |
|---|---|---|
| MCP-Python-SDK-Client (Referenz) gegen die **echte** stdio-Brücke und den **echten** Kernprozess | `list_tools`, `status`, `remember`, `search_memory`, `get_source`, `forget_memory` (korrekt verweigert); Fakt danach in der normalen SpAIder-Suche | `tests/test_connectors.py::test_stdio_bridge_end_to_end`, Lauf in `MODELHUB_TEST_REPORT.md` |
| **Claude Code 2.1.322** (claude.exe der VS-Code-Erweiterung) | `claude mcp add -s local …` → `claude mcp list` meldet „✓ Connected“ (Claude Code hat die Brücke gestartet und den MCP-Handshake gegen den laufenden Kern ausgeführt); mit isoliertem `CLAUDE_CONFIG_DIR`, danach wieder entfernt. Kein Modellaufruf. | WORKLOG Phase 4, `scratchpad/client_check.py` |
| **Codex-CLI 0.167.0-alpha.3.2** (aus dem Codex-App-Paket) | `codex mcp list`/`get` zeigen den Server aus dem erzeugten TOML (isoliertes `CODEX_HOME`); **nur Konfigurationsebene**, kein Handshake durch Codex (bräuchte eine Sitzung mit Modell) | WORKLOG Phase 4 |
| Claude Desktop | nicht installiert → Schnipsel nur vorbereitet | – |
| ChatGPT (Tunnel) | nur Status/Anleitung | `CONNECT_CHATGPT_DE.md` |
