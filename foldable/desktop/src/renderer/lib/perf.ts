/**
 * Development-only frame-gap measurement (`window.__spaiderPerf`).
 *
 * Installed when the renderer runs under `vite dev` or the page is loaded
 * with `?perf=1` (verification harness). The store starts a measurement at
 * the beginning of every chat stream and stops it when the stream ends; the
 * result (max gap between two animation frames, frame count, duration) is
 * available as `window.__spaiderPerf.last` and via `report()`.
 *
 * A frame gap well above 16–33 ms means the main thread was blocked (e.g. a
 * Markdown re-render per token). Nothing here affects the production UI.
 */
export interface PerfReport {
  label: string;
  startedAt: number;
  durationMs: number;
  frames: number;
  maxGapMs: number;
  meanGapMs: number;
  gapsOver50ms: number;
}

export interface SpaiderPerf {
  start(label?: string): void;
  stop(): PerfReport | null;
  report(): PerfReport | null;
  last: PerfReport | null;
  readonly running: boolean;
}

declare global {
  interface Window {
    __spaiderPerf?: SpaiderPerf;
  }
}

function createPerf(): SpaiderPerf {
  let raf: number | null = null;
  let label = "";
  let startedAt = 0;
  let lastFrame = 0;
  let frames = 0;
  let maxGap = 0;
  let sumGap = 0;
  let over50 = 0;

  const snapshot = (): PerfReport => ({
    label,
    startedAt,
    durationMs: Math.round(performance.now() - startedAt),
    frames,
    maxGapMs: Math.round(maxGap * 10) / 10,
    meanGapMs: frames > 1 ? Math.round((sumGap / (frames - 1)) * 10) / 10 : 0,
    gapsOver50ms: over50,
  });

  const tick = (now: number) => {
    if (lastFrame > 0) {
      const gap = now - lastFrame;
      if (gap > maxGap) maxGap = gap;
      if (gap > 50) over50 += 1;
      sumGap += gap;
    }
    lastFrame = now;
    frames += 1;
    raf = requestAnimationFrame(tick);
  };

  const perf: SpaiderPerf = {
    last: null,
    get running() {
      return raf !== null;
    },
    start(l = "stream") {
      if (raf !== null) perf.stop();
      label = l;
      startedAt = performance.now();
      lastFrame = 0;
      frames = 0;
      maxGap = 0;
      sumGap = 0;
      over50 = 0;
      raf = requestAnimationFrame(tick);
    },
    stop() {
      if (raf === null) return perf.last;
      cancelAnimationFrame(raf);
      raf = null;
      perf.last = snapshot();
      return perf.last;
    },
    report() {
      return raf !== null ? snapshot() : perf.last;
    },
  };
  return perf;
}

export function installPerfHelper(): void {
  if (typeof window === "undefined" || window.__spaiderPerf) return;
  let wanted = import.meta.env.DEV;
  try {
    wanted = wanted || new URLSearchParams(window.location.search).has("perf");
  } catch {
    /* no location */
  }
  if (wanted) window.__spaiderPerf = createPerf();
}

/** The helper, if installed (dev / harness only). */
export function getPerf(): SpaiderPerf | undefined {
  return typeof window === "undefined" ? undefined : window.__spaiderPerf;
}
