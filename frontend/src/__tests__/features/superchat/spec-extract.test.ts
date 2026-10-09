// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";
import { extractStructuredBlocks } from "@/features/superchat/spec-extract";

const specBody = JSON.stringify({
  type: "keyframe_video",
  root: "root",
  elements: {
    root: {
      type: "Card",
      props: { title: "Episode 1" },
      children: ["video_1"],
    },
    video_1: {
      type: "Video",
      props: {
        src: "/static/projects/demo/video.mp4",
        overlayTitle: "Beat 1",
      },
      children: [],
    },
  },
});

describe("extractStructuredBlocks", () => {
  it("parses ui-spec tags with attributes", () => {
    const result = extractStructuredBlocks({
      text: `<ui-spec type="keyframe_video">${specBody}</ui-spec>`,
      raw: null,
    });

    expect(result.displayText).toBe("");
    expect(result.blocks).toHaveLength(1);
    expect(result.blocks[0]?.label).toBe("ui-spec");
  });

  it("parses ui-spec tags inside json-render fences", () => {
    const result = extractStructuredBlocks({
      text: `\`\`\`json-render\n<ui-spec data-kind="video">${specBody}</ui-spec>\n\`\`\``,
      raw: null,
    });

    expect(result.displayText).toBe("");
    expect(result.blocks).toHaveLength(1);
    expect(result.blocks[0]?.label).toBe("ui-spec");
  });

  it("repairs a ui-spec JSON block missing one trailing object closer", () => {
    const malformed = specBody.slice(0, -1);
    const result = extractStructuredBlocks({
      text: `<ui-spec>${malformed}</ui-spec>`,
      raw: null,
    });

    expect(result.displayText).toBe("");
    expect(result.blocks).toHaveLength(1);
    expect(result.blocks[0]?.label).toBe("ui-spec");
  });

  it("recovers when prose mentions ui-spec before the real tag", () => {
    const result = extractStructuredBlocks({
      text: `当前环境支持 <ui-spec> JSON 块。真正内容：<ui-spec>${specBody}</ui-spec>`,
      raw: null,
    });

    expect(result.blocks).toHaveLength(1);
    expect(result.blocks[0]?.label).toBe("ui-spec");
  });

  it("parses multiple canonical specs from one media bundle tag", () => {
    const secondSpecBody = specBody.replace("video.mp4", "video-2.mp4");
    const result = extractStructuredBlocks({
      text: `<ui-spec type="media_bundle">[${specBody},${secondSpecBody}]</ui-spec>`,
      raw: null,
    });

    expect(result.displayText).toBe("");
    expect(result.blocks).toHaveLength(2);
    expect(result.blocks[0]?.label).toBe("ui-spec");
    expect(result.blocks[1]?.label).toBe("ui-spec");
  });
});

// `statusBadgeVariant` is private; drive it through the legacy keyframe_video
// coercion, which stamps the Badge element's `variant` from the status string.
function statusBadgeVariantFor(status: string): string {
  const body = JSON.stringify({
    type: "keyframe_video",
    props: { title: "Beat 1", status },
  });
  const result = extractStructuredBlocks({
    text: `<ui-spec>${body}</ui-spec>`,
    raw: null,
  });
  const spec = result.blocks[0]?.value as
    | { elements?: Record<string, { props?: { variant?: string } }> }
    | undefined;
  return spec?.elements?.status?.props?.variant ?? "";
}

describe("status badge variant", () => {
  it("does not paint a negated completion green", () => {
    // 这些串都“包含”完成/complete，但语义是否定的，不能显示成功色。
    for (const status of [
      "未完成",
      "还没有完成",
      "尚未完成",
      "等待完成",
      "无法完成",
      "incomplete",
      "not completed",
      "unfinished",
    ]) {
      expect(statusBadgeVariantFor(status), status).not.toBe("success");
    }
  });

  it("paints 未完成 as default, not success or danger", () => {
    expect(statusBadgeVariantFor("未完成")).toBe("default");
  });

  it("still paints a positive completion green", () => {
    for (const status of ["完成", "已完成", "completed", "done", "complete"]) {
      expect(statusBadgeVariantFor(status), status).toBe("success");
    }
  });

  it("keeps failure and in-flight colors", () => {
    for (const status of ["失败", "failed", "error"]) {
      expect(statusBadgeVariantFor(status), status).toBe("danger");
    }
    for (const status of ["生成中", "处理中", "running", "pending"]) {
      expect(statusBadgeVariantFor(status), status).toBe("info");
    }
  });
});
