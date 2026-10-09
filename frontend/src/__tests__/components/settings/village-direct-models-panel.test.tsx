// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { toast } from "sonner";

import {
  DirectVideoModelsPanel,
  inferVillageDirectModelProtocol,
  LocalDirectModelsPanel,
  normalizeVillageDirectModelProtocol,
  villageDirectModelRuntimeReady,
  videoWireContractBadge,
} from "@/components/settings/settings-dialog";
import type {
  DirectModelConfig,
  DirectVideoModelConfig,
  DirectVideoModelProbeResult,
} from "@/lib/queries/model-gateway";

const queryMocks = vi.hoisted(() => ({
  discoverDirectModels: vi.fn(),
  discoverDirectVideoModels: vi.fn(),
  probeDirectModel: vi.fn(),
  probeDirectVideoModel: vi.fn(),
  saveDirectModels: vi.fn(),
  saveDirectVideoModels: vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: {
    error: vi.fn(),
    success: vi.fn(),
    warning: vi.fn(),
  },
}));

vi.mock("@/lib/queries/model-gateway", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/queries/model-gateway")>(
      "@/lib/queries/model-gateway",
    );
  return {
    ...actual,
    discoverDirectModels: queryMocks.discoverDirectModels,
    discoverDirectVideoModels: queryMocks.discoverDirectVideoModels,
    useSaveDirectModels: () => ({
      isPending: false,
      mutateAsync: queryMocks.saveDirectModels,
    }),
    useProbeDirectModel: () => ({
      isPending: false,
      mutateAsync: queryMocks.probeDirectModel,
    }),
    useSaveDirectVideoModels: () => ({
      isPending: false,
      mutateAsync: queryMocks.saveDirectVideoModels,
    }),
    useProbeDirectVideoModel: () => ({
      isPending: false,
      mutateAsync: queryMocks.probeDirectVideoModel,
    }),
  };
});

const emptySavedModels = (): DirectModelConfig[] => [];

describe("Village direct non-video model panel", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.clearAllMocks();
    queryMocks.probeDirectModel.mockReset();
    queryMocks.probeDirectVideoModel.mockReset();
    queryMocks.discoverDirectModels.mockReset();
    queryMocks.discoverDirectVideoModels.mockReset();
    queryMocks.saveDirectModels.mockReset().mockResolvedValue({ ok: true, data: [] });
    queryMocks.saveDirectVideoModels.mockReset().mockResolvedValue({ ok: true, data: [] });
  });

  it("infers common direct model protocols without calling upstream APIs", () => {
    expect(
      inferVillageDirectModelProtocol(
        "text",
        "https://opencode.ai/zen/go/v1",
        "deepseek-v4-flash",
        "auto",
      ),
    ).toBe("openai-compatible");
    expect(
      inferVillageDirectModelProtocol("agent", "https://api.anthropic.com/v1", "claude-4-sonnet", "auto"),
    ).toBe("anthropic-messages");
    expect(
      inferVillageDirectModelProtocol(
        "vision",
        "https://generativelanguage.googleapis.com/v1beta",
        "gemini-2.5-pro",
        "auto",
      ),
    ).toBe("gemini");
    expect(
      inferVillageDirectModelProtocol(
        "vision",
        "https://aiwble.com",
        "gemini-3.6-flash",
        "auto",
      ),
    ).toBe("openai-compatible");
    expect(inferVillageDirectModelProtocol(
      "vision",
      "https://aiwble.com/v1",
      "gemini-3.1-pro-preview",
      "gemini",
    )).toBe("openai-compatible");
    expect(inferVillageDirectModelProtocol("text", "http://127.0.0.1:11434/v1", "qwen3:8b", "auto")).toBe(
      "ollama-openai",
    );
    expect(normalizeVillageDirectModelProtocol("openai-chat")).toBe("openai-compatible");
    expect(villageDirectModelRuntimeReady("text", "anthropic-messages")).toBe(true);
    expect(villageDirectModelRuntimeReady("vision", "gemini")).toBe(true);
    expect(villageDirectModelRuntimeReady("agent", "anthropic-messages")).toBe(false);
    expect(villageDirectModelRuntimeReady("agent", "gemini")).toBe(false);
  });

  it("keeps a newly added row when the parent re-renders with an empty saved list", async () => {
    const { rerender } = render(
      <LocalDirectModelsPanel kind="chat" savedModels={emptySavedModels()} />,
    );

    fireEvent.click(screen.getByRole("button", { name: /添加模型/ }));
    expect(screen.getByLabelText("直连对话模型名称")).toBeInTheDocument();

    rerender(
      <LocalDirectModelsPanel
        kind="chat"
        savedModels={emptySavedModels()}
        onPersist={() => undefined}
      />,
    );
    await act(async () => undefined);

    expect(screen.getByLabelText("直连对话模型名称")).toBeInTheDocument();
  });

  it("requires a new key after a saved model changes to another endpoint", async () => {
    const savedModels: DirectModelConfig[] = [
      {
        id: "text-saved-model",
        label: "已保存对话模型",
        modelId: "model-v1",
        baseUrl: "https://first.example/v1",
        enabled: true,
        isDefault: true,
        configured: true,
        apiKeyPreview: "sk-f...cret",
        protocol: "openai-compatible",
        requestedProtocol: "auto",
        runtimeReady: true,
      },
    ];
    render(<LocalDirectModelsPanel kind="chat" savedModels={savedModels} />);

    const baseUrl = await screen.findByLabelText("直连对话模型 Base URL");
    const apiKey = screen.getByLabelText("直连对话模型 API Key");
    expect(apiKey).toHaveAttribute("placeholder", "已保存：sk-f...cret");

    fireEvent.change(baseUrl, { target: { value: "https://second.example/v1" } });

    expect(apiKey).toHaveAttribute("placeholder", "API Key");
    fireEvent.click(screen.getByRole("button", { name: "保存直连对话模型" }));
    expect(toast.error).toHaveBeenCalledWith(expect.stringContaining("API Key"));
  });

  it("shows an explicitly degraded route as waiting for a new check", async () => {
    const savedModels: DirectModelConfig[] = [
      {
        id: "agent-openai-compatible",
        label: "Agent 模型",
        modelId: "agent-model",
        baseUrl: "https://gateway.example/v1",
        enabled: true,
        isDefault: true,
        configured: true,
        apiKeyPreview: "sk-a...123",
        protocol: "openai-compatible",
        runtimeReady: false,
        verificationStatus: "directory-only",
      },
    ];

    render(<LocalDirectModelsPanel kind="chat" savedModels={savedModels} />);

    expect(await screen.findByText("待检测")).toBeInTheDocument();
    expect(screen.queryByText("协议待适配")).not.toBeInTheDocument();
  });

  it("marks declared chat capabilities with their real probe evidence", async () => {
    const savedModels: DirectModelConfig[] = [
      {
        id: "chat-verified",
        label: "实测模型",
        modelId: "chat-verified",
        baseUrl: "https://chat.example/v1",
        enabled: true,
        isDefault: true,
        configured: true,
        apiKeyPreview: "sk-c...123",
        protocol: "openai-compatible",
        runtimeReady: true,
        usable: true,
        supportsTools: true,
        supportsVision: true,
        toolCallingVerified: true,
        visionProbeStatus: "passed",
      },
      {
        id: "chat-text-only",
        label: "纯文字模型",
        modelId: "chat-text-only",
        baseUrl: "https://text.example/v1",
        enabled: true,
        isDefault: false,
        configured: true,
        apiKeyPreview: "sk-t...123",
        protocol: "openai-compatible",
        runtimeReady: true,
        usable: true,
        supportsTools: true,
        supportsVision: false,
        toolCallingVerified: true,
        visionProbeStatus: "rejected",
      },
    ];

    render(<LocalDirectModelsPanel kind="chat" savedModels={savedModels} />);

    expect(await screen.findAllByText("已实测")).toHaveLength(3);
    expect(screen.getByText("实测不支持")).toBeInTheDocument();
  });

  it("shows a server-verified row as green even when its numbers are local presets", async () => {
    // 2026-09-29 用户报告：服务端判 runtime-verified / usable=true 的行被画成黄色。
    // 颜色只能由可用性决定；能力来源只影响下面那句说明。
    const savedModels: DirectModelConfig[] = [
      {
        id: "chat-verified-local-preset",
        label: "gemini-3.8-flash",
        modelId: "gemini-3.8-flash",
        baseUrl: "https://aiwble.com/v1",
        enabled: true,
        isDefault: true,
        configured: true,
        apiKeyPreview: "sk-g...7ED",
        protocol: "openai-compatible",
        runtimeReady: true,
        usable: true,
        runtimeProbeRequired: true,
        runtimeProbeComplete: true,
        verificationStatus: "runtime-verified",
        supportsTools: true,
        supportsVision: true,
        toolCallingVerified: true,
        visionProbeStatus: "passed",
      },
    ];

    const { container } = render(
      <LocalDirectModelsPanel kind="chat" savedModels={savedModels} />,
    );

    expect(await screen.findByText("连接可用")).toBeInTheDocument();
    const status = container.querySelector(".village-model-connection");
    expect(status?.className).toContain("is-ready");
  });

  it("shows saved runtime-ready video capabilities as available and synced to the node", async () => {
    const savedModels: DirectVideoModelConfig[] = [
      {
        id: "video-runtime-ready",
        label: "全能视频模型",
        modelId: "video-omni",
        baseUrl: "https://video.example/v1",
        enabled: true,
        isDefault: true,
        configured: true,
        apiKeyPreview: "sk-v...123",
        protocol: "openai-video",
        runtimeReady: true,
        verificationStatus: "runtime-verified",
        supportedModes: ["textToVideo", "imageToVideo", "allReference", "videoEdit"],
        referenceLimits: {
          imageToVideo: { image: 1, video: 0, audio: 0 },
          allReference: { image: 9, video: 3, audio: 3 },
        },
        resolutionOptions: ["720p", "1080p"],
        aspectRatioOptions: ["16:9", "9:16"],
        minDuration: 4,
        maxDuration: 10,
        nativeAudio: "optional",
      },
    ];

    render(<DirectVideoModelsPanel models={savedModels} />);

    expect(await screen.findByText("连接可用")).toBeInTheDocument();
    const preview = screen.getByLabelText("视频节点能力预览");
    expect(within(preview).getByText("上次检测通过（时间未知）")).toBeInTheDocument();
    expect(within(preview).getByText("图生视频（首帧）")).toBeInTheDocument();
    expect(within(preview).getByText("全能参考")).toBeInTheDocument();
    expect(within(preview).getByText(/全能参考：图 9 \/ 视频 3 \/ 音频 3/)).toBeInTheDocument();
    expect(within(preview).getByText(/分辨率 720p \/ 1080p/)).toBeInTheDocument();
    expect(within(preview).getByText(/时长 4-10 秒/)).toBeInTheDocument();
    expect(within(preview).getByText(/原生音频可选/)).toBeInTheDocument();
  });

  it("keeps non-video model probe progress isolated per card", async () => {
    let resolveProbe: ((value: unknown) => void) | undefined;
    queryMocks.probeDirectModel.mockImplementation(
      () => new Promise((resolve) => {
        resolveProbe = resolve;
      }),
    );
    const savedModels: DirectModelConfig[] = [
      {
        id: "text-model-a",
        label: "对话模型 A",
        modelId: "text-a",
        baseUrl: "https://text-a.example/v1",
        enabled: true,
        isDefault: true,
        configured: true,
        apiKeyPreview: "sk-a...123",
        protocol: "openai-compatible",
      },
      {
        id: "text-model-b",
        label: "对话模型 B",
        modelId: "text-b",
        baseUrl: "https://text-b.example/v1",
        enabled: true,
        isDefault: false,
        configured: true,
        apiKeyPreview: "sk-b...456",
        protocol: "openai-compatible",
      },
    ];

    render(<LocalDirectModelsPanel kind="chat" savedModels={savedModels} />);

    const probeButtons = await screen.findAllByRole("button", { name: "检测连接" });
    fireEvent.click(probeButtons[0]);

    await waitFor(() => expect(probeButtons[0]).toBeDisabled());
    expect(probeButtons[0].querySelector(".animate-spin")).not.toBeNull();
    expect(probeButtons[1]).toBeEnabled();
    expect(probeButtons[1].querySelector(".animate-spin")).toBeNull();

    await act(async () => {
      resolveProbe?.({
        ok: true,
        data: {
          ok: true,
          modelFound: true,
          protocol: "openai-compatible",
          capabilities: {},
        },
      });
    });
    await waitFor(() => expect(probeButtons[0]).toBeEnabled());
  });

  it("reads the upstream catalog and fills the selected model ID", async () => {
    queryMocks.discoverDirectModels.mockResolvedValue({
      ok: true,
      data: {
        ok: true,
        models: [
          { id: "deepseek-v4", metadata: {} },
          { id: "gemini-3-flash", metadata: { displayName: "Gemini 3 Flash" } },
        ],
        discoveredModelCount: 2,
        protocol: "openai-compatible",
        detectedProtocol: "openai-compatible",
      },
    });
    const savedModels: DirectModelConfig[] = [
      {
        id: "text-catalog",
        label: "对话模型",
        modelId: "old-model",
        baseUrl: "https://relay.example/v1",
        enabled: true,
        isDefault: true,
        configured: true,
        apiKeyPreview: "sk-r...123",
        protocol: "openai-compatible",
      },
    ];

    render(<LocalDirectModelsPanel kind="chat" savedModels={savedModels} />);
    fireEvent.click(await screen.findByRole("button", { name: "读取上游模型" }));

    const picker = await screen.findByLabelText("选择上游模型并填入");
    fireEvent.change(picker, { target: { value: "gemini-3-flash" } });

    expect(screen.getByLabelText("直连对话模型模型 ID")).toHaveValue("gemini-3-flash");
    expect(screen.getByLabelText("直连对话模型名称")).toHaveValue("Gemini 3 Flash");
    expect(queryMocks.discoverDirectModels).toHaveBeenCalledWith({
      kind: "chat",
      id: "text-catalog",
      baseUrl: "https://relay.example/v1",
      apiKey: "",
      protocol: "openai-compatible",
    });
  });

  it("shows connection progress only on the video model being probed", async () => {
    let resolveProbe: ((value: { ok: true; data: DirectVideoModelProbeResult }) => void) | undefined;
    queryMocks.probeDirectVideoModel.mockImplementation(
      () => new Promise((resolve) => {
        resolveProbe = resolve;
      }),
    );
    const savedModels: DirectVideoModelConfig[] = [
      {
        id: "video-model-a",
        label: "视频模型 A",
        modelId: "video-a",
        baseUrl: "https://video-a.example/v1",
        enabled: true,
        isDefault: true,
        configured: true,
        apiKeyPreview: "sk-a...123",
        protocol: "openai-video",
      },
      {
        id: "video-model-b",
        label: "视频模型 B",
        modelId: "video-b",
        baseUrl: "https://video-b.example/v1",
        enabled: true,
        isDefault: false,
        configured: true,
        apiKeyPreview: "sk-b...456",
        protocol: "openai-video",
      },
    ];

    render(<DirectVideoModelsPanel models={savedModels} />);

    const probeButtons = await screen.findAllByRole("button", { name: /检测连接/ });
    fireEvent.click(probeButtons[0]);

    await waitFor(() => expect(probeButtons[0]).toBeDisabled());
    expect(probeButtons[0].querySelector(".animate-spin")).not.toBeNull();
    expect(probeButtons[1]).toBeEnabled();
    expect(probeButtons[1].querySelector(".animate-spin")).toBeNull();

    await act(async () => {
      resolveProbe?.({
        ok: true,
        data: {
          ok: true,
          modelFound: true,
          discoveredModelCount: 2,
          protocol: "openai-video",
        },
      });
    });
    await waitFor(() => expect(probeButtons[0]).toBeEnabled());
    expect(probeButtons[0].querySelector(".animate-spin")).toBeNull();
  });

  it("previews freshly detected first-last-frame parameters without generating video", async () => {
    queryMocks.probeDirectVideoModel.mockResolvedValue({
      ok: true,
      data: {
        ok: true,
        modelFound: true,
        discoveredModelCount: 1,
        protocol: "openai-video",
        capability: {
          verificationStatus: "metadata",
          modes: ["textToVideo", "imageToVideo", "firstLastFrame"],
          resolutionOptions: ["720p", "1080p"],
          aspectRatios: ["16:9", "9:16"],
          durationRange: [5, 12],
          nativeAudio: "unsupported",
          referenceLimits: { inputImages: 2 },
          referenceLimitsKnown: ["inputImages"],
        },
      },
    });
    const savedModels: DirectVideoModelConfig[] = [
      {
        id: "video-first-last",
        label: "首尾帧模型",
        modelId: "video-flf",
        baseUrl: "https://video.example/v1",
        enabled: true,
        isDefault: true,
        configured: true,
        apiKeyPreview: "sk-v...123",
        protocol: "openai-video",
        runtimeReady: false,
        verificationStatus: "unverified",
      },
    ];

    render(<DirectVideoModelsPanel models={savedModels} />);
    fireEvent.click(await screen.findByRole("button", { name: "检测连接" }));

    const preview = await screen.findByLabelText("视频节点能力预览");
    await waitFor(() => expect(within(preview).getByText("本次检测")).toBeInTheDocument());
    expect(within(preview).getByText("图生视频（首帧）")).toBeInTheDocument();
    expect(within(preview).getByText("首尾帧")).toBeInTheDocument();
    expect(within(preview).getByText(/输入图 2/)).toBeInTheDocument();
    expect(within(preview).getByText(/比例 16:9 \/ 9:16/)).toBeInTheDocument();
    expect(within(preview).getByText(/时长 5-12 秒/)).toBeInTheDocument();
    expect(within(preview).getByText(/无原生音频/)).toBeInTheDocument();
  });

  it("does not revive saved presets after a probe explicitly returns empty enums", async () => {
    queryMocks.probeDirectVideoModel.mockResolvedValue({
      ok: true,
      data: {
        ok: true,
        modelFound: true,
        discoveredModelCount: 1,
        protocol: "openai-video",
        capability: {
          verificationStatus: "metadata",
          modes: [],
          resolutionOptions: [],
          sizeSlots: [],
          aspectRatios: [],
          nativeAudio: "unsupported",
        },
      },
    });
    const savedModels: DirectVideoModelConfig[] = [{
      id: "video-stale-contract",
      label: "旧能力模型",
      modelId: "video-stale",
      baseUrl: "https://video.example/v1",
      enabled: true,
      isDefault: true,
      configured: true,
      apiKeyPreview: "sk-v...123",
      protocol: "openai-video",
      runtimeReady: true,
      verificationStatus: "runtime-verified",
      supportedModes: ["textToVideo", "allReference"],
      resolutionOptions: ["720p", "1080p"],
      aspectRatioOptions: ["16:9", "9:16"],
    }];

    render(<DirectVideoModelsPanel models={savedModels} />);
    fireEvent.click(await screen.findByRole("button", { name: "检测连接" }));

    const preview = await screen.findByLabelText("视频节点能力预览");
    await waitFor(() => expect(within(preview).getByText("本次检测")).toBeInTheDocument());
    expect(within(preview).queryByText("文生视频")).not.toBeInTheDocument();
    expect(within(preview).queryByText("全能参考")).not.toBeInTheDocument();
    expect(within(preview).queryByText(/分辨率 720p/)).not.toBeInTheDocument();
    expect(within(preview).queryByText(/比例 16:9/)).not.toBeInTheDocument();
    expect(within(preview).getByText(/无原生音频/)).toBeInTheDocument();
  });

  it("does not report a directory-only video ID as node-ready", async () => {
    queryMocks.probeDirectVideoModel.mockResolvedValue({
      ok: true,
      data: {
        ok: true,
        modelFound: true,
        discoveredModelCount: 1,
        protocol: "openai-video",
        capability: {
          verificationStatus: "directory-only",
        },
      },
    });
    const savedModels: DirectVideoModelConfig[] = [{
      id: "video-directory-only",
      label: "目录模型",
      modelId: "sd-2.0-fast-v1",
      baseUrl: "https://video.example/v1",
      enabled: true,
      isDefault: true,
      configured: true,
      apiKeyPreview: "sk-v...123",
      protocol: "openai-video",
      runtimeReady: false,
      verificationStatus: "directory-only",
    }];

    render(<DirectVideoModelsPanel models={savedModels} />);
    fireEvent.click(await screen.findByRole("button", { name: "检测连接" }));

    await waitFor(() => {
      expect(screen.getByText("待检测")).toBeInTheDocument();
    });
    expect(
      screen.getByText(
        "模型 ID 已匹配，但上游目录没有返回视频能力字段；当前不会映射到视频节点。",
      ),
    ).toBeInTheDocument();
    expect(toast.warning).toHaveBeenCalledWith(
      "模型目录已匹配，但缺少视频能力证据：sd-2.0-fast-v1",
    );
  });

  it("reads and fills a video model without submitting generation", async () => {
    queryMocks.discoverDirectVideoModels.mockResolvedValue({
      ok: true,
      data: {
        ok: true,
        models: [{ id: "video-pro", metadata: { displayName: "Video Pro" } }],
        discoveredModelCount: 1,
        protocol: "openai-video",
      },
    });
    const savedModels: DirectVideoModelConfig[] = [
      {
        id: "video-catalog",
        label: "视频模型",
        modelId: "old-video",
        baseUrl: "https://video.example/v1",
        enabled: true,
        isDefault: true,
        configured: true,
        apiKeyPreview: "sk-v...123",
        protocol: "openai-video",
      },
    ];

    render(<DirectVideoModelsPanel models={savedModels} />);
    fireEvent.click(await screen.findByRole("button", { name: "读取上游模型" }));

    const picker = await screen.findByLabelText("选择上游视频模型并填入");
    fireEvent.change(picker, { target: { value: "video-pro" } });

    expect(screen.getByLabelText("直连视频模型 ID")).toHaveValue("video-pro");
    expect(screen.getByLabelText("直连视频模型名称")).toHaveValue("Video Pro");
    expect(queryMocks.discoverDirectVideoModels).toHaveBeenCalledWith({
      id: "video-catalog",
      baseUrl: "https://video.example/v1",
      apiKey: "",
      protocol: "openai-video",
    });
  });
});

/**
 * 保存接口是**整体替换**语义（`{models}` 就是新全集），空数组 = 把整族配置删光。
 * 2026-09-16 用户的 3 条生图模型、2026-08-31 的 4 条直连视频模型都是这样没的。
 * 空保存本身合法（比如整体切到统一网关），所以这里要的是**确认**而不是硬拦。
 */
describe("模型配置清空保存要确认", () => {
  const confirmSpy = vi.fn();

  beforeEach(() => {
    window.localStorage.clear();
    vi.clearAllMocks();
    confirmSpy.mockReset();
    vi.stubGlobal("confirm", confirmSpy);
    queryMocks.saveDirectModels.mockReset().mockResolvedValue({ ok: true, data: [] });
    queryMocks.saveDirectVideoModels.mockReset().mockResolvedValue({ ok: true, data: [] });
  });

  const savedImageLike: DirectModelConfig[] = [
    {
      id: "image-keep",
      label: "要保留的生图模型",
      modelId: "gpt-image-2.5-flare",
      baseUrl: "https://img.example/v1",
      enabled: true,
      isDefault: true,
      configured: true,
      apiKeyPreview: "sk-k...eep",
      protocol: "openai-images",
      runtimeReady: true,
      usable: true,
    },
  ];

  it("删除最后一条后保存＝清空整族，必须确认；取消就不落库", async () => {
    confirmSpy.mockReturnValue(false);
    render(<LocalDirectModelsPanel kind="image" savedModels={savedImageLike} />);
    // 删掉唯一一条 —— 编辑区随之变空，此时保存等于清空整族。
    fireEvent.click(await screen.findByRole("button", { name: /删除/ }));
    fireEvent.click(await screen.findByRole("button", { name: "保存直连生图模型" }));

    expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining("清空"));
    expect(queryMocks.saveDirectModels).not.toHaveBeenCalled();
  });

  it("确认清空后照旧落库（不挡合法操作）", async () => {
    confirmSpy.mockReturnValue(true);
    render(<LocalDirectModelsPanel kind="image" savedModels={savedImageLike} />);
    fireEvent.click(await screen.findByRole("button", { name: /删除/ }));
    fireEvent.click(await screen.findByRole("button", { name: "保存直连生图模型" }));

    await waitFor(() =>
      expect(queryMocks.saveDirectModels).toHaveBeenCalledWith({
        kind: "image",
        models: [],
        confirmClear: true,
      }),
    );
  });

  it("本来就没配过模型时，空保存不问也不报错（迁移路径不能被拦）", async () => {
    render(<LocalDirectModelsPanel kind="image" savedModels={emptySavedModels()} />);
    fireEvent.click(await screen.findByRole("button", { name: "保存直连生图模型" }));

    expect(confirmSpy).not.toHaveBeenCalled();
    await waitFor(() => expect(queryMocks.saveDirectModels).toHaveBeenCalled());
  });

  it("直连视频模型清空同样要确认，取消就不落库", async () => {
    confirmSpy.mockReturnValue(false);
    const savedVideo: DirectVideoModelConfig[] = [
      {
        id: "video-keep",
        label: "要保留的视频模型",
        modelId: "minimax_h3_z0903",
        baseUrl: "https://video.example/v1",
        enabled: true,
        isDefault: true,
        configured: true,
        apiKeyPreview: "sk-v...eep",
        protocol: "openai-video",
      },
    ];
    render(<DirectVideoModelsPanel models={savedVideo} />);
    fireEvent.click(await screen.findByRole("button", { name: /删除/ }));
    fireEvent.click(await screen.findByRole("button", { name: "保存直连视频模型" }));

    expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining("清空"));
    expect(queryMocks.saveDirectVideoModels).not.toHaveBeenCalled();
  });

  it("确认清空视频模型后会把确认位一起交给服务端", async () => {
    confirmSpy.mockReturnValue(true);
    const savedVideo: DirectVideoModelConfig[] = [
      {
        id: "video-keep",
        label: "要保留的视频模型",
        modelId: "minimax_h3_z0903",
        baseUrl: "https://video.example/v1",
        enabled: true,
        isDefault: true,
        configured: true,
        apiKeyPreview: "sk-v...eep",
        protocol: "openai-video",
      },
    ];
    render(<DirectVideoModelsPanel models={savedVideo} />);
    fireEvent.click(await screen.findByRole("button", { name: /删除/ }));
    fireEvent.click(await screen.findByRole("button", { name: "保存直连视频模型" }));

    await waitFor(() =>
      expect(queryMocks.saveDirectVideoModels).toHaveBeenCalledWith({
        models: [],
        confirmClear: true,
      }),
    );
  });
});

describe("视频模型的出线合同来源徽标", () => {
  const baseVideo = (patch: Partial<DirectVideoModelConfig>): DirectVideoModelConfig => ({
    id: "video-contract",
    label: "合同来源模型",
    modelId: "seedance-2.5",
    baseUrl: "https://video.example/v1",
    enabled: true,
    isDefault: true,
    configured: true,
    apiKeyPreview: "sk-v...123",
    protocol: "openai-video",
    ...patch,
  });

  it("渠道自带且真实验证过就显示绿色已验证券", () => {
    const badge = videoWireContractBadge(
      baseVideo({
        wireContractSource: "channel",
        wireContractSourceLabel: "渠道自带合同（随渠道增删） · 真实提交验证",
        wireContractProfileId: "relay-openai-ish",
        wireContractEvidence: "submit",
        wireContractVerified: true,
        wireContractConflictsWithSeed: ["ratio", "image"],
      }),
    );

    expect(badge).toMatchObject({
      tone: "verified",
      label: "渠道合同 · 已验证",
      conflicts: ["ratio", "image"],
      error: "",
    });
    expect(badge?.detail).toContain("真实提交验证");
  });

  it("渠道合同只有目录探测证据时不能冒充已验证", () => {
    const badge = videoWireContractBadge(
      baseVideo({
        wireContractSource: "channel",
        wireContractProfileId: "relay-openai-ish",
        wireContractEvidence: "probe",
        wireContractVerified: false,
      }),
    );

    expect(badge).toMatchObject({ tone: "unverified", label: "渠道合同 · 未验证" });
    expect(badge?.detail).toContain("只探测过模型目录");
  });

  it("源码种子与通用兜底各自标明来源，不当成渠道合同", () => {
    expect(
      videoWireContractBadge(
        baseVideo({ wireContractSource: "seed", wireContractSeedProfileId: "seedance-2.0" }),
      ),
    ).toMatchObject({ tone: "seed", label: "源码种子 · 未验证", detail: "seedance-2.0" });
    expect(
      videoWireContractBadge(baseVideo({ wireContractSource: "default" })),
    ).toMatchObject({ tone: "fallback", label: "通用兜底" });
  });

  it("合同读不动时把原因摆出来，不静默当没有", () => {
    const badge = videoWireContractBadge(
      baseVideo({ wireContractError: "wireContract.source 必须是 channel" }),
    );

    expect(badge).toMatchObject({
      tone: "unverified",
      label: "合同读不动",
      error: "wireContract.source 必须是 channel",
    });
  });

  it("后端没给合同来源时不画徽标", () => {
    expect(videoWireContractBadge(baseVideo({}))).toBeNull();
    expect(videoWireContractBadge(undefined)).toBeNull();
  });

  it("卡片头把徽标和与种子不一致的字段一起画出来", () => {
    render(
      <DirectVideoModelsPanel
        models={[
          baseVideo({
            wireContractSource: "channel",
            wireContractProfileId: "relay-openai-ish",
            wireContractEvidence: "submit",
            wireContractVerified: true,
            wireContractConflictsWithSeed: ["ratio", "image"],
          }),
        ]}
      />,
    );

    expect(screen.getByText("合同：渠道合同 · 已验证")).toBeInTheDocument();
    expect(screen.getByText(/与同名源码种子不一致的字段：ratio、image/)).toBeInTheDocument();
  });
});
