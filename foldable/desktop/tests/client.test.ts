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

describe("ApiClient v1.5 (execution core)", () => {
  it("runs a workflow, compiles a task and patches a workflow with the contract bodies", async () => {
    const fetchMock = vi.fn(async (url: string) => {
      if (url.includes("/run")) return jsonResponse(201, { task: { id: "task_1" } });
      if (url.includes("/compile")) return jsonResponse(201, { workflow: { id: "wf_1", name: "X" } });
      return jsonResponse(200, { workflow: { id: "wf_1", status: "demoted" } });
    });
    const client = new ApiClient(config, fetchMock as unknown as typeof fetch);
    const task = await client.runWorkflow("wf 1", { parameters: { zielordner: "C:\\out" }, mode: "replan" });
    expect(task.id).toBe("task_1");
    let [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:1234/api/agent/workflows/wf%201/run");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ parameters: { zielordner: "C:\\out" }, mode: "replan" });

    const wf = await client.compileWorkflow("task_1", "Name", [{ name: "p", description_de: "d", example: "e", value: "v" }]);
    expect(wf.id).toBe("wf_1");
    [url, init] = fetchMock.mock.calls[1] as unknown as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:1234/api/agent/tasks/task_1/compile");
    expect(JSON.parse(String(init.body))).toEqual({ name: "Name", parameters: [{ name: "p", description_de: "d", example: "e", value: "v" }] });

    const demoted = await client.patchWorkflow("wf_1", { status: "demoted" });
    expect(demoted.status).toBe("demoted");
    [url, init] = fetchMock.mock.calls[2] as unknown as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:1234/api/agent/workflows/wf_1");
    expect(init.method).toBe("PATCH");
  });

  it("addresses the binding and teach endpoints", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, { session: { id: "t1" }, binding: { role: "r" }, ok: true, new_steps: 1, step: {} }));
    const client = new ApiClient(config, fetchMock as unknown as typeof fetch);
    await client.putBinding("ablage", "dir", "C:\\Ablage");
    await client.deleteBinding("ablage");
    await client.teachCapture("t1", "Speichern");
    await client.teachCapture("t1");
    await client.teachRemoveStep("t1", 2);
    await client.teachFinish("t1", [], [{ kind: "page_text", target: "https://x", expect: ["Fertig"], description_de: "Fertig sichtbar" }]);
    const calls = fetchMock.mock.calls as unknown as [string, RequestInit][];
    expect(calls.map(([u, i]) => `${i.method} ${u.replace("http://127.0.0.1:1234", "")}`)).toEqual([
      "PUT /api/agent/bindings",
      "DELETE /api/agent/bindings/ablage",
      "POST /api/agent/teach/t1/capture",
      "POST /api/agent/teach/t1/capture",
      "DELETE /api/agent/teach/t1/steps/2",
      "POST /api/agent/teach/t1/finish",
    ]);
    expect(JSON.parse(String(calls[0][1].body))).toEqual({ role: "ablage", kind: "dir", target: "C:\\Ablage" });
    expect(JSON.parse(String(calls[2][1].body))).toEqual({ invoke_name: "Speichern" });
    expect(JSON.parse(String(calls[3][1].body))).toEqual({});
    expect(JSON.parse(String(calls[5][1].body)).checks[0].kind).toBe("page_text");
  });

  it("forwards `event: step` of the task stream", async () => {
    const encoder = new TextEncoder();
    const raw = 'event: task\ndata: {"id":"t1"}\n\nevent: step\ndata: {"kind":"read","message_de":"Datei gelesen","at":"2026-11-11T10:00:00Z"}\n\nevent: unknown\ndata: {}\n\nevent: done\ndata: {"id":"t1"}\n\n';
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode(raw));
        controller.close();
      },
    });
    const fetchMock = vi.fn(async () => new Response(stream, { status: 200, headers: { "Content-Type": "text/event-stream" } }));
    const client = new ApiClient(config, fetchMock as unknown as typeof fetch);
    const seen: string[] = [];
    await client.taskEvents("t1", (ev) => seen.push(ev.type));
    expect(seen).toEqual(["task", "step", "done"]);
  });
});
