import { describe, expect, it } from "vitest";
import { IPC, isAbsoluteFilePath, isOpenableExtension, OPENABLE_EXTENSIONS, pathExtension } from "../src/shared-types/bridge";

describe("openPath / revealPath helpers", () => {
  it("knows the IPC channel names", () => {
    expect(IPC.openPath).toBe("spaider:open-path");
    expect(IPC.revealPath).toBe("spaider:reveal-path");
  });

  it("extracts lower-cased extensions", () => {
    expect(pathExtension("C:\\out\\Bericht.MD")).toBe(".md");
    expect(pathExtension("/tmp/a.tar.gz")).toBe(".gz");
    expect(pathExtension("C:\\out\\README")).toBe("");
    expect(pathExtension("C:\\out\\.gitignore")).toBe("");
  });

  it("opens only text / document types with the default app", () => {
    for (const ext of OPENABLE_EXTENSIONS) expect(isOpenableExtension(`C:\\x\\beleg${ext}`)).toBe(true);
    expect(isOpenableExtension("C:\\x\\Beleg.PDF")).toBe(true);
    expect(isOpenableExtension("C:\\x\\setup.exe")).toBe(false);
    expect(isOpenableExtension("C:\\x\\script.ps1")).toBe(false);
    expect(isOpenableExtension("C:\\x\\archiv.zip")).toBe(false);
    expect(isOpenableExtension("C:\\x\\ordner")).toBe(false);
  });

  it("accepts only absolute paths without control characters", () => {
    expect(isAbsoluteFilePath("C:\\Users\\x\\out.md")).toBe(true);
    expect(isAbsoluteFilePath("D:/data/out.md")).toBe(true);
    expect(isAbsoluteFilePath("\\\\server\\share\\out.md")).toBe(true);
    expect(isAbsoluteFilePath("/home/x/out.md")).toBe(true);
    expect(isAbsoluteFilePath("out.md")).toBe(false);
    expect(isAbsoluteFilePath("..\\out.md")).toBe(false);
    expect(isAbsoluteFilePath("C:\\x\\out\n.md")).toBe(false);
    expect(isAbsoluteFilePath("")).toBe(false);
    expect(isAbsoluteFilePath(123 as unknown as string)).toBe(false);
  });
});
