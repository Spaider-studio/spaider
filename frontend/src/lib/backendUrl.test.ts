import { describe, expect, it } from "vitest";
import { backendUrl } from "./constants";

describe("backendUrl", () => {
  it("uses the configured address when there is one", () => {
    expect(backendUrl("https://api.example.com/api/v1", { protocol: "http:", hostname: "localhost" })).toBe(
      "https://api.example.com/api/v1"
    );
  });

  it("follows the host the Studio was opened on, instead of assuming localhost", () => {
    // The address was always localhost:8000. Opened from another machine, or as
    // 127.0.0.1, uploads and exports went to the wrong place or the real stack.
    expect(backendUrl(undefined, { protocol: "http:", hostname: "192.168.1.20" })).toBe(
      "http://192.168.1.20:8000/api/v1"
    );
    expect(backendUrl(undefined, { protocol: "https:", hostname: "studio.example.com" })).toBe(
      "https://studio.example.com:8000/api/v1"
    );
  });

  it("treats an empty setting as not set", () => {
    // A Docker build argument with no value arrives as an empty string.
    expect(backendUrl("", { protocol: "http:", hostname: "localhost" })).toBe("http://localhost:8000/api/v1");
  });

  it("falls back to localhost where there is no page, as in a server render", () => {
    expect(backendUrl(undefined, undefined)).toBe("http://localhost:8000/api/v1");
  });
});
