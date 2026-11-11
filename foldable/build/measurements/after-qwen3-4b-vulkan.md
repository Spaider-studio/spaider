# Messung after-qwen3-4b-vulkan

Modell: `C:\Users\mucbay\spaider-foldable\measure\Qwen3-4B-Q4_K_M.gguf`  
Runtime: `C:\Users\mucbay\spaider-foldable\repo\foldable\build\runtime\llama-vulkan` gpu_layers=999 threads=None  
Zeit: 2026-11-02 14:25:25

## Direkt (llama-server), Laden 4.17 s

| Art | kalt | n | TTFT median s | TTFT min/max | tok/s median | Prompt tok/s | CPU s/Antwort | Peak RSS MB |
|---|---|---|---|---|---|---|---|---|
| short | ja | 1 | 0.453 | 0.453/0.453 | 31.06 | 1082.45 | 0.52 | 4118.0 |
| short | nein | 3 | 0.032 | 0.031/0.046 | 28.81 | 31.41 | 0.47 | 4130.2 |
| long | ja | 1 | 1.297 | 1.297/1.297 | 29.12 | 674.29 | 1.41 | 4132.2 |
| long | nein | 3 | 0.031 | 0.031/0.031 | 29.18 | 29.29 | 1.2 | 4132.3 |

## App-Pfad, Kernstart 4.58 s, Modell bereit nach 4.33 s

| Art | Gedächtnis | n | Suche fertig s | erstes Token s (median) | min/max | tok/s | Prompt-Tokens | Gesamt s | CPU s (Kern+Server) | Peak RSS MB (Server) |
|---|---|---|---|---|---|---|---|---|---|---|
| short | empty | 1 | 0.0 | 0.453 | 0.453/0.453 | 29.51 | 494 | 1.906 | 0.58 | 4122.3 |
| short | populated | 3 | 0.0 | 1.25 | 1.203/1.312 | 26.79 | 1037 | 4.469 | 1.44 | 6686.2 |
| long | empty | 1 | 0.015 | 2.14 | 2.14/2.14 | 27.61 | 1476 | 5.703 | 1.73 | 4209.8 |
| long | populated | 3 | 0.016 | 7.078 | 4.25/8.016 | 21.42 | 3690 | 11.797 | 3.14 | 8925.3 |
| recall | populated | 3 | 0.016 | 7.516 | 7.391/7.641 | 19.73 | 5390 | 9.141 | 1.89 | 3857.8 |
| fact | filling | 3 | 0.0 | 1.75 | 0.953/1.89 | 27.4 | 1747 | 2.953 | 0.98 | 6687.6 |
| stop | populated | 1 | 0.016 | 0.672 | 0.672/0.672 | 0.0 | 0 | 0.922 | 0.23 | 3169.9 |

Stopp: POST /stop → done-Ereignis 0.063 s, llama-server CPU ruhig nach 0.313 s, Status stopped.

Shutdown 2.73 s; llama-server nach Shutdown: []
Ingest-Drain nach kalter Kurz-/Langantwort: 7.06 s / 484.2 s; nach den drei Fakten: [18.05, 32.09, 22.06] s
