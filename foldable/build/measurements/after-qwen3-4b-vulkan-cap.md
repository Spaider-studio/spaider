# Messung after-qwen3-4b-vulkan-cap

Modell: `C:\Users\<Nutzer>\spaider-foldable\measure\Qwen3-4B-Q4_K_M.gguf`  
Runtime: `C:\Users\<Nutzer>\spaider-foldable\repo\foldable\build\runtime\llama-vulkan` gpu_layers=999 threads=None  
Zeit: 2026-11-02 15:02:23

## App-Pfad, Kernstart 8.28 s, Modell bereit nach 4.73 s

| Art | Gedächtnis | n | Suche fertig s | erstes Token s (median) | min/max | tok/s | Prompt-Tokens | Gesamt s | CPU s (Kern+Server) | Peak RSS MB (Server) |
|---|---|---|---|---|---|---|---|---|---|---|
| short | empty | 1 | 0.016 | 0.703 | 0.703/0.703 | 24.2 | 494 | 2.36 | 0.86 | 4135.5 |
| short | populated | 3 | 0.016 | 1.984 | 1.922/2.109 | 23.24 | 1051 | 5.516 | 1.72 | 5693.6 |
| long | empty | 1 | 0.016 | 2.734 | 2.734/2.734 | 21.12 | 1472 | 7.625 | 2.39 | 4224.0 |
| long | populated | 3 | 0.016 | 5.75 | 3.766/9.0 | 19.43 | 3304 | 11.25 | 2.56 | 7726.4 |
| recall | populated | 3 | 0.016 | 4.875 | 4.844/6.954 | 19.08 | 4617 | 6.5 | 1.39 | 8519.8 |
| fact | filling | 3 | 0.015 | 2.031 | 1.812/2.203 | 21.48 | 2078 | 3.562 | 0.97 | 5844.2 |
| stop | populated | 1 | 0.0 | 0.828 | 0.828/0.828 | 0.0 | 0 | 1.078 | 0.23 | 8031.3 |

Stopp: POST /stop → done-Ereignis 0.046 s, llama-server CPU ruhig nach 0.296 s, Status stopped.

Shutdown 2.72 s; llama-server nach Shutdown: []
Ingest-Drain nach kalter Kurz-/Langantwort: 10.56 s / 577.77 s; nach den drei Fakten: [23.11, 42.2, 26.11] s
