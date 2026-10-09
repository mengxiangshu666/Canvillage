// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

describe("ImageGenNode context palette", () => {
  it("wires ImageGenNode to context collection and prompt insertion", () => {
    const source = readFileSync(
      resolve(process.cwd(), "src/features/canvas/nodes/ImageGenNode.tsx"),
      "utf8",
    );

    // 调色盘按钮通过 NodeContextPromptPaletteButton 接入。
    expect(source).toContain("<NodeContextPromptPaletteButton");
    expect(source).toContain("nodeId={id}");
    // 插入走编辑器命令式 API（回调稳定，不再依赖 prompt）。
    expect(source).toContain("contextPromptPaletteInsertionText(entry)");
    expect(source).toContain("insertTextAtCursor(");
    expect(source).toContain("ref={promptEditorRef}");

    // 命名色在打开弹层时按需读取，不在每个拖动帧订阅/扫描整张图。
    const wrapperSource = readFileSync(
      resolve(
        process.cwd(),
        "src/features/canvas/nodes/ContextPromptPaletteButton.tsx",
      ),
      "utf8",
    );
    expect(wrapperSource).toContain("const { nodes, edges } = useCanvasStore.getState()")
    expect(wrapperSource).toContain("resolvePalette={resolvePalette}")
    expect(wrapperSource).not.toContain("useCanvasStore((state) => state.nodes)")
    expect(wrapperSource).not.toContain("useCanvasStore((state) => state.edges)")
  });
});
