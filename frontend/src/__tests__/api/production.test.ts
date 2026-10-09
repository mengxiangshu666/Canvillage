// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeEach, describe, expect, it, vi } from "vitest";

import { apiCall } from "@/api/client";
import {
  commandProductionRun,
  getProductionControl,
  startProductionRun,
  uploadProductionNovel,
} from "@/api/production";

vi.mock("@/api/client", () => ({
  apiCall: vi.fn(),
}));

describe("production control api", () => {
  beforeEach(() => {
    vi.mocked(apiCall).mockReset();
  });

  it("loads the live control snapshot with an abort signal", async () => {
    const controller = new AbortController();
    vi.mocked(apiCall).mockResolvedValueOnce({ latest_run: null });

    await getProductionControl("my project", controller.signal);

    expect(apiCall).toHaveBeenCalledWith(
      "projects/my%20project/production/control",
      { signal: controller.signal },
    );
  });

  it("uploads a novel through the existing ingest endpoint", async () => {
    const file = new File(["chapter one"], "story.txt", { type: "text/plain" });
    vi.mocked(apiCall).mockResolvedValueOnce({ filename: "stored.txt", size: 11 });

    await uploadProductionNovel("project-a", file);

    expect(apiCall).toHaveBeenCalledTimes(1);
    const [path, options] = vi.mocked(apiCall).mock.calls[0];
    expect(path).toBe("projects/project-a/ingest/upload");
    expect(options).toMatchObject({ method: "POST", timeout: false });
    expect(options?.body).toBeInstanceOf(FormData);
    const uploaded = (options?.body as FormData).get("file");
    expect(uploaded).toBeInstanceOf(File);
    expect((uploaded as File).name).toBe(file.name);
    expect(await (uploaded as File).text()).toBe("chapter one");
  });

  it("starts a best-result run with the complete backend contract", async () => {
    const payload = {
      mode: "best" as const,
      uploaded_filename: "stored.txt",
      target_episodes: 12,
      episode: null,
      image_model: "image-model",
      video_backend: "video-provider",
      aspect_ratio: "9:16" as const,
      auto_generate_paid_media: true,
      confirmed_paid_media: true,
    };
    vi.mocked(apiCall).mockResolvedValueOnce({ id: "run_1" });

    await startProductionRun("project-a", payload);

    expect(apiCall).toHaveBeenCalledWith(
      "projects/project-a/production/control/runs",
      { method: "POST", json: payload },
    );
  });

  it("posts run commands to the selected durable run", async () => {
    vi.mocked(apiCall).mockResolvedValueOnce({ id: "run/1", status: "paused" });

    await commandProductionRun("project-a", "run/1", "take_over");

    expect(apiCall).toHaveBeenCalledWith(
      "projects/project-a/production/control/runs/run%2F1/command",
      { method: "POST", json: { command: "take_over", confirmed_paid_media: false } },
    );
  });
});
