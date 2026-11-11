import { describe, expect, it, vi } from "vitest";
import { ApiClient, ApiError, mapErrorBody } from "../src/renderer/api/client";

const config = { baseUrl: "http://127.0.0.1:1234/", token: "secret-token" };

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("mapErrorBody", () => {
  it("maps the contract error shape and keeps extra fields", () => {
    const err = mapErrorBody(409, { detail: "Zu wenig Platz", code: "insufficient_space", required_bytes: 10, free_bytes: 3 });
    expect(err).toBeInstanceOf(ApiError);
    expect(err.detail).toBe("Zu wenig Platz");
    expect(err.code).toBe("insufficient_space");
    expect(err.status).toBe(409);
    expect(err.extra).toEqual({ required_bytes: 10, free_bytes: 3 });
  });

  it("falls back for non-JSON bodies", () => {
    const err = mapErrorBody(500, null);
    expect(err.code).toBe("http_error");
    expect(err.status).toBe(500);
    expect(err.detail).toContain("500");
  });

  it("maps 401 without body to unauthorized", () => {
    expect(mapErrorBody(401, "nope").code).toBe("unauthorized");
  });
});

describe("ApiClient", () => {
  it("sends the token header on every request and strips trailing slashes", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, { status: "ok" }));
    const client = new ApiClient(config, fetchMock as unknown as typeof fetch);
    await client.health();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:1234/api/health");
    expect((init.headers as Record<string, string>)["X-SpAIder-Token"]).toBe("secret-token");
  });

  it("throws ApiError with detail/code for 4xx JSON responses", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(409, { detail: "Kein Modell aktiv.", code: "no_model" }));
    const client = new ApiClient(config, fetchMock as unknown as typeof fetch);
    await expect(client.createConversation()).rejects.toMatchObject({ detail: "Kein Modell aktiv.", code: "no_model", status: 409 });
  });

  it("maps network failures to network_error", async () => {
    const fetchMock = vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    });
    const client = new ApiClient(config, fetchMock as unknown as typeof fetch);
    await expect(client.setupStatus()).rejects.toMatchObject({ code: "network_error", status: 0 });
  });

  it("serialises JSON bodies and query parameters", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, { results: [] }));
    const client = new ApiClient(config, fetchMock as unknown as typeof fetch);
    await client.memorySearch("Hallo Welt", 5);
    const [url] = fetchMock.mock.calls[0] as unknown as [string];
    expect(url).toBe("http://127.0.0.1:1234/api/memory/search?q=Hallo+Welt&limit=5");

    await client.patchSettings({ temperature: 0.3 });
    const [, init] = fetchMock.mock.calls[1] as unknown as [string, RequestInit];
    expect(init.method).toBe("PATCH");
    expect(init.body).toBe('{"temperature":0.3}');
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
  });
});
