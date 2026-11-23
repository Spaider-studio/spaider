import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useLatestRequest } from "./useLatestRequest";

describe("useLatestRequest", () => {
  it("only the newest request is current", () => {
    const { result } = renderHook(() => useLatestRequest());

    const first = result.current();
    const second = result.current();

    expect(first()).toBe(false);
    expect(second()).toBe(true);
  });

  it("a request stays current until a newer one starts", () => {
    const { result } = renderHook(() => useLatestRequest());

    const only = result.current();

    expect(only()).toBe(true);
    expect(only()).toBe(true);
  });

  it("nothing is current once the component is gone", () => {
    const { result, unmount } = renderHook(() => useLatestRequest());
    const pending = result.current();

    unmount();

    expect(pending()).toBe(false);
  });
});
