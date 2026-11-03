import { describe, expect, it } from "vitest";
import { sameAccelerator, validateAccelerator } from "../src/shared-types/accelerator";

describe("validateAccelerator", () => {
  it("accepts the defaults and normalises spelling", () => {
    expect(validateAccelerator("Ctrl+Alt+Space")).toEqual({ ok: true, normalized: "Control+Alt+Space", error: null });
    expect(validateAccelerator("ctrl+alt+backspace").normalized).toBe("Control+Alt+Backspace");
    expect(validateAccelerator("CmdOrCtrl+Shift+k").normalized).toBe("CommandOrControl+Shift+K");
    expect(validateAccelerator("Alt+F9").normalized).toBe("Alt+F9");
    expect(validateAccelerator("Super+num5").normalized).toBe("Super+num5");
    expect(validateAccelerator(" Ctrl + Alt + 1 ").normalized).toBe("Control+Alt+1");
  });

  it("accepts a literal plus key", () => {
    expect(validateAccelerator("Ctrl++")).toEqual({ ok: true, normalized: "Control++", error: null });
  });

  it("rejects empty, unknown keys, missing key, two keys and modifiers after the key", () => {
    expect(validateAccelerator("").ok).toBe(false);
    expect(validateAccelerator("Ctrl+Alt+Foo").error).toMatch(/Unbekannte Taste/);
    expect(validateAccelerator("Ctrl+Alt").error).toMatch(/eigentliche Taste/);
    expect(validateAccelerator("Ctrl+A+B").error).toMatch(/eine Taste/);
    expect(validateAccelerator("A+Ctrl").error).toMatch(/vor der Taste/);
  });

  it("requires a strong modifier for global hotkeys", () => {
    expect(validateAccelerator("Space").ok).toBe(false);
    expect(validateAccelerator("Shift+Space").error).toMatch(/Ctrl, Alt oder Super/);
    expect(validateAccelerator("Shift+Alt+Space").ok).toBe(true);
  });
});

describe("sameAccelerator", () => {
  it("compares independent of spelling and modifier order", () => {
    expect(sameAccelerator("Ctrl+Alt+Space", "alt+control+space")).toBe(true);
    expect(sameAccelerator("Ctrl+Alt+Space", "Ctrl+Alt+Backspace")).toBe(false);
    expect(sameAccelerator("nonsense", "nonsense")).toBe(false);
  });
});
