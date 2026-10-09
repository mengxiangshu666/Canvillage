import { describe, expect, it } from "vitest";

import {
  inferVillageDirectModelProtocol,
  modelConnectionSummary,
  villageDirectModelRuntimeReady,
} from "@/components/settings/models/direct-model-config";

describe("Gemini image protocol", () => {
  it("auto-resolves Gemini image model ids to the native image contract", () => {
    expect(
      inferVillageDirectModelProtocol(
        "image",
        "https://img.yunfei.best/v1",
        "gemini-3-pro-image",
        "auto",
      ),
    ).toBe("gemini-image");
  });

  it("marks the native Gemini image transport as executable", () => {
    expect(villageDirectModelRuntimeReady("image", "gemini-image")).toBe(true);
  });
});

describe("unlisted preview models", () => {
  it("reads a catalog-missing row as usable once the runtime probe passed", () => {
    const summary = modelConnectionSummary({
      configured: true,
      enabled: true,
      lastProbe: "unknown",
      catalogMissing: true,
      runtimeReady: true,
    });

    expect(summary.label).toBe("连接可用");
    expect(summary.detail).toContain("目录未列出");
    expect(summary.tone).toBe("ready");
  });

  it("keeps a failed probe as the dominant signal", () => {
    const summary = modelConnectionSummary({
      configured: true,
      enabled: true,
      lastProbe: "failed",
      catalogMissing: true,
      runtimeReady: false,
    });

    expect(summary.label).toBe("连接失败");
    expect(summary.tone).toBe("danger");
  });
});

/**
 * 2026-09-29 用户报告：一条服务端已判 runtime-verified / usable=true 的对话模型
 * 在模型中心显示黄色「本地预设能力，上游未声明参数」。原因是能力来源只当成了措辞，
 * 却被当成了颜色开关 —— 三个非视频族压根没传这个字段，于是永远落到"本地预设"分支。
 * 颜色只回答"能不能用"，来源只回答"参数是哪来的"。
 */
describe("连接状态的颜色只看可用性", () => {
  const verifiedRow = {
    configured: true,
    enabled: true,
    runtimeReady: true,
    usable: true,
    runtimeProbeRequired: true,
    runtimeProbeComplete: true,
    verificationStatus: "runtime-verified" as const,
  };

  it("服务端判可用时必须显绿，即使能力是本地预设", () => {
    const summary = modelConnectionSummary({ ...verifiedRow, lastProbe: "unknown" });

    expect(summary.label).toBe("连接可用");
    expect(summary.tone).toBe("ready");
  });

  it("能力来源只改说明文字，不改颜色", () => {
    const local = modelConnectionSummary({
      ...verifiedRow,
      lastProbe: "unknown",
      capabilitySource: "local",
    });
    const upstream = modelConnectionSummary({
      ...verifiedRow,
      lastProbe: "unknown",
      capabilitySource: "upstream",
    });

    expect(local.tone).toBe("ready");
    expect(upstream.tone).toBe("ready");
    expect(local.detail).toContain("本地预设");
    expect(upstream.detail).toContain("能力合同已验证");
  });

  it("前端刚探测通过的行同样显绿", () => {
    const summary = modelConnectionSummary({
      configured: true,
      enabled: true,
      lastProbe: "ok",
      runtimeReady: true,
    });

    expect(summary.tone).toBe("ready");
  });

  it("视频目录匹配但接口鉴权未确认时不显示可用", () => {
    const summary = modelConnectionSummary({
      configured: true,
      enabled: true,
      lastProbe: "ok",
      credentialValidated: false,
      runtimeReady: true,
    });

    expect(summary.label).toBe("视频鉴权待确认");
    expect(summary.detail).toContain("视频接口尚未接受该 Key");
    expect(summary.tone).toBe("warning");
  });

  it("停用的模型显示已停用，不落到协议待适配", () => {
    const summary = modelConnectionSummary({
      ...verifiedRow,
      enabled: false,
    });

    expect(summary.label).toBe("已停用");
    expect(summary.tone).toBe("neutral");
  });

  it("待运行验证仍保持黄色（这里确实还没跑过真实调用）", () => {
    const summary = modelConnectionSummary({
      configured: true,
      enabled: true,
      lastProbe: "ok",
      runtimeReady: true,
      usable: false,
      runtimeProbeRequired: true,
      runtimeProbeComplete: false,
    });

    expect(summary.label).toBe("目录已匹配，待运行验证");
    expect(summary.tone).toBe("warning");
  });
});
