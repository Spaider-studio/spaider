import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({ getServiceHealth: vi.fn(), getSwarmHealth: vi.fn() }));

import { getServiceHealth, getSwarmHealth } from "@/lib/api";
import ServiceConnectivityPanel from "./ServiceConnectivityPanel";

type States = Record<string, "ok" | "unavailable" | "disabled">;

function health(services: States) {
  vi.mocked(getServiceHealth).mockResolvedValue({
    app: "SpAIder",
    version: "0.5.1",
    environment: "development",
    services,
    healthy: true,
  });
}

beforeEach(() => {
  vi.mocked(getServiceHealth).mockReset();
  vi.mocked(getSwarmHealth).mockReset();
  vi.mocked(getSwarmHealth).mockResolvedValue({ active_agents: [], total: 0 });
});

describe("ServiceConnectivityPanel", () => {
  it("lists the services the backend reports, not invented ones", async () => {
    health({ neo4j: "ok", redis: "ok", kafka: "ok", clickhouse: "ok", postgres: "ok", vector_index: "ok" });

    render(<ServiceConnectivityPanel />);

    for (const label of ["Neo4j", "Redis", "Kafka", "ClickHouse", "Postgres", "Vector index"]) {
      await screen.findByText(label);
    }
    expect(screen.queryByText("Flink")).toBeNull();
    expect(screen.queryByText("Schema Registry")).toBeNull();
  });

  it("shows a service that is switched off as not used, and not as a failure", async () => {
    // The Lite profile runs without Kafka. That is a choice, not an outage.
    health({ neo4j: "ok", redis: "disabled", kafka: "disabled", vector_index: "ok" });

    render(<ServiceConnectivityPanel />);

    expect(await screen.findByText("All services healthy")).toBeTruthy();
    expect(screen.getAllByText("Not used in this setup")).toHaveLength(2);
    expect(screen.queryByText("Unavailable")).toBeNull();
  });

  it("names the services that are down", async () => {
    health({ neo4j: "ok", redis: "ok", kafka: "unavailable", vector_index: "unavailable" });

    render(<ServiceConnectivityPanel />);

    expect(await screen.findByText("2 services unavailable: Kafka, Vector index")).toBeTruthy();
  });

  it("says one service, not one services", async () => {
    health({ neo4j: "ok", kafka: "unavailable" });

    render(<ServiceConnectivityPanel />);

    expect(await screen.findByText("1 service unavailable: Kafka")).toBeTruthy();
  });

  it("does not claim anything before the backend has answered", () => {
    vi.mocked(getServiceHealth).mockReturnValue(new Promise(() => {}));

    render(<ServiceConnectivityPanel />);

    expect(screen.getByText("Checking services...")).toBeTruthy();
    expect(screen.queryByText("All services healthy")).toBeNull();
  });

  it("shows the error when the health request fails", async () => {
    vi.mocked(getServiceHealth).mockRejectedValue(new Error("Health check failed: 502"));

    render(<ServiceConnectivityPanel />);

    await waitFor(() => expect(screen.getByText("Health check failed: 502")).toBeTruthy());
    expect(screen.queryByText("All services healthy")).toBeNull();
  });
});
