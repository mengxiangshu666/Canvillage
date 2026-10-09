// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

import {
  coerceVideoModelList,
  coerceVideoChannelStatus,
  VIDEO_CHANNEL_OFFLINE_REASON,
} from "@/api/ops";
import { describe, expect, it } from "vitest";

describe("coerceVideoChannelStatus", () => {
  it("preserves a backend-declared Seedance 2 family for opaque model ids", () => {
    expect(coerceVideoModelList([{
      id: "provider-opaque-model",
      api_model: "provider-opaque-model",
      provider_id: "seedance",
      family: "seedance-2",
    }])[0]?.family).toBe("seedance-2");
  });

  it("preserves the capability revision and source used by the picker", () => {
    expect(coerceVideoModelList([{
      id: "direct_operator-video",
      api_model: "operator-video-v1",
      provider_id: "direct",
      capability_revision: "video-cap.0123456789abcdef",
      capability_source: "upstream",
    }])[0]).toMatchObject({
      capabilityRevision: "video-cap.0123456789abcdef",
      capabilitySource: "upstream",
    });
  });

  it("passes the outbound-contract source through to the node picker", () => {
    expect(coerceVideoModelList([{
      id: "direct_channel-video",
      api_model: "channel-video-v1",
      provider_id: "direct",
      wireContractSource: "channel",
      wireContractSourceLabel: "渠道自带合同（随渠道增删） · 真实提交验证",
      wireContractProfileId: "relay-openai-ish",
      wireContractEvidence: "submit",
      wireContractVerified: true,
      wireContractConflictsWithSeed: ["ratio", "image"],
    }])[0]).toMatchObject({
      wireContractSource: "channel",
      wireContractProfileId: "relay-openai-ish",
      wireContractEvidence: "submit",
      wireContractVerified: true,
      wireContractConflictsWithSeed: ["ratio", "image"],
    });
  });

  it("keeps an unreadable contract visible instead of dropping the marker", () => {
    expect(coerceVideoModelList([{
      id: "direct_broken-video",
      api_model: "broken-video-v1",
      provider_id: "direct",
      wire_contract_source: "seed",
      wire_contract_error: "wireContract.source 必须是 channel",
    }])[0]).toMatchObject({
      wireContractSource: "seed",
      wireContractError: "wireContract.source 必须是 channel",
    });
  });

  it("preserves Firefly Seedance2 capabilities instead of coercing them to generic", () => {
    expect(
      coerceVideoModelList([
        {
          id: "newapi_firefly-seedance2-fast-480p",
          api_model: "newapi_firefly-seedance2-fast-480p",
          provider_id: "seedance",
          family: "firefly-seedance2",
          resolution_options: ["480p"],
          min_duration: 4,
          max_duration: 15,
          supported_modes: [
            "textToVideo",
            "imageToVideo",
            "allReference",
            "imageReference",
          ],
          reference_limits: {
            allReference: { image: 9, video: 3, audio: 3, total: 12 },
            imageReference: { image: 9, video: 0, audio: 0 },
          },
        },
      ])[0],
    ).toMatchObject({
      family: "firefly-seedance2",
      resolutionOptions: ["480p"],
      minDuration: 4,
      maxDuration: 15,
      supportedModes: [
        "textToVideo",
        "imageToVideo",
        "allReference",
        "imageReference",
      ],
      referenceLimits: {
        allReference: { image: 9, video: 3, audio: 3, total: 12 },
        imageReference: { image: 9, video: 0, audio: 0 },
      },
    });
  });

  it("preserves server-selected balanced defaults for a direct model", () => {
    expect(coerceVideoModelList([{
      id: "direct_local-kling",
      api_model: "kling-v3-omni-v2v-create",
      provider_id: "direct",
      family: "kacang-kling-v2v",
      parameter_defaults: {
        resolution: "720p",
        duration_seconds: 5,
        aspect_ratio: "16:9",
        generate_audio: false,
        strategy: "balanced",
      },
    }])[0]).toMatchObject({
      family: "kacang-kling-v2v",
      parameterDefaults: {
        resolution: "720p",
        durationSeconds: 5,
        aspectRatio: "16:9",
        generateAudio: false,
        strategy: "balanced",
      },
    });
  });

  it("maps the complete direct-video capability contract into node options", () => {
    expect(coerceVideoModelList([{
      id: "direct_metadata-video",
      api_model: "metadata-video",
      provider_id: "direct",
      supported_modes: ["imageToVideo", "videoEdit"],
      size_options: ["992x432", "640x640", "bad-size"],
      aspect_ratio_options: ["16:9", "1:1", "bad-ratio"],
      resolution_options: ["720p", "invalid"],
      min_duration: 4,
      max_duration: 12,
      native_audio: "unsupported",
      is_default: true,
    }])[0]).toMatchObject({
      supportedModes: ["imageToVideo", "videoEdit"],
      sizeOptions: ["992x432", "640x640"],
      aspectRatioOptions: ["16:9", "1:1"],
      resolutionOptions: ["720p"],
      minDuration: 4,
      maxDuration: 12,
      nativeAudio: "unsupported",
      isDefault: true,
    });
  });

  it("keeps catalog resolution claims separate from runtime-effective options", () => {
    expect(coerceVideoModelList([{
      id: "direct_h3",
      api_model: "minimax_h3",
      provider_id: "direct",
      resolution_options: ["768p"],
      advertised_resolution_options: ["768p", "2k"],
      runtime_resolution_options: ["768p"],
      runtime_rejected_resolution_options: ["2k"],
      runtime_capability_status: "degraded",
      runtime_capability_note: "当前网关拒绝 2K",
    }])[0]).toMatchObject({
      resolutionOptions: ["768p"],
      advertisedResolutionOptions: ["768p", "2k"],
      runtimeResolutionOptions: ["768p"],
      runtimeRejectedResolutionOptions: ["2k"],
      runtimeCapabilityStatus: "degraded",
      runtimeCapabilityNote: "当前网关拒绝 2K",
    });
  });

  it("preserves valid upstream-specific video ratios and resolutions", () => {
    expect(coerceVideoModelList([{
      id: "direct_custom-video",
      api_model: "custom-video",
      provider_id: "direct",
      aspect_ratio_options: ["2.39:1", "16:9", "bad-ratio"],
      resolution_options: ["1440p", "720p", "invalid"],
      advertised_resolution_options: ["1440p", "720p"],
      runtime_resolution_options: ["1440p"],
    }])[0]).toMatchObject({
      aspectRatioOptions: ["2.39:1", "16:9"],
      resolutionOptions: ["1440p", "720p"],
      advertisedResolutionOptions: ["1440p", "720p"],
      runtimeResolutionOptions: ["1440p"],
    });
  });

  it("preserves audio slots and semantic contracts for node execution", () => {
    expect(coerceVideoModelList([{
      id: "direct_audio-video",
      api_model: "audio-video-v1",
      provider_id: "direct",
      media_inputs: [
        { key: "ref_audio_0", providerKey: "ref_audio_0", type: "audio" },
        null,
        "bad-entry",
      ],
      audio_input_semantics: [
        "driving-audio",
        "voice_profile",
        "driving_audio",
        "unknown",
      ],
    }])[0]).toMatchObject({
      mediaInputs: [
        { key: "ref_audio_0", providerKey: "ref_audio_0", type: "audio" },
      ],
      media_inputs: [
        { key: "ref_audio_0", providerKey: "ref_audio_0", type: "audio" },
      ],
      audioInputSemantics: ["driving_audio", "voice_profile"],
      audio_input_semantics: ["driving_audio", "voice_profile"],
    });
  });

  it("preserves discrete duration, fps, input-slot, and tail-frame contracts", () => {
    expect(coerceVideoModelList([{
      id: "direct_seedance25",
      api_model: "seedance2.5",
      provider_id: "direct",
      duration_options: [30, "5", 10, 5, 0, 301],
      fps_options: [24, "30", 0, 241, 24.5],
      input_slots: ["image", "video", "image"],
      return_last_frame: false,
      supports_custom_duration: false,
      duration_parameter_enabled: true,
    }])[0]).toMatchObject({
      durationOptions: [5, 10, 30],
      fpsOptions: [24, 24.5, 30],
      inputSlots: ["image", "video", "image"],
      returnLastFrame: false,
      supportsCustomDuration: false,
      durationParameterEnabled: true,
    });
  });

  it("keeps an explicit empty duration contract empty", () => {
    expect(coerceVideoModelList([{
      id: "direct_fixed",
      api_model: "provider-fixed",
      provider_id: "direct",
      durationOptions: [],
      supportsCustomDuration: false,
    }])[0]).toMatchObject({
      durationOptions: [],
      supportsCustomDuration: false,
    });
  });

  it("keeps provider-native orientation suffixes in resolution options", () => {
    expect(coerceVideoModelList([{
      id: "direct_autodl-h3",
      api_model: "minimax_h3_lightx2v_v5",
      provider_id: "direct",
      protocol: "autodl-comfyui",
      resolution_options: ["480p竖", "768p竖", "480p横", "768p横"],
      aspect_ratio_options: ["9:16", "16:9"],
    }])[0]).toMatchObject({
      resolutionOptions: ["480p竖", "768p竖", "480p横", "768p横"],
      aspectRatioOptions: ["9:16", "16:9"],
    });
  });

  it("keeps provider-native square resolution labels", () => {
    expect(coerceVideoModelList([{
      id: "direct_autodl-square",
      api_model: "minimax_h3_b99_001",
      provider_id: "direct",
      protocol: "autodl-comfyui",
      resolution_options: ["736p竖", "736p横", "736p(1:1)"],
      aspect_ratio_options: ["9:16", "16:9", "1:1"],
    }])[0]).toMatchObject({
      resolutionOptions: ["736p竖", "736p横", "736p(1:1)"],
      aspectRatioOptions: ["9:16", "16:9", "1:1"],
    });
  });

  it("keeps non-pixel provider resolution enums unchanged", () => {
    expect(coerceVideoModelList([{
      id: "direct_custom-resolution",
      api_model: "provider_video_v1",
      provider_id: "direct",
      resolution_options: ["Full HD (cinematic)", "Ultra HD 2"],
      workflowDiscovery: { status: "discovered", source: "workflow_schema" },
    }])[0]).toMatchObject({
      resolutionOptions: ["Full HD (cinematic)", "Ultra HD 2"],
    });
  });

  it("preserves AutoDL workflow input rules and resolution mappings", () => {
    const workflowInputRules = [
      {
        key: "resolution",
        rule: {
          options: [
            {
              label: "736p竖",
              values: {
                "174.inputs.Number": 736,
                "175.inputs.Number": 1280,
              },
            },
          ],
        },
      },
      { key: "duration", nodeId: "3", field: "inputs.duration" },
      null,
      "invalid",
    ];
    const resolutionMappings = [
      {
        label: "736p竖",
        width: 736,
        height: 1280,
        values: { "174.inputs.Number": 736 },
      },
      null,
    ];

    expect(
      coerceVideoModelList([
        {
          id: "direct_autodl-workflow",
          api_model: "minimax_h3_workflow",
          provider_id: "direct",
          workflow_id: "workflow-h3",
          workflow_name: "H3 ComfyUI",
          workflow_input_rules: workflowInputRules,
          resolution_mappings: resolutionMappings,
          workflow_discovery: { status: "discovered", source: "workflow_schema" },
        },
      ])[0],
    ).toMatchObject({
      workflowId: "workflow-h3",
      workflowName: "H3 ComfyUI",
      workflowInputRules: [workflowInputRules[0], workflowInputRules[1]],
      resolutionMappings: [resolutionMappings[0]],
      workflowDiscovery: { status: "discovered", source: "workflow_schema" },
    });
  });

  it("accepts camelCase workflow schema fields and keeps empty declarations", () => {
    expect(
      coerceVideoModelList([
        {
          id: "direct_empty-workflow",
          model: "empty-workflow",
          workflowInputRules: [],
          resolutionMappings: [],
        },
      ])[0],
    ).toMatchObject({
      workflowInputRules: [],
      resolutionMappings: [],
    });
  });

  it("reads workflow schema records wrapped in an options object", () => {
    const workflowInputRules = [
      { key: "width", nodeId: "174", field: "inputs.Number" },
    ];
    const resolutionMappings = [
      { label: "736p竖", values: { "174.inputs.Number": 736 } },
    ];

    expect(
      coerceVideoModelList([
        {
          id: "direct_options-workflow",
          model: "options-workflow",
          workflowInputRules: { options: workflowInputRules },
          resolutionMappings: { options: resolutionMappings },
        },
      ])[0],
    ).toMatchObject({
      workflowInputRules,
      resolutionMappings,
    });
  });

  it("fails closed when the channel envelope is missing", () => {
    expect(coerceVideoChannelStatus({ data: [{ id: "newapi_seedance-2.0" }] })).toEqual({
      enabled: false,
      generationEnabled: false,
      disabledReason: VIDEO_CHANNEL_OFFLINE_REASON,
    });
    expect(coerceVideoChannelStatus(["newapi_seedance-2.0"])).toEqual({
      enabled: false,
      generationEnabled: false,
      disabledReason: VIDEO_CHANNEL_OFFLINE_REASON,
    });
  });

  it("honours an explicit offline channel and its reason", () => {
    expect(
      coerceVideoChannelStatus({
        channel: { enabled: false, disabled_reason: "上游维护中" },
      }),
    ).toEqual({
      enabled: false,
      generationEnabled: false,
      disabledReason: "上游维护中",
    });
  });

  it("recovers channel status from model rows after the API envelope is unwrapped", () => {
    expect(
      coerceVideoChannelStatus([
        {
          id: "newapi_seedance-2.0",
          enabled: true,
          channel_enabled: true,
        },
      ]),
    ).toEqual({ enabled: true, generationEnabled: true, disabledReason: "" });
    expect(
      coerceVideoChannelStatus([
        {
          id: "newapi_seedance-2.0",
          enabled: false,
          channelEnabled: false,
        },
      ]),
    ).toEqual({
      enabled: false,
      generationEnabled: false,
      disabledReason: VIDEO_CHANNEL_OFFLINE_REASON,
    });
  });

  it("keeps the channel live when enabled and disabled direct models coexist", () => {
    expect(
      coerceVideoChannelStatus([
        {
          id: "direct_video-live",
          enabled: true,
          channelEnabled: true,
          runtimeReady: true,
        },
        {
          id: "direct_video-disabled",
          enabled: false,
          channelEnabled: false,
          runtimeReady: false,
        },
      ]),
    ).toEqual({ enabled: true, generationEnabled: true, disabledReason: "" });
  });

  it("requires a consistent explicit enabled signal", () => {
    expect(
      coerceVideoChannelStatus({
        channel: { enabled: true, generation_enabled: true },
      }),
    ).toEqual({ enabled: true, generationEnabled: true, disabledReason: "" });
    expect(
      coerceVideoChannelStatus({
        channel: { enabled: true, generation_enabled: false },
      }),
    ).toEqual({
      enabled: false,
      generationEnabled: false,
      disabledReason: VIDEO_CHANNEL_OFFLINE_REASON,
    });
  });
});
