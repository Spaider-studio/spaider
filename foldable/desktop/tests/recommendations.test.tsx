import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { ModelCatalogEntry, RecommendationPick } from "../src/renderer/api/types";
import RecommendationCard from "../src/renderer/components/models/RecommendationCard";
import { ResolveResult } from "../src/renderer/components/models/ModelSearchPanel";

afterEach(cleanup);

const GB = 1024 ** 3;

const entry = (over: Partial<ModelCatalogEntry> = {}): ModelCatalogEntry => ({
  id: "qwen3-1.7b-q8_0",
  name: "Qwen3 1.7B (Q8_0)",
  role: "chat",
  family: "Qwen3",
  publisher: "Qwen",
  source: "official",
  repo: "Qwen/Qwen3-1.7B-GGUF",
  revision: "abc",
  repo_url: "https://huggingface.co/Qwen/Qwen3-1.7B-GGUF",
  files: [{ path: "Qwen3-1.7B-Q8_0.gguf", size: 1834426016, sha256: "ab" }],
  total_bytes: 1834426016,
  license: { id: "apache-2.0", name: "Apache 2.0", url: "https://huggingface.co/x", kind: "open-source", gated: false, requires_account: false },
  architecture: "qwen3",
  parameters: "1.7B",
  quantization: "Q8_0",
  context_length: 32768,
  embedding_dimensions: null,
  min_ram_bytes: 4 * GB,
  recommended_ram_bytes: 8 * GB,
  support: "executed",
  description_de: "",
  fit: { level: "recommended", message_de: "Passt gut." },
  installed_model_id: null,
  verification: { status: "executed", date: "2026-10-28T14:00:00Z", scope: "b12032" },
  ...over,
});

const pick = (over: Partial<RecommendationPick> = {}): RecommendationPick => ({
  slot: "balanced",
  entry: entry(),
  reasons_de: ["Gute Qualität bei 24 Token/s", "Nur 1,7 GB Download"],
  estimate: { ram_bytes: 2.3 * GB, vram_bytes: 2.1 * GB, download_bytes: 1834426016, context_tokens: 8192 },
  measured: null,
  installed_model_id: null,
  ...over,
});

describe("RecommendationCard", () => {
  it("renders slot, reasons, sizes and the measured value when present", () => {
    render(
      <RecommendationCard
        pick={pick({ measured: { backend: "vulkan", tokens_per_second: 24.3, time_to_first_token_ms: 310, prompt_tokens_per_second: 420, measured_at: "2026-10-28T14:12:00Z", fingerprint: "fp" } })}
        onDownload={() => undefined}
      />,
    );
    expect(screen.getByText("Ausgewogen")).toBeTruthy();
    expect(screen.getByText("Gute Qualität bei 24 Token/s")).toBeTruthy();
    expect(screen.getByText("1,7 GB")).toBeTruthy();
    expect(screen.getByTestId("measured").textContent).toContain("gemessen auf diesem Rechner: 24 Token/s");
    expect(screen.getByTestId("measured").textContent).toContain("Vulkan");
    expect(screen.queryByTestId("measured-none")).toBeNull();
    expect(screen.getByText("getestet")).toBeTruthy();
    expect(screen.getByText("Open Source")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Herunterladen/ })).toBeTruthy();
  });

  it("says 'noch nicht gemessen' without a measurement and never invents numbers", () => {
    const { container } = render(<RecommendationCard pick={pick({ slot: "fast", measured: null })} onDownload={() => undefined} />);
    expect(screen.getByText("Schnell und sparsam")).toBeTruthy();
    expect(screen.getByTestId("measured-none").textContent).toBe("noch nicht gemessen");
    expect(container.textContent).not.toContain("gemessen auf diesem Rechner");
  });

  it("shows the account warning for gated models and disables the download", () => {
    render(
      <RecommendationCard
        pick={pick({
          slot: "quality",
          entry: entry({ license: { id: "gemma", name: "Gemma Terms", url: "https://x", kind: "other", gated: true, requires_account: true }, verification: { status: "unverified", date: null, scope: null } }),
        })}
        onDownload={() => undefined}
      />,
    );
    expect(screen.getByText("Mehr Qualität")).toBeTruthy();
    expect(screen.getByTestId("gated-notice")).toBeTruthy();
    expect(screen.getByText("Konto erforderlich")).toBeTruthy();
    expect(screen.getByText("ungeprüft")).toBeTruthy();
    expect((screen.getByRole("button", { name: /Herunterladen/ }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("marks installed picks", () => {
    render(<RecommendationCard pick={pick({ installed_model_id: "m1" })} onDownload={() => undefined} />);
    expect(screen.getByText("Installiert")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Herunterladen/ })).toBeNull();
  });
});

describe("ResolveResult", () => {
  it("lists split files with and without checksum and the compatibility verdict", () => {
    const e = entry({
      id: "hf:unsloth/Qwen3-30B-A3B-GGUF:part1",
      files: [
        { path: "Qwen3-30B-Q4_K_M-00001-of-00002.gguf", size: 9_200_000_000, sha256: "11" },
        { path: "Qwen3-30B-Q4_K_M-00002-of-00002.gguf", size: 8_900_000_000, sha256: null },
      ],
      verification: { status: "metadata", date: null, scope: null },
    });
    render(
      <ResolveResult
        result={{ entry: e, files: e.files, compatibility: { ok: true, reasons_de: ["Architektur unterstützt"], architecture: "qwen3", context_length: 40960, type: "chat" }, fit: { level: "tight", message_de: "Knapp." } }}
        onDownload={() => undefined}
      />,
    );
    expect(screen.getAllByText("Prüfsumme vorhanden")).toHaveLength(1);
    expect(screen.getAllByText("keine Prüfsumme")).toHaveLength(1);
    expect(screen.getByText(/Mehrteiliges Modell/)).toBeTruthy();
    expect(screen.getByText("Kompatibel mit der mitgelieferten Laufzeit")).toBeTruthy();
    expect(screen.getByText("nach Metadaten geprüft")).toBeTruthy();
    expect(screen.getByText("Knapp")).toBeTruthy();
    expect((screen.getByRole("button", { name: /Herunterladen/ }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("disables the download for incompatible models", () => {
    const e = entry({ id: "hf:x/y-base:f", type: "base" });
    render(
      <ResolveResult
        result={{ entry: e, files: e.files, compatibility: { ok: false, reasons_de: ["Kein Chat-Template"], architecture: "qwen3", context_length: 4096, type: "base" }, fit: { level: "ok", message_de: "Passt." } }}
        onDownload={() => undefined}
      />,
    );
    expect(screen.getByText("Nicht kompatibel")).toBeTruthy();
    expect(screen.getByText("Kein Chat-Template")).toBeTruthy();
    expect((screen.getByRole("button", { name: /Herunterladen/ }) as HTMLButtonElement).disabled).toBe(true);
  });
});
