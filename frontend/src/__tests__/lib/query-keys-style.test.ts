import { describe, expect, it } from "vitest";

import { queryKeys } from "@/lib/query-keys";

describe("style detail query key", () => {
  it("isolates same-id custom styles by project", () => {
    expect(queryKeys.style("project-a", "shared-style")).not.toEqual(
      queryKeys.style("project-b", "shared-style"),
    );
  });
});
