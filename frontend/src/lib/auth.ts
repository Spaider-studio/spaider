// The key lives in sessionStorage so it ends with the tab and is never in a URL.
const STORAGE_KEY = "spaider.apiKey";

export function getApiKey(): string | null {
  if (typeof window === "undefined") return null;
  return window.sessionStorage.getItem(STORAGE_KEY);
}

export function setApiKey(key: string): void {
  window.sessionStorage.setItem(STORAGE_KEY, key);
}

export function clearApiKey(): void {
  window.sessionStorage.removeItem(STORAGE_KEY);
}

/** fetch with the signed-in key. A 401 means the key is no longer good: go to login. */
export async function authFetch(input: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  const key = getApiKey();
  if (key) headers.set("X-Api-Key", key);
  const response = await fetch(input, { ...init, headers });
  if (response.status === 401 && typeof window !== "undefined" && window.location.pathname !== "/login") {
    clearApiKey();
    window.location.assign("/login");
  }
  return response;
}

/** A single-use ticket for a WebSocket or EventSource URL, which cannot carry headers. */
export async function connectionTicket(baseUrl = "/api/v1"): Promise<string> {
  const response = await authFetch(`${baseUrl}/auth/ticket`, { method: "POST" });
  if (!response.ok) throw new Error(`Ticket request failed: ${response.status}`);
  return ((await response.json()) as { ticket: string }).ticket;
}
