// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  REFERENCE_CHIP_SHORT_LABEL,
  REFERENCE_TYPE_PREFIX,
  referenceChipNumberLabel,
  referenceDurationBadge,
  referenceInsertText,
  referenceMentionName,
} from "@/features/canvas/nodes/shared/referenceStripBadges";

describe("referenceChipNumberLabel", () => {
  it("序号原样显示，不做任何换算", () => {
    expect(referenceChipNumberLabel(1)).toBe("1");
    expect(referenceChipNumberLabel(16)).toBe("16");
  });

  it("非正数 / 非有限数不显示角标", () => {
    expect(referenceChipNumberLabel(0)).toBe("");
    expect(referenceChipNumberLabel(-3)).toBe("");
    expect(referenceChipNumberLabel(Number.NaN)).toBe("");
    expect(referenceChipNumberLabel(Number.POSITIVE_INFINITY)).toBe("");
  });

  it("小数序号取整——角标只可能来自 1-based 计数", () => {
    expect(referenceChipNumberLabel(2.9)).toBe("2");
  });

  it("单类型的一行保持裸数字，不加类型前缀", () => {
    // 只挂图片时类型是自明的、角标也不会撞号，加个「图」字纯属噪声。
    expect(referenceChipNumberLabel(1, "image", false)).toBe("1");
    expect(referenceChipNumberLabel(3, "video", false)).toBe("3");
    // 调用方没给类型 / 没给混合标记时按旧行为（裸数字）。
    expect(referenceChipNumberLabel(1, "image")).toBe("1");
    expect(referenceChipNumberLabel(1)).toBe("1");
  });

  it("混合类型的一行给出单字类型前缀，三个「1」不再互相冒充", () => {
    // 按类型各数各的序号是既定口径（后端按 reference_images[] 等三个平行数组解释
    // 引用），所以混合行出现三个 1 是正常的；靠前缀把它们区分开。
    expect(referenceChipNumberLabel(1, "image", true)).toBe("图1");
    expect(referenceChipNumberLabel(1, "video", true)).toBe("视1");
    expect(referenceChipNumberLabel(1, "audio", true)).toBe("音1");
    expect(referenceChipNumberLabel(11, "image", true)).toBe("图11");
  });

  it("前缀表覆盖三种类型且互不相同", () => {
    const values = Object.values(REFERENCE_CHIP_SHORT_LABEL);
    expect(values).toEqual(["图", "视", "音"]);
    expect(new Set(values).size).toBe(3);
  });

  it("前缀只加在角标上；点一下写进提示词的仍是 @图片N 那一族", () => {
    // 这次改动的核心约束：角标可以带类型字，但插入文本必须仍是后端认的 token
    // —— 否则 video_request_contract.py 的 prompt_reference_missing 会静默放行。
    expect(referenceMentionName("image", 1)).toBe("图片1");
    expect(referenceInsertText(referenceMentionName("image", 1))).toBe("@图片1 ");
    expect(referenceInsertText(referenceMentionName("video", 1))).toBe("@视频1 ");
    expect(referenceInsertText(referenceMentionName("audio", 1))).toBe("@音频1 ");
  });
});

describe("referenceMentionName", () => {
  it("与候选列表的 name 同构：前缀 + 同类型序号", () => {
    expect(referenceMentionName("image", 3)).toBe("图片3");
    expect(referenceMentionName("video", 1)).toBe("视频1");
    expect(referenceMentionName("audio", 12)).toBe("音频12");
  });

  it("前缀表与三类型一一对应", () => {
    expect(REFERENCE_TYPE_PREFIX.image).toBe("图片");
    expect(REFERENCE_TYPE_PREFIX.video).toBe("视频");
    expect(REFERENCE_TYPE_PREFIX.audio).toBe("音频");
  });

  it("非法序号给空串，而不是造出「图片0」这种假引用", () => {
    expect(referenceMentionName("image", 0)).toBe("");
    expect(referenceMentionName("image", Number.NaN)).toBe("");
  });
});

describe("referenceDurationBadge", () => {
  it("视频按毫秒出一位小数的秒", () => {
    expect(referenceDurationBadge("video", 2000)).toBe("2.0s");
    expect(referenceDurationBadge("video", 2530)).toBe("2.5s");
  });

  it("音频同样给时长角标", () => {
    expect(referenceDurationBadge("audio", 12_340)).toBe("12.3s");
  });

  it("图片永远没有时长角标——即便节点上带了 durationMs", () => {
    expect(referenceDurationBadge("image", 2000)).toBeNull();
  });

  it("时长未知 / 非法时宁可不显示，也不猜一个数字", () => {
    expect(referenceDurationBadge("video", null)).toBeNull();
    expect(referenceDurationBadge("video", undefined)).toBeNull();
    expect(referenceDurationBadge("video", 0)).toBeNull();
    expect(referenceDurationBadge("video", -100)).toBeNull();
    expect(referenceDurationBadge("video", Number.NaN)).toBeNull();
  });
});

describe("referenceInsertText", () => {
  it("写成 @名字 + 一个尾随空格", () => {
    expect(referenceInsertText("图片3")).toBe("@图片3 ");
  });

  it("首尾空白先裁掉，避免 @ 和名字之间出现空格（后端 pattern 不容错）", () => {
    expect(referenceInsertText("  图片3 ")).toBe("@图片3 ");
  });

  it("空名字不产生任何文本", () => {
    expect(referenceInsertText("")).toBe("");
    expect(referenceInsertText("   ")).toBe("");
  });
});
