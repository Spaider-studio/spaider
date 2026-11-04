import { describe, expect, it } from "vitest";
import type { LiveSources, ScreenSource, Task, WindowSource } from "../src/renderer/api/types";
import {
  applyShown,
  closePicker,
  confirmSource,
  effectiveTarget,
  initialSourceState,
  isSessionReferenced,
  needsNewSource,
  openPicker,
  preselectedId,
  qualityText,
  selectableSources,
  sessionStateText,
  targetLine,
} from "../src/renderer/store/overlay";

const win = (id: string, title: string, extra: Partial<WindowSource> = {}): WindowSource => ({
  id,
  hwnd: 1,
  pid: 2,
  process: "notepad.exe",
  exe: null,
  title,
  class: "Notepad",
  rect: { x: 0, y: 0, w: 100, h: 100 },
  monitor_id: "screen:1",
  minimized: false,
  is_self: false,
  ...extra,
});
const screen: ScreenSource = { id: "screen:1", name: "Bildschirm 1", rect: { x: 0, y: 0, w: 1920, h: 1080 }, scale: 1, primary: true };

describe("overlay source state", () => {
  it("first show: picker must be confirmed, the hint is only highlighted", () => {
    const s = applyShown(initialSourceState(), win("w1", "Editor"));
    expect(s.pickerOpen).toBe(true);
    expect(s.confirmed).toBeNull();
    expect(effectiveTarget(s)).toBeNull();
    expect(preselectedId(s)).toBe("w1");
    const confirmed = confirmSource(s, win("w1", "Editor"));
    expect(confirmed.pickerOpen).toBe(false);
    expect(confirmed.everConfirmed).toBe(true);
    expect(effectiveTarget(confirmed)?.id).toBe("w1");
  });

  it("first show without a hint still opens the picker", () => {
    const s = applyShown(initialSourceState(), null);
    expect(s.pickerOpen).toBe(true);
    expect(preselectedId(s)).toBeNull();
  });

  it("later shows preselect a different foreground hint and require one confirmation", () => {
    let s = confirmSource(applyShown(initialSourceState(), win("w1", "Editor")), win("w1", "Editor"));
    s = applyShown(s, win("w2", "Browser", { process: "msedge.exe" }));
    expect(s.pickerOpen).toBe(false);
    expect(s.hintPending).toBe(true);
    expect(effectiveTarget(s)?.id).toBe("w2");
    expect(targetLine(effectiveTarget(s))).toBe("Ziel: Browser – msedge.exe");
    // Enter / click confirms the hint (no silent switch before that).
    expect(s.confirmed?.id).toBe("w1");
    s = confirmSource(s, effectiveTarget(s)!);
    expect(s.confirmed?.id).toBe("w2");
    expect(s.hintPending).toBe(false);
  });

  it("same window again just refreshes the title; a missing hint keeps the confirmed source", () => {
    let s = confirmSource(applyShown(initialSourceState(), win("w1", "Editor")), win("w1", "Editor"));
    s = applyShown(s, win("w1", "Editor – neu"));
    expect(s.hintPending).toBe(false);
    expect((s.confirmed as WindowSource).title).toBe("Editor – neu");
    s = applyShown(s, null);
    expect(s.hintPending).toBe(false);
    expect(effectiveTarget(s)?.id).toBe("w1");
  });

  it("never offers or confirms SpAIder's own windows", () => {
    const self = win("self", "SpAIder Foldable", { is_self: true, process: "SpAIder.exe" });
    const s = applyShown(initialSourceState(), self);
    expect(s.hint).toBeNull();
    const confirmed = confirmSource(s, self);
    expect(confirmed.confirmed).toBeNull();
    const sources: LiveSources = { windows: [self, win("w1", "Editor")], screens: [screen], foreground_id: "self" };
    const sel = selectableSources(sources);
    expect(sel.windows.map((w) => w.id)).toEqual(["w1"]);
    expect(sel.screens).toHaveLength(1);
    expect(selectableSources(null)).toEqual({ windows: [], screens: [] });
  });

  it("opens and closes the picker and accepts screens", () => {
    let s = openPicker(initialSourceState());
    expect(s.pickerOpen).toBe(true);
    s = closePicker(s);
    expect(s.pickerOpen).toBe(false);
    s = confirmSource(s, screen);
    expect(targetLine(effectiveTarget(s))).toBe("Ziel: Bildschirm 1 – Bildschirm (primär)");
    expect(targetLine(null)).toBe("Ziel: nicht gewählt");
  });
});

describe("quality texts", () => {
  it("maps non-ok qualities to short German texts", () => {
    expect(qualityText("ok")).toBeNull();
    expect(qualityText(null)).toBeNull();
    expect(qualityText("lost")).toMatch(/verloren/);
    expect(qualityText("minimized")).toMatch(/minimiert/);
    expect(qualityText("black")).toMatch(/schwarz/);
    expect(sessionStateText("lost")).toMatch(/neu wählen/);
    expect(sessionStateText("active")).toBeNull();
  });

  it("offers a new source for lost / minimized / black", () => {
    expect(needsNewSource("ok", "active")).toBe(false);
    expect(needsNewSource("lost", "active")).toBe(true);
    expect(needsNewSource("minimized", "active")).toBe(true);
    expect(needsNewSource("black", "active")).toBe(true);
    expect(needsNewSource("occluded", "active")).toBe(false);
    expect(needsNewSource("ok", "lost")).toBe(true);
  });
});

describe("session lifetime", () => {
  const task = (state: Task["state"], live_session_id: string | null): Task =>
    ({ id: `t-${state}`, state, live_session_id, goal: "x" }) as unknown as Task;

  it("keeps a session only while an unfinished task references it", () => {
    expect(isSessionReferenced([], "s1")).toBe(false);
    expect(isSessionReferenced([task("executing", "s2")], "s1")).toBe(false);
    expect(isSessionReferenced([task("executing", "s1")], "s1")).toBe(true);
    expect(isSessionReferenced([task("waiting_for_user", "s1")], "s1")).toBe(true);
    expect(isSessionReferenced([task("paused", "s1")], "s1")).toBe(true);
    for (const done of ["completed", "failed", "cancelled"] as const) {
      expect(isSessionReferenced([task(done, "s1")], "s1")).toBe(false);
    }
    expect(isSessionReferenced([task("completed", "s1"), task("planning", "s1")], "s1")).toBe(true);
  });
});
