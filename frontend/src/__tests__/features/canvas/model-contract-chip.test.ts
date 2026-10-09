// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { wireContractChip } from "@/features/canvas/ui/modelContractChip";
import { describe, expect, it } from "vitest";

describe("节点选择器上的出线合同来源", () => {
  it("渠道自带且真实验证过才写已验证", () => {
    expect(wireContractChip({ wireContractSource: "channel", wireContractVerified: true }))
      .toBe("渠道合同·已验证");
  });

  it("渠道合同只有目录探测证据时写未验证", () => {
    expect(wireContractChip({ wireContractSource: "channel", wireContractVerified: false }))
      .toBe("渠道合同·未验证");
  });

  it("源码种子与通用兜底各自标出来", () => {
    expect(wireContractChip({ wireContractSource: "seed" })).toBe("未验证种子");
    expect(wireContractChip({ wireContractSource: "default" })).toBe("通用兜底");
  });

  it("合同读不动时优先报这个，不冒充任何来源", () => {
    expect(wireContractChip({
      wireContractSource: "seed",
      wireContractError: "wireContract.source 必须是 channel",
    })).toBe("合同读不动");
  });

  it("后端没给来源时什么都不画", () => {
    expect(wireContractChip({})).toBe("");
  });
});
