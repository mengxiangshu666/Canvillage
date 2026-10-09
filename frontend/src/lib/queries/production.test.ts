import { describe, expect, it } from "vitest";

import { productionControlRefetchInterval } from "./production";

describe("production control polling", () => {
  it("refreshes while a run is working or applying a pause", () => {
    expect(productionControlRefetchInterval("running")).toBe(2_000);
    expect(productionControlRefetchInterval("pausing")).toBe(2_000);
  });

  it("uses the idle interval after a run stops changing", () => {
    expect(productionControlRefetchInterval("paused")).toBe(10_000);
    expect(productionControlRefetchInterval("blocked")).toBe(10_000);
    expect(productionControlRefetchInterval(undefined)).toBe(10_000);
  });
});
