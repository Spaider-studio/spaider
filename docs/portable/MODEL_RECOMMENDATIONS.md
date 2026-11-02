# SpAIder Foldable: Modellempfehlungen für diesen Computer

Stand: 2026-11-02 (Core 0.2.0). Beschreibt, wie „Für diesen Computer
empfohlen“ zustande kommt. Code: `spaider_foldable/models/recommend.py`,
`hardware.py`, `inference/options.py`; API: `GET /api/models/recommendations?focus=…`.

## Eingaben

| Quelle | Inhalt | Herkunft |
|---|---|---|
| Hardwareprofil | CPU-Name, physische/logische Kerne, RAM gesamt/verfügbar, GPUs mit nutzbarem Speicher (nur Geräte, die die gebündelte Vulkan-Runtime meldet), Treiberversion (WMI), freier Speicher, Dateisystem, Akkubetrieb | lokal erkannt, pro Fingerabdruck in `hardware_profiles` gespeichert; GPU-Erkennung einmal pro Start (~1 s) |
| Katalog | eingebaute Einträge (`catalog.json`), `data/catalog.local.json`, aufgelöste Suchtreffer (`catalog_entries`) | Metadaten, keine Leistungszahlen |
| Messprofile | Token/s, Zeit bis erstes Token, Prompt-Token/s, Ladezeit je (Hardware-Fingerabdruck, Modell-SHA-256, Backend) | nur echte Messungen (`Kurz messen`, Kalibrierung); `model_profiles` |
| Einstellungen | Kontextlänge, Embedding-Modell aktiv, Schwerpunkt (Alltag / Dokumente / Coding-Fragen) | Nutzer |

## Stufe 1: Harte Eignung (`hard_filter`)

Ein Eintrag wird ausgeschlossen (mit Grund in `excluded`), wenn

1. er kein Chatmodell ist (`type` ≠ `chat`: Embedding, Basis-, Bild-, Audiomodell);
2. der Download ein Konto oder eine Freischaltung erfordert (`license.gated`
   oder `requires_account`); ein manueller Import bleibt möglich;
3. die Kompatibilität `unverified` ist (reine Suchtreffer ohne Header-Prüfung);
4. eine Datei größer als 4 GB ist und der Datenträger FAT32 verwendet;
5. der Download nicht auf den Datenträger passt (Größe + 1 GB Reserve);
6. der geschätzte Speicherbedarf nicht passt (siehe unten).

### Speicherschätzung

```
KV-Cache        = 2 · Schichten · Kontext · KV-Köpfe · Kopfdimension · 2 Byte   (f16)
                  (fehlen die Metadaten: 128 KiB pro Token, konservativ)
CPU-Pfad RAM    = 1,05 · Gewichte + KV-Cache + 0,6 GB Arbeitsbereiche
GPU-Pfad VRAM   = 1,08 · Gewichte + KV-Cache + 0,6 GB;  RAM = 0,3 · Gewichte + 0,6 GB
verfügbar RAM   = RAM gesamt − 3 GB Windows-Reserve − 0,9 GB (falls Embedding-Modell aktiv)
```

Die Downloadgröße allein ist nie das Kriterium. Geteilter Grafikspeicher
(iGPU) ist kein zusätzlicher RAM: Der VRAM-Pfad wird nur gewählt, wenn die
Runtime einen Vulkan-Speicher meldet, der den Bedarf deckt, und der
RAM-Anteil zusätzlich passt. Bei MoE-Modellen zählen die Gewichte aller
Experten (Dateigröße), nicht nur die aktiven Parameter. Kontextobergrenzen
aus Modellkarten werden nicht ausgeschöpft; gerechnet wird mit der
eingestellten Kontextlänge (Standard 8192).

## Stufe 2: Bewertung (`score`)

| Kriterium | Wirkung |
|---|---|
| Größenklasse vs. Schwerpunkt | Alltag zielt auf ~4B, Dokumente/Coding auf ~8B; Abstand zum Ziel senkt die Punkte |
| Verifikation | `executed` (echte Inferenz in diesem Projekt) +3; `metadata` 0 |
| Belege | Modellkarte nennt Deutsch +1; Code-Belege bei Schwerpunkt Coding +1 |
| Messung | nur echte lokale Messung: +min(Token/s ÷ 10, 4); ohne Messung Hinweis „noch nicht gemessen“ |
| GPU | passt vollständig in den Grafikspeicher +1,5 |
| Ressourcenkosten | −(Bedarf ÷ 8 GB) |
| Lizenz | Open-Source-Lizenz +0,5 |

Bis zu drei Picks:

- **Ausgewogen**: höchste Gesamtbewertung.
- **Schnell und sparsam**: geringster Speicherbedarf unter den passenden.
- **Mehr Qualität**: größtes passendes Modell, nur wenn es größer als der
  ausgewogene Pick ist.

Jeder Pick nennt seine Gründe (`reasons_de`), Downloadgröße, geschätzten
RAM/VRAM-Bedarf (als Schätzung), Verifikationsstatus, Lizenzart und, falls
vorhanden, die lokale Messung. „Am besten“ heißt: beste nachvollziehbare
Eignung unter den geprüften Kandidaten für diesen Rechner und Schwerpunkt,
kein universeller Sieger.

## Aktualisierung und Grenzen

- Nach der Aktivierung eines Modells kann „Kurz messen“ (abbrechbar, synthetischer
  Prompt, 96 Tokens) das Messprofil anlegen; die Empfehlung berücksichtigt es
  beim nächsten Aufruf. Kein Modell wird automatisch heruntergeladen, um es zu
  messen.
- Messprofile sind an den Hardware-Fingerabdruck gebunden (CPU-Name, Kerne,
  RAM-Klasse, GPU-Namen; keine Seriennummern). Auf einem anderen PC wird neu
  erkannt; fremde GPU-Konfigurationen werden nicht übernommen.
- Ist das aktive Modell auf einem anderen PC zu groß, bleibt es installiert;
  der Start scheitert mit Meldung, die Empfehlung zeigt passende Alternativen.
  Kein stiller Austausch.
- Ein Modell, das die Korrektur-/Quellen-Tests verfehlt (siehe TEST_REPORT),
  erhält keinen Vermerk „für das persönliche Gedächtnis uneingeschränkt
  geeignet“; die Empfehlung weist auf die Größenklasse hin.
- Fehlende Metadaten (Schichten, KV-Köpfe) führen zu einer konservativen
  Schätzung, nie zu erfundenen Werten.

## Beispielprofile (Simulation aus `tests/test_v2_core.py`)

| Profil | Ergebnis |
|---|---|
| 32 GB RAM, Vulkan-GPU 12 GB frei | drei Picks; 27B passt in den RAM (als „Mehr Qualität“ möglich, mit Hinweis auf CPU-Tempo) |
| 16 GB RAM, keine GPU | Qwen3.8 27B ausgeschlossen (RAM) |
| 8 GB RAM, keine GPU | nur Modelle mit ≤ 5 GB Gesamtbedarf; 8B-Modelle ausgeschlossen |
| 4 GB RAM, 32k Kontext | höchstens ein sehr kleines Modell |
| FAT32-Datenträger | Dateien > 4 GB ausgeschlossen |
| 1 GB frei auf dem Datenträger | Downloads ausgeschlossen |
| gated / unverified Eintrag | nie empfohlen |
