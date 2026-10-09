// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

function read(relativePath: string): string {
  // 工作树上绝大多数源文件是 CRLF（core.autocrlf=true，本仓 1292 个 ts/tsx 里 980 个是），
  // 而下面的断言要跨行匹配，不归一化换行符就永远匹配不到。只在这里归一，不动源码。
  return readFileSync(resolve(process.cwd(), relativePath), "utf8").replace(/\r\n/g, "\n");
}

/**
 * 视频目录没到齐时，VideoNode 里那些 normalize* 派生出的是一组退化值
 * （qualityOptions / aspectRatioOptions 全空 → 480P / 空字符串），落盘后会被
 * autosave 带走，而空 aspectRatio 在下次加载时被 store 补成 1:1 —— 1:1 恰好是
 * 多数视频模型的合法档位，于是永远不会自我纠正。这段契约把「目录权威才允许写回」
 * 钉在源码上；行为断言在 canvas-store-aspect-ratio-backfill.test.ts（A2）和
 * useVideoModeReconciliation.test.ts（模式改写）里。
 */
describe("VideoNode parameter write-back gate", () => {
  it("derives the authoritative flag from loading / fallback / resolved model", () => {
    const source = read("src/features/canvas/nodes/VideoNode.tsx");

    expect(source).toContain(
      "const catalogAuthoritative =\n      !videoModelsLoading && !videoModelsFallback && Boolean(capabilityVideoModel);",
    );
  });

  it("returns early instead of persisting degraded params", () => {
    const source = read("src/features/canvas/nodes/VideoNode.tsx");

    expect(source).toContain("      if (!catalogAuthoritative) return;");
    // 闸门必须是 effect 的第一条语句，否则后面的 patch 计算仍会往 updateNodeData 走。
    expect(source).toContain(
      "    useEffect(() => {\n      if (!catalogAuthoritative) return;",
    );
  });

  it("keeps the flag in the effect dependency list", () => {
    const source = read("src/features/canvas/nodes/VideoNode.tsx");

    expect(source).toContain("      aspectRatio,\n      catalogAuthoritative,");
  });

  it("passes the flag through to the mode reconciliation hook", () => {
    const source = read("src/features/canvas/nodes/VideoNode.tsx");

    expect(source).toContain("catalogAuthoritative,");
    expect(source).toContain("updateNodeData,");
    expect(source).toMatch(/useVideoModeReconciliation\(\{[\s\S]{0,900}catalogAuthoritative,/);
  });
});
