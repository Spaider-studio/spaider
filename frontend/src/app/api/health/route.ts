import { NextResponse } from "next/server";

// BACKEND_HEALTH_URL: direct URL to the backend-api container, bypassing Kong.
// Kong does not expose a /health route, so we cannot go through BACKEND_API_URL
// (which points to http://kong:8000/api). Use the Docker service name directly.
const BACKEND_ROOT =
  process.env.BACKEND_HEALTH_URL ??
  process.env.BACKEND_API_URL?.replace(/\/api(\/v\d+)?$/, "") ??
  "http://localhost:8000";

// Thin proxy so the browser can reach /health (backend root) through Next.js.
// Called as GET /api/health → proxied to <backend>/health.
export async function GET() {
  try {
    const res = await fetch(`${BACKEND_ROOT}/health`, { cache: "no-store" });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    return NextResponse.json(
      { healthy: false, error: String(err) },
      { status: 503 }
    );
  }
}
