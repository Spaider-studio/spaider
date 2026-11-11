import { describe, expect, it } from "vitest";
import type { ActivityEvent } from "../src/renderer/api/types";
import {
  createTrack,
  MAX_EVENTS_PER_TRACK,
  promptPercent,
  reduceActivity,
  stepText,
  summarizeActivity,
} from "../src/renderer/store/activity";

const ev = (seq: number, phase: string, state: ActivityEvent["state"], extra: Partial<ActivityEvent> = {}): ActivityEvent => ({
  request_id: "req-1",
  seq,
  phase,
  state,
  t_ms: seq * 100,
  at: "2026-11-02T10:00:00Z",
  message_de: extra.message_de ?? `${phase} ${state}`,
  counters: null,
  source_ids: [],
  ...extra,
});

describe("activity reducer", () => {
  it("accepts events in seq order and ignores lower or equal seq", () => {
    let track = createTrack("req-1", ev(0, "memory_search", "started"), 1000);
    expect(track.events).toHaveLength(1);
    expect(track.startedAt).toBe(1000);
    track = reduceActivity(track, ev(1, "memory_search", "done"));
    const stale = reduceActivity(track, ev(1, "prompt_build", "started"));
    expect(stale).toBe(track);
    const older = reduceActivity(track, ev(0, "prompt_build", "started"));
    expect(older).toBe(track);
    track = reduceActivity(track, ev(5, "prompt_build", "started"));
    expect(track.events.map((e) => e.seq)).toEqual([0, 1, 5]);
    expect(track.lastSeq).toBe(5);
    expect(track.current.phase).toBe("prompt_build");
  });

  it("discards events of a different request id", () => {
    const track = createTrack("req-1", ev(0, "memory_search", "started"), 0);
    const foreign = reduceActivity(track, { ...ev(1, "generating", "started"), request_id: "req-OLD" });
    expect(foreign).toBe(track);
    expect(track.events).toHaveLength(1);
  });

  it("keeps the live step text with measured percent and Token/s only", () => {
    const pp = ev(3, "prompt_processing", "progress", { message_de: "Kontext wird verarbeitet", counters: { prompt_total: 412, prompt_processed: 256 } });
    expect(promptPercent(pp)).toBe(62);
    expect(stepText(pp)).toBe("Kontext wird verarbeitet (62 %)");
    const noTotal = ev(4, "prompt_processing", "progress", { counters: { prompt_processed: 10 } });
    expect(promptPercent(noTotal)).toBeNull();
    const gen = ev(5, "generating", "progress", { message_de: "Antwort wird geschrieben", counters: { tokens: 12, tokens_per_second: 24.04 } });
    expect(promptPercent(gen)).toBeNull();
    expect(stepText(gen)).toBe("Antwort wird geschrieben · 24 Token/s");
    const genNoCounters = ev(6, "generating", "started", { message_de: "Antwort wird geschrieben" });
    expect(stepText(genNoCounters)).toBe("Antwort wird geschrieben");
  });

  it("collapses on answer_done using real counters", () => {
    let track = createTrack("req-1", ev(0, "memory_search", "started"), 0);
    track = reduceActivity(track, ev(1, "generating", "progress", { counters: { tokens: 50, tokens_per_second: 23.6 } }));
    expect(summarizeActivity(track).outcome).toBe("live");
    track = reduceActivity(track, ev(2, "answer_done", "done", { t_ms: 3812, message_de: "Antwort fertig", counters: { tokens: 90, tokens_per_second: 24.0 } }));
    expect(track.outcome).toBe("done");
    expect(summarizeActivity(track)).toEqual({ text: "Antwort fertig · 3,8 s · 24 Token/s", memory: null, outcome: "done" });

    track = reduceActivity(track, ev(3, "saved", "done", { message_de: "Gespräch gespeichert" }));
    expect(summarizeActivity(track).memory).toBe("Gespräch gespeichert");
    track = reduceActivity(track, ev(4, "postprocess_pending", "started"));
    expect(summarizeActivity(track).memory).toBe("Gedächtnis-Nachverarbeitung ausstehend");
    track = reduceActivity(track, ev(5, "postprocess_running", "started"));
    expect(summarizeActivity(track).memory).toBe("Gedächtnis wird aktualisiert");
    track = reduceActivity(track, ev(6, "postprocess_done", "done"));
    expect(summarizeActivity(track).memory).toBe("Gedächtnis aktualisiert");
    // later events never reopen the answer
    expect(track.outcome).toBe("done");
    expect(summarizeActivity(track).text).toBe("Antwort fertig · 3,8 s · 24 Token/s");
  });

  it("falls back to the last generating counters when answer_done carries none", () => {
    let track = createTrack("req-1", ev(0, "generating", "progress", { counters: { tokens: 5, tokens_per_second: 9.96 } }), 0);
    track = reduceActivity(track, ev(1, "answer_done", "done", { t_ms: 1234 }));
    expect(summarizeActivity(track).text).toBe("Antwort fertig · 1,2 s · 10 Token/s");
    let bare = createTrack("req-1", ev(0, "generating", "started"), 0);
    bare = reduceActivity(bare, ev(1, "answer_done", "done", { t_ms: 500 }));
    expect(summarizeActivity(bare).text).toBe("Antwort fertig · 0,5 s");
  });

  it("marks cancelled and failed outcomes", () => {
    let track = createTrack("req-1", ev(0, "generating", "started"), 0);
    track = reduceActivity(track, ev(1, "answer_done", "cancelled", { t_ms: 1500, message_de: "Abgebrochen", counters: { tokens: 3, tokens_per_second: 2.0 } }));
    expect(track.outcome).toBe("cancelled");
    expect(summarizeActivity(track).text).toBe("Abgebrochen · 1,5 s · 2,0 Token/s");

    let failed = createTrack("req-1", ev(0, "generating", "started"), 0);
    failed = reduceActivity(failed, ev(1, "generating", "failed", { message_de: "Fehlgeschlagen: Die Laufzeit hat die Verbindung beendet" }));
    expect(failed.outcome).toBe("failed");
    expect(summarizeActivity(failed)).toMatchObject({ outcome: "failed", text: "Fehlgeschlagen: Die Laufzeit hat die Verbindung beendet" });
    let failedPhase = createTrack("req-1", ev(0, "generating", "started"), 0);
    failedPhase = reduceActivity(failedPhase, ev(1, "failed", "failed", { message_de: "Modell konnte nicht geladen werden" }));
    expect(summarizeActivity(failedPhase).text).toBe("Fehlgeschlagen: Modell konnte nicht geladen werden");
  });

  it("keeps post-processing failures separate from the answer outcome", () => {
    let track = createTrack("req-1", ev(0, "answer_done", "done", { t_ms: 900 }), 0);
    track = reduceActivity(track, ev(1, "postprocess_running", "failed", { message_de: "Extraktion fehlgeschlagen" }));
    expect(track.outcome).toBe("done");
    expect(track.postprocess).toBe("failed");
    expect(summarizeActivity(track).memory).toBe("Gedächtnis-Nachverarbeitung fehlgeschlagen");
  });

  it("bounds the stored events per track", () => {
    let track = createTrack("req-1", ev(0, "generating", "started"), 0);
    for (let i = 1; i <= MAX_EVENTS_PER_TRACK + 20; i++) track = reduceActivity(track, ev(i, "generating", "progress"));
    expect(track.events).toHaveLength(MAX_EVENTS_PER_TRACK);
    expect(track.events[0].seq).toBe(21);
    expect(track.lastSeq).toBe(MAX_EVENTS_PER_TRACK + 20);
  });
});
