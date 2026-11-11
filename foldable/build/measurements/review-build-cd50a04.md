# SpAIder Foldable – Review-Build (Vorschau, kein Release)

| Feld | Wert |
|---|---|
| Datei | `SpAIder-Foldable-0.3.0-review-cd50a04-win-x64.zip` |
| Größe | 395922585 Bytes (377.6 MiB) |
| SHA-256 | `20b4096516796956b76b4b2503b29a258bbaf71de46ade6c2a04e1f28cd6b5ff` |
| Prüfsummendatei | `SHA256SUMS-0.3.0-review-cd50a04.txt` |
| Quell-Commit (Branch) | `cd50a042046acee034e74383087851b58acd04f0` (`feature/spaider-foldable-review`) |
| Letzte Änderung Kern (`foldable/core`) | `34948d5e2a5a67e8e635348ff0d144a5f9380f5d` |
| Letzte Änderung Launcher (`foldable/desktop`) | `a85dca66743fdfc112ef5d7bf666d426da204d7c` |
| Letzte Änderung Build-Skripte | `fc4b96f9a87341f8317a92bc952f9fc027030ec2` |
| Kern gebaut (PyInstaller, onedir) | 2026-11-04 12:48 lokale Zeit, Python 3.12, aus `foldable/build/spaider_foldable_core.spec` im Review-Worktree |
| Launcher gebaut (@electron/packager) | 2026-11-04 12:41 lokale Zeit, Electron 45.2.1, App-Version 0.3.0 |
| Runtime | llama.cpp b12032, Varianten CPU x64 + Vulkan x64, SHA-256 in `foldable/build/fetch_runtime.py` gepinnt (aus lokalem Cache verifiziert extrahiert) |
| Browserpaket (nicht im ZIP, Download im Datenordner) | Chrome for Testing 156.0.8078.4 (Playwright-Build 1248), 207 073 013 Bytes, SHA-256 `220021ecfdad08c116fc71a153e65e3861ba841c531afc4f7acc7bd5d44b41db`, Playwright 1.64.1 |
| Modelle | nicht enthalten; Download über den Katalog (Qwen3-VL-4B: 2 497 281 664 + 453 974 304 Bytes) |
| Datenordner | nicht enthalten; entsteht beim ersten Start neben `SpAIder.exe` |
| Build-Rechner | Windows 11 Enterprise 10.0.26200, Intel Core Ultra 7 268V, Intel Arc 140V |

**Kennzeichnung:** Review-Build zur gemeinsamen Prüfung. Bekannte Einschränkungen in
`docs/portable/KNOWN_LIMITATIONS.md`; nicht ausgeführte Prüfungen in
`docs/portable/AGENT_LIVE_TEST_REPORT.md` („NICHT VERIFIZIERT“) und in der PR-Beschreibung.
Das frühere Paket `SpAIder-Foldable-0.3.0-win-x64.zip` (SHA-256 `87058242…73fa`) wurde aus
dem Code-Stand `371a503`/`a85dca6` gebaut und ist **nicht** dieser Review-Build.
