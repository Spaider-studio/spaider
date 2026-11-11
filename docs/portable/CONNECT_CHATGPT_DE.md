# ChatGPT mit SpAIder Foldable: Konto-Anmeldung und Gedächtnis-Tunnel

Stand: 2026-11-11 (Core 0.4.0). Zwei getrennte, **optionale** Wege. Keiner ist
Voraussetzung für SpAIder; das lokale Modell bleibt der Standard. Beide sind
vorbereitet, aber **nicht gegen echte OpenAI-Konten geprüft** (0 € Cloud-Ausgaben,
kein unbeaufsichtigtes Akzeptieren von Anmeldedialogen).

## Weg 1: ChatGPT-Konto als Modellquelle („Sign in with ChatGPT“)

Was es ist: OpenAI erlaubt lokal laufenden Open-Source-Apps, nach einer
Anmeldung im Browser den ChatGPT-Plan des Nutzers für Responses-Anfragen zu
nutzen (Quellen S16–S21 in `MODELHUB_RESEARCH.md`). Das ist **kein**
API-Schlüssel, kein Zugriff auf ChatGPT-Gespräche und kein stiller Wechsel auf
API-Abrechnung.

Status in SpAIder: Einstellungen → Verbindungen → Karte „ChatGPT-Konto (Sign in
with ChatGPT, optional)“ zeigt **„Einrichtung erforderlich“**, bis die Anmeldung
abgeschlossen ist.

### Ablauf

1. Verbindung aus der Karte anlegen (Basis-URL `https://api.openai.com/v1`,
   Anmeldeart „Anmeldung im Browser“).
2. „Mit ChatGPT anmelden“: SpAIder erzeugt PKCE (S256), `state`, `nonce`, öffnet
   einen Loopback-Empfänger auf `http://127.0.0.1:<zufälliger Port>/auth/callback`
   und zeigt die Autorisierungsadresse (`https://auth.openai.com/api/accounts/authorize`
   mit `client_id=dynamic_agent_client`, `agent_name_hint=SpAIder Foldable`,
   `ext_agent_host_id=<stabile, nicht geheime Rechner-ID>`, Scope
   `openid profile email offline_access resource.invoke chatgpt.tokens.use.direct`,
   `resource=https://api.openai.com/v1`).
3. Sie öffnen die Adresse im Browser, melden sich an und bestätigen. SpAIder
   bestätigt nichts automatisch; ohne Rückkehr innerhalb von 10 Minuten endet die
   Anmeldung mit „Zeitüberschreitung“.
4. Der Browser kehrt zum Loopback zurück; SpAIder tauscht den Code am
   Token-Endpunkt (`…/api/accounts/oauth/token`) mit Verifier und `resource` ein,
   prüft das ID-Token gegen die JWKS des Issuers (Issuer, Audience = ausgestellte
   Client-ID, Nonce) und speichert Zugriffs- und Refresh-Token **nur auf diesem
   Rechner** (DPAPI; ersatzweise nur für die Sitzung). Refresh-Token landen nie im
   portablen Tresor.
5. Die Karte zeigt „Angemeldet“ mit der E-Mail des Kontos. Modelle: „Modelle“ in
   der Verbindung (`GET /v1/models`, nur sichtbare Einträge). Anfragen:
   `POST /v1/responses` mit `store:false`, `stream:true`; Parameter außerhalb der
   erlaubten Liste (`tools`, `text`, `reasoning`, `instructions`) werden nicht
   gesendet.

### Erneuerung, Fehler, Abmelden

- Läuft das Zugriffstoken ab, erneuert SpAIder es serialisiert (nur ein
  Refresh gleichzeitig) mit Rotation.
- `invalid_grant`/`refresh_token_*` → Status „Einrichtung erforderlich“ und die
  Aufforderung, sich erneut anzumelden. **Kein** stiller Rückfall auf einen
  API-Schlüssel.
- `subscription_sharing_usage_limit_exceeded` (429) → warten, Meldung mit
  Wartezeit; `subscription_sharing_user_not_eligible` (403) → Konto nicht
  berechtigt, Vorgang stoppt.
- „Abmelden“ ruft den `revocation_endpoint` aus der OpenID-Konfiguration auf
  und löscht die Token.

### Was geprüft wurde

Der komplette Ablauf (Autorisierung → Loopback → Token-Austausch mit
PKCE-Prüfung → ID-Token-Verifikation per JWKS → Speicherung → Refresh mit
Rotation und Serialisierung → `invalid_grant` → Abmelden mit Revocation →
abgelehnte Zustimmung → falscher `state` → fremd signiertes ID-Token wird
abgewiesen) läuft in `tests/providers/test_siwc.py` gegen einen **lokalen
Fake-Issuer**. Gegen `auth.openai.com` wurde nichts ausgeführt: Ob SpAIder
Foldable die Zulassung für die dynamische Registrierung erhält und welches Feld
die ausgestellte Client-ID trägt, ist eine Annahme aus der Dokumentation
(SpAIder liest `client_id` aus der Token-Antwort oder aus dem `aud` des
ID-Tokens). Erster echter Test: siehe `MODELHUB_QUICKSTART_DE.md`, Abschnitt
„Erster Nutzertest“.

## Weg 2: SpAIder-Gedächtnis in ChatGPT (Secure MCP Tunnel)

ChatGPT kann keinen lokalen Prozess starten; die stdio-Brücke aus
`CONNECT_MEMORY_CLIENTS_DE.md` muss über einen Tunnel erreichbar gemacht werden.
OpenAI stellt dafür den `tunnel-client` bereit (Quelle S24). SpAIder zeigt unter
Verbindungen → Gedächtnis-Clients → „ChatGPT (Tunnel)“ nur **Status und Schritte**:

| Voraussetzung | Status auf dem Entwicklungsrechner |
|---|---|
| ChatGPT-Workspace/Organisation mit Zugriff auf die Plattform-Tunnel-Einstellungen | nicht vorhanden |
| Tunnel-ID und Runtime-API-Key (Tunnels Read+Use) | nicht vorhanden |
| `tunnel-client` installiert | nicht gefunden (Statusanzeige prüft den PATH) |
| Veröffentlichung als Plugin/Connector | **nicht vorgesehen**, nichts veröffentlicht |

Schritte, sobald die Voraussetzungen vorliegen:

1. In SpAIder einen Connector vom Typ „ChatGPT (Tunnel)“ anlegen (Rechte wie
   gewünscht) und den Brückenbefehl notieren.
2. `tunnel-client init --profile spaider --tunnel-id <ID> --mcp-command "<Brückenbefehl>"`
   (fragt den Runtime-Key ab).
3. `tunnel-client doctor`, dann `tunnel-client run`; SpAIder muss laufen.
4. In den Plattform-Tunnel-Einstellungen den Connector dem eigenen Workspace
   zuordnen (nicht öffentlich).

Limits laut Dokumentation: 600 Anfragen/Minute, 10 MiB je Anfrage, 120 s. Der
Tunnel wurde hier nicht aufgebaut und nicht getestet.
