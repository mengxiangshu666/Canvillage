import { beforeEach, describe, expect, it, vi } from "vitest";

import { apiCall } from "@/api/client";
import { fetchFreezoneJobResultWithRetry } from "@/api/ops";

vi.mock("@/api/client", () => ({
  apiCall: vi.fn(),
}));

describe("fetchFreezoneJobResultWithRetry", () => {
  beforeEach(() => {
    vi.mocked(apiCall).mockReset();
  });

  it("recovers when the completed task result is not immediately readable", async () => {
    vi.mocked(apiCall)
      .mockResolvedValueOnce({ url: "", size: 0 })
      .mockRejectedValueOnce(new Error("temporary relay failure"))
      .mockResolvedValueOnce({ url: "/static/video.mp4", size: 1024 });

    await expect(
      fetchFreezoneJobResultWithRetry(
        "demo",
        "freezone_video_gen",
        "job-1",
        { attempts: 3, delayMs: 0 },
      ),
    ).resolves.toEqual({ url: "/static/video.mp4", size: 1024 });
    expect(apiCall).toHaveBeenCalledTimes(3);
  });

  it("returns the final retrieval error after the retry budget is exhausted", async () => {
    vi.mocked(apiCall).mockRejectedValue(new Error("relay offline"));

    await expect(
      fetchFreezoneJobResultWithRetry(
        "demo",
        "freezone_video_gen",
        "job-2",
        { attempts: 2, delayMs: 0 },
      ),
    ).rejects.toThrow("relay offline");
    expect(apiCall).toHaveBeenCalledTimes(2);
  });
});
