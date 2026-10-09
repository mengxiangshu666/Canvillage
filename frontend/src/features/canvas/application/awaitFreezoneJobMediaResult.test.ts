import { beforeEach, describe, expect, it, vi } from "vitest";
import type { FreezoneVideoGenerationRequest } from '@/api/freezoneGenerationHistory';
import requestFixture from '../../../../../tests/fixtures/video_generation_request.json';

import { fetchFreezoneJobResult } from "@/api/ops";
import { awaitTaskCompletion } from "@/api/tasks";
import {
  awaitFreezoneJobMediaResult,
  mediaResultProbeDelay,
  resolveFreezoneMediaUrl,
  resolveFreezoneTaskPreviewUrl,
} from "./awaitFreezoneJobMediaResult";

vi.mock("@/api/ops", () => ({
  fetchFreezoneJobResult: vi.fn(),
}));

vi.mock("@/api/tasks", () => ({
  awaitTaskCompletion: vi.fn(),
}));

const ref = {
  task_type: "freezone_video_gen" as const,
  task_key: "task-1",
  job_id: "job-1",
};

describe("awaitFreezoneJobMediaResult", () => {
  beforeEach(() => {
    vi.mocked(awaitTaskCompletion).mockReset();
    vi.mocked(fetchFreezoneJobResult).mockReset();
  });

  it("backs off result URL probes without changing the initial delay", () => {
    expect(mediaResultProbeDelay(0, 900, 6000)).toBe(900);
    expect(mediaResultProbeDelay(1, 900, 6000)).toBe(1350);
    expect(mediaResultProbeDelay(8, 900, 6000)).toBe(6000);
    expect(mediaResultProbeDelay(0, 900, 100)).toBe(900);
  });

  it("resolves immediately when the dedicated job result is readable before task completion event", async () => {
    vi.mocked(awaitTaskCompletion).mockReturnValue(new Promise(() => {}));
    vi.mocked(fetchFreezoneJobResult).mockResolvedValue({
      url: "/static/ready.mp4",
      size: 1024,
    });

    await expect(
      awaitFreezoneJobMediaResult("demo", ref, { maxProbeMs: 1000 }),
    ).resolves.toEqual({
      url: "/static/ready.mp4",
      task: null,
      source: "result",
      result: { url: "/static/ready.mp4", size: 1024 },
    });
  });

  it("uses the inline task result when it already carries a media url", async () => {
    vi.mocked(awaitTaskCompletion).mockResolvedValue({
      task_type: "freezone_video_gen",
      task_key: "task-1",
      username: "local",
      project: "demo",
      episode: 0,
      status: "completed",
      result: { video_url: "/static/from-task.mp4" },
    });
    vi.mocked(fetchFreezoneJobResult).mockReturnValue(new Promise(() => {}));

    await expect(
      awaitFreezoneJobMediaResult("demo", ref, { maxProbeMs: 1000 }),
    ).resolves.toMatchObject({
      url: "/static/from-task.mp4",
      source: "task",
    });
  });

  it.each(['task', 'result'] as const)('preserves the actual receipt when %s wins the race', async winner => {
    const receipt = { schema: 'video_generation_source.v1' as const, task_type: 'freezone_video_gen' as const,
      job_id: ref.job_id, output_url: '/source.mp4', execution_prompt_sha256: 'a'.repeat(64),
      generation_request: requestFixture.request as FreezoneVideoGenerationRequest, provider_model: 'original-provider-model' };
    vi.mocked(awaitTaskCompletion).mockReturnValue(winner === 'task' ? Promise.resolve({
      status: 'completed', result: { output_url: '/source.mp4', video_generation_source: receipt },
    } as never) : new Promise(() => {}));
    vi.mocked(fetchFreezoneJobResult).mockReturnValue(winner === 'result' ? Promise.resolve({
      url: '/source.mp4', size: 10, video_generation_source: receipt,
    }) : new Promise(() => {}));
    const result = await awaitFreezoneJobMediaResult('demo', ref);
    expect(result.source).toBe(winner);
    expect(result.result?.video_generation_source).toEqual(receipt);
  });

  it("stops the result-url probe when the task result closes first", async () => {
    vi.mocked(awaitTaskCompletion).mockResolvedValue({
      task_type: "freezone_video_gen",
      task_key: "task-1",
      username: "local",
      project: "demo",
      episode: 0,
      status: "completed",
      result: { video_url: "/static/from-task.mp4" },
    });
    vi.mocked(fetchFreezoneJobResult).mockResolvedValue({ url: "", size: 0 });

    await expect(
      awaitFreezoneJobMediaResult("demo", ref, {
        probeDelayMs: 100,
        maxProbeMs: 1000,
      }),
    ).resolves.toMatchObject({ url: "/static/from-task.mp4", source: "task" });

    await new Promise((resolve) => setTimeout(resolve, 140));
    expect(fetchFreezoneJobResult).toHaveBeenCalledTimes(1);
  });

  it("forwards the abort signal to task monitoring and result probing", async () => {
    const controller = new AbortController();
    const reason = new Error("cancelled");
    vi.mocked(awaitTaskCompletion).mockRejectedValue(reason);
    vi.mocked(fetchFreezoneJobResult).mockRejectedValue(reason);

    await expect(
      awaitFreezoneJobMediaResult("demo", ref, {
        maxProbeMs: 1000,
        signal: controller.signal,
      }),
    ).rejects.toBe(reason);

    expect(awaitTaskCompletion).toHaveBeenCalledWith("task-1", "demo", {
      signal: expect.any(AbortSignal),
    });
    expect(fetchFreezoneJobResult).toHaveBeenCalledWith(
      "demo",
      "freezone_video_gen",
      "job-1",
      { signal: expect.any(AbortSignal) },
    );
  });
  it("cancels task monitoring when the result URL wins", async () => {
    let monitoringSignal: AbortSignal | undefined;
    vi.mocked(awaitTaskCompletion).mockImplementation((_key, _project, options) => {
      monitoringSignal = options?.signal;
      return new Promise((_resolve, reject) => {
        monitoringSignal?.addEventListener("abort", () => reject(monitoringSignal?.reason), { once: true });
      });
    });
    vi.mocked(fetchFreezoneJobResult).mockResolvedValue({ url: "/ready.mp4", size: 1 });
    await expect(awaitFreezoneJobMediaResult("demo", ref)).resolves.toMatchObject({ source: "result" });
    expect(monitoringSignal?.aborted).toBe(true);
  });

  it("preserves the caller cancellation reason in both readers", async () => {
    const controller = new AbortController();
    const reason = new Error("caller cancelled");
    vi.mocked(awaitTaskCompletion).mockImplementation((_key, _project, options) =>
      new Promise((_resolve, reject) => options?.signal?.addEventListener("abort", () => reject(options.signal?.reason), { once: true })),
    );
    vi.mocked(fetchFreezoneJobResult).mockReturnValue(new Promise(() => {}));
    const pending = awaitFreezoneJobMediaResult("demo", ref, { signal: controller.signal });
    controller.abort(reason);
    await expect(pending).rejects.toBe(reason);
    expect(vi.mocked(fetchFreezoneJobResult).mock.calls[0][3]?.signal?.reason).toBe(reason);
  });
});

describe("resolveFreezoneMediaUrl", () => {
  it("normalizes common image and video result keys", () => {
    expect(resolveFreezoneMediaUrl({ image_url: "/image.png" })).toBe("/image.png");
    expect(resolveFreezoneMediaUrl({ video_url: "/video.mp4" })).toBe("/video.mp4");
    expect(resolveFreezoneMediaUrl({ output_url: "/output.bin" })).toBe("/output.bin");
  });
});

describe("resolveFreezoneTaskPreviewUrl", () => {
  it("reads the upstream preview while the local download is still running", () => {
    expect(
      resolveFreezoneTaskPreviewUrl({
        metadata: { provider_preview_url: "https://cdn.example/video.mp4" },
      }),
    ).toBe("https://cdn.example/video.mp4");
  });

  it("ignores empty or malformed preview metadata", () => {
    expect(resolveFreezoneTaskPreviewUrl({ metadata: { provider_preview_url: " " } })).toBeNull();
    expect(resolveFreezoneTaskPreviewUrl(null)).toBeNull();
  });
});
