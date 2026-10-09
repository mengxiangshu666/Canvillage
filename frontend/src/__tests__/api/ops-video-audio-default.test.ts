// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

import { apiCall } from "@/api/client";
import {
  submitFreezoneVideoEdit,
  submitFreezoneVideoGen,
  submitFreezoneVideoI2v,
  submitFreezoneVideoKeyframes,
  submitFreezoneVideoOmniGen,
} from "@/api/ops";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({
  apiCall: vi.fn(),
}));

describe("freezone video audio defaults", () => {
  beforeEach(() => {
    vi.mocked(apiCall).mockReset();
    vi.mocked(apiCall).mockResolvedValue({ job_id: "job-1" });
  });

  it("defaults every video submission mode to native audio", async () => {
    await submitFreezoneVideoGen("project", { prompt: "text" });
    await submitFreezoneVideoKeyframes("project", { firstFrameUrl: "https://fixture/first.png" });
    await submitFreezoneVideoI2v("project", { imageUrls: ["https://fixture/image.png"] });
    await submitFreezoneVideoEdit("project", { videoUrl: "https://fixture/video.mp4" });
    await submitFreezoneVideoOmniGen("project", { prompt: "omni" });

    expect(vi.mocked(apiCall).mock.calls).toHaveLength(5);
    for (const [, options] of vi.mocked(apiCall).mock.calls) {
      expect(options?.json).toMatchObject({
        generate_audio: true,
        native_audio_strategy: "native",
      });
      expect(options?.json).not.toHaveProperty("generate_audio_explicit");
      expect(options?.json).not.toHaveProperty("human_review");
      expect(options?.json).not.toHaveProperty("parameters");
    }
  });

  it("forwards explicit human review for every video mode", async () => {
    await submitFreezoneVideoGen("project", { prompt: "text", humanReview: true });
    await submitFreezoneVideoKeyframes("project", {
      firstFrameUrl: "https://fixture/first.png",
      humanReview: true,
    });
    await submitFreezoneVideoI2v("project", {
      imageUrls: ["https://fixture/image.png"],
      humanReview: true,
    });
    await submitFreezoneVideoEdit("project", {
      videoUrl: "https://fixture/video.mp4",
      humanReview: true,
    });
    await submitFreezoneVideoOmniGen("project", { prompt: "omni", humanReview: true });

    for (const [, options] of vi.mocked(apiCall).mock.calls) {
      expect(options?.json).toHaveProperty("human_review", true);
    }
  });

  it("forwards parameters only when a caller explicitly provides them", async () => {
    const parameters = { seed: 42 };
    await submitFreezoneVideoGen("project", { prompt: "text", parameters });
    expect(vi.mocked(apiCall).mock.calls[0]?.[1]?.json).toHaveProperty(
      "parameters",
      parameters,
    );
  });

  it("preserves an explicit user choice to disable audio", async () => {
    await submitFreezoneVideoGen("project", {
      prompt: "silent",
      generateAudio: false,
      generateAudioExplicit: true,
    });

    expect(vi.mocked(apiCall).mock.calls[0]?.[1]?.json).toMatchObject({
      generate_audio: false,
      generate_audio_explicit: true,
    });
  });

  it("preserves shared fields and mode-specific fields across all five endpoints", async () => {
    const common = {
      prompt: "a moving subject",
      cameraTemplateId: "dolly_in",
      marks: [{ label: "subject", pointX: 0, pointY: 0.5 }],
      aspectRatio: "9:16",
      resolution: "720p" as const,
      durationSeconds: 6,
      generateAudio: false,
      generateAudioExplicit: true,
      dialogueText: "hello",
      spokenDialogue: ["hello"],
      audioType: "dialogue" as const,
      speaker: "actor",
      nativeAudioStrategy: "native" as const,
      audioAssetRef: "voice.wav",
      model: "direct-model",
      modelId: "catalog-model",
      genMode: "custom-mode",
      humanReview: false,
      parameters: { seed: 0 },
      canvasId: "canvas",
      nodeId: "node",
    };
    await submitFreezoneVideoGen("project", { ...common, characterIds: ["actor"] });
    await submitFreezoneVideoKeyframes("project", { ...common, firstFrameUrl: "first", lastFrameUrl: "last" });
    await submitFreezoneVideoI2v("project", { ...common, imageUrls: ["image"] });
    await submitFreezoneVideoEdit("project", { ...common, videoUrl: "video", audioSetting: "origin" });
    await submitFreezoneVideoOmniGen("project", { ...common, theme: "theme", references: [{ type: "audio", url: "audio" }] });

    const bodies = vi.mocked(apiCall).mock.calls.map(([, options]) => options?.json as Record<string, unknown>);
    const shared = {
      prompt: "a moving subject", camera_template_id: "dolly_in",
      marks: [{ label: "subject", source_url: "", point_x: 0, point_y: 0.5, box_x: null, box_y: null, box_width: null, box_height: null, note: "" }],
      aspect_ratio: "9:16", resolution: "720p", duration_seconds: 6,
      generate_audio: false, generate_audio_explicit: true,
      dialogue_text: "hello", spoken_dialogue: ["hello"], audio_type: "dialogue",
      speaker: "actor", native_audio_strategy: "native", audio_asset_ref: "voice.wav",
      model: "direct-model", model_id: "catalog-model", gen_mode: "custom-mode",
      human_review: false, parameters: { seed: 0 }, canvas_id: "canvas", node_id: "node",
    };
    const specific = [
      { character_ids: ["actor"], scene_optimize: null },
      { first_frame_url: "first", last_frame_url: "last", scene_optimize: null },
      { image_urls: ["image"], scene_optimize: null },
      { video_url: "video", image_urls: [], audio_setting: "origin" },
      { theme: "theme", references: [{ type: "audio", url: "audio", role: "", label: "" }], scene_optimize: null },
    ];
    bodies.forEach((body, index) => expect(body).toEqual({ ...shared, ...specific[index] }));
  });

  it("omits an explicitly unsupported aspect-ratio parameter", async () => {
    await submitFreezoneVideoGen("project", {
      prompt: "model decides",
      aspectRatio: "",
    });

    expect(vi.mocked(apiCall).mock.calls[0]?.[1]?.json).not.toHaveProperty(
      "aspect_ratio",
    );
  });

  it("omits an empty audio type instead of sending an invalid value", async () => {
    await submitFreezoneVideoGen("project", { prompt: "text", audioType: "" });
    await submitFreezoneVideoKeyframes("project", {
      firstFrameUrl: "https://fixture/first.png",
      audioType: "",
    });
    await submitFreezoneVideoI2v("project", {
      imageUrls: ["https://fixture/image.png"],
      audioType: "",
    });
    await submitFreezoneVideoEdit("project", {
      videoUrl: "https://fixture/video.mp4",
      audioType: "",
    });
    await submitFreezoneVideoOmniGen("project", { prompt: "omni", audioType: "" });

    for (const [, options] of vi.mocked(apiCall).mock.calls) {
      expect(options?.json).not.toHaveProperty("audio_type");
    }

    vi.mocked(apiCall).mockClear();
    await submitFreezoneVideoOmniGen("project", {
      prompt: "spoken",
      audioType: "dialogue",
    });
    expect(vi.mocked(apiCall).mock.calls[0]?.[1]?.json).toMatchObject({
      audio_type: "dialogue",
    });
  });

  /**
   * T-154：画布路径不再下发运镜目录 id，运镜只经提示词那一段表达。
   *
   * 这一条锚的是**载荷默认值**：没有显式给 `cameraTemplateId` 时发出去的必须是
   * `null`。此前脚本派生的节点把 `[运镜轨迹] …` 这样的自由文本塞进这个字段，后端
   * `unknown camera_template_id: …` 直接 400。显式给了 id 的调用方（例如测试或未来的
   * 目录选择器）仍然照原样发出。
   */
  it("defaults camera_template_id to null and forwards an explicit id unchanged", async () => {
    await submitFreezoneVideoGen("project", { prompt: "text" });
    await submitFreezoneVideoKeyframes("project", { firstFrameUrl: "https://fixture/first.png" });
    await submitFreezoneVideoI2v("project", { imageUrls: ["https://fixture/image.png"] });
    await submitFreezoneVideoEdit("project", { videoUrl: "https://fixture/video.mp4" });
    await submitFreezoneVideoOmniGen("project", { prompt: "omni" });

    for (const [, options] of vi.mocked(apiCall).mock.calls) {
      expect((options?.json as Record<string, unknown>).camera_template_id).toBeNull();
    }

    vi.mocked(apiCall).mockClear();
    await submitFreezoneVideoGen("project", {
      prompt: "preset",
      cameraTemplateId: "dolly_in",
    });
    expect(
      (vi.mocked(apiCall).mock.calls[0]?.[1]?.json as Record<string, unknown>)
        .camera_template_id,
    ).toBe("dolly_in");
  });
});
