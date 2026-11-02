# Messung after-qwen3-4b-cpu

Modell: `C:\Users\mucbay\spaider-foldable\measure\Qwen3-4B-Q4_K_M.gguf`  
Runtime: `C:\Users\mucbay\spaider-foldable\repo\foldable\build\runtime\llama` gpu_layers=None threads=None  
Zeit: 2026-11-02 13:26:16

## Direkt (llama-server), Laden 4.66 s

| Art | kalt | n | TTFT median s | TTFT min/max | tok/s median | Prompt tok/s | CPU s/Antwort | Peak RSS MB |
|---|---|---|---|---|---|---|---|---|
| short | ja | 1 | 6.11 | 6.11/6.11 | 11.05 | 73.93 | 43.91 | 5355.1 |
| short | nein | 3 | 0.094 | 0.093/0.109 | 10.38 | 11.36 | 13.98 | 5355.2 |
| long | ja | 1 | 17.14 | 17.14/17.14 | 7.17 | 50.6 | 113.88 | 5367.1 |
| long | nein | 3 | 0.125 | 0.093/0.125 | 9.74 | 9.25 | 42.16 | 5367.1 |

## App-Pfad, Kernstart 6.27 s, Modell bereit nach 4.03 s

| Art | Gedächtnis | n | Suche fertig s | erstes Token s (median) | min/max | tok/s | Prompt-Tokens | Gesamt s | CPU s (Kern+Server) | Peak RSS MB (Server) |
|---|---|---|---|---|---|---|---|---|---|---|
| short | empty | 1 | 0.016 | 6.703 | 6.703/6.703 | 10.94 | 494 | 11.813 | 53.59 | 5362.1 |
| short | populated | 3 | 0.0 | 10.281 | 8.391/10.781 | 9.78 | 1037 | 18.047 | 81.14 | 6598.5 |
| long | empty | 1 | 0.016 | 17.391 | 17.391/17.391 | 11.06 | 1488 | 26.359 | 135.25 | 5443.8 |
| long | populated | 3 | 0.016 | 36.141 | 28.078/37.563 | 7.84 | 3510 | 53.875 | 301.88 | 7690.6 |
| recall | populated | 3 | 0.0 | 29.406 | 29.0/29.969 | 7.27 | 5281 | 33.734 | 253.39 | 9010.6 |
| fact | filling | 3 | 0.0 | 5.922 | 5.234/8.187 | 10.08 | 1754 | 8.906 | 41.33 | 6752.9 |
| stop | populated | 1 | 0.0 | 2.985 | 2.985/2.985 | 0.0 | 0 | 3.547 | 26.58 | 8385.7 |

Stopp: POST /stop → done-Ereignis 0.109 s, llama-server CPU ruhig nach 0.359 s, Status stopped.

Shutdown 1.89 s; llama-server nach Shutdown: []
Ingest-Drain nach kalter Kurz-/Langantwort: 17.11 s / 758.2 s; nach den drei Fakten: [34.11, 62.19, 36.11] s
