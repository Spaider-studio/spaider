/**
 * Measured latencies of the main process (overlay hotkey -> window shown ->
 * first paint). Every sample is a real performance.now() difference; nothing
 * is estimated. The last MAX_SAMPLES are kept for `getPerfSamples()` and every
 * sample is written to the launcher log (timings only, never content).
 */
import type { PerfSample } from "../shared-types/bridge";
import { log } from "./log";

export const MAX_SAMPLES = 20;
const samples: PerfSample[] = [];

export function recordSample(name: string, ms: number): PerfSample {
  const sample: PerfSample = { name, ms: Math.round(ms * 10) / 10, at: Date.now() };
  samples.push(sample);
  while (samples.length > MAX_SAMPLES) samples.shift();
  log(`perf ${name}=${sample.ms}`);
  return sample;
}

export function getPerfSamples(): PerfSample[] {
  return samples.slice();
}
