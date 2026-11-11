import { describe, expect, it } from "vitest";
import { ApiError, mapErrorBody } from "../src/renderer/api/client";
import { jobErrorText, resolveErrorInfo } from "../src/renderer/lib/modelErrors";

describe("resolveErrorInfo", () => {
  it("explains a 409 gated response as account/approval required without offering a workaround", () => {
    const err = mapErrorBody(409, { detail: "Das Repository ist zugangsbeschränkt (gated).", code: "gated", requires_account: true });
    const info = resolveErrorInfo(err);
    expect(info.gated).toBe(true);
    expect(info.code).toBe("gated");
    expect(info.title).toBe("Modell erfordert ein Konto");
    expect(info.text).toContain("zugangsbeschränkt");
    expect(info.text).toContain("Hugging-Face-Konto");
    expect(info.text).not.toMatch(/Umgehung|Workaround|Token eingeben/i);
  });

  it("treats requires_account without the gated code as gated too", () => {
    const info = resolveErrorInfo(new ApiError("Nur mit Konto.", "forbidden", 403, { requires_account: true }));
    expect(info.gated).toBe(true);
  });

  it("maps split_incomplete and not_found with the core detail first", () => {
    const split = resolveErrorInfo(new ApiError("Teil 2 von 3 fehlt.", "split_incomplete", 409));
    expect(split.gated).toBe(false);
    expect(split.title).toBe("Prüfung nicht möglich");
    expect(split.text).toBe("Teil 2 von 3 fehlt.");
    const missing = resolveErrorInfo(new ApiError("HTTP 404", "not_found", 404));
    expect(missing.text).toContain("nicht gefunden");
  });

  it("falls back for non-API errors", () => {
    const info = resolveErrorInfo(new Error("Keine Verbindung zum SpAIder-Kern."));
    expect(info).toMatchObject({ code: "unknown", gated: false, text: "Keine Verbindung zum SpAIder-Kern." });
  });
});

describe("jobErrorText (import codes)", () => {
  it("adds actionable context for the new import error codes", () => {
    expect(jobErrorText({ code: "split_incomplete", detail: "Gefunden: 1 von 3." })).toContain("Gefunden: 1 von 3.");
    expect(jobErrorText({ code: "split_incomplete", detail: "Gefunden: 1 von 3." })).toContain("alle Teile");
    expect(jobErrorText({ code: "not_chat_model", detail: null })).toContain("kein Chat-Modell");
    expect(jobErrorText({ code: "arch_unsupported", detail: "" })).toContain("llama.cpp");
    expect(jobErrorText({ code: "role_mismatch", detail: "" })).toContain("Rolle");
    expect(jobErrorText({ code: "gguf_invalid", detail: "Die Datei ist keine gültige GGUF-Datei." })).toBe("Die Datei ist keine gültige GGUF-Datei.");
    expect(jobErrorText({ code: "mystery", detail: "" }, "Fallback.")).toBe("Fallback.");
    expect(jobErrorText(null, "Fallback.")).toBe("Fallback.");
  });
});
