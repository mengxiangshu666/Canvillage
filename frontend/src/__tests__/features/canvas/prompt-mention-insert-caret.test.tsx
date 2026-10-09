// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeAll, describe, expect, it, vi } from "vitest";
import { createRef } from "react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { render } from "@testing-library/react";

import {
  PromptMentionEditor,
  type PromptMentionEditorHandle,
} from "@/features/canvas/nodes/PromptMentionEditor";

// jsdom 未实现 scrollIntoView（弹层高亮行的 ref 回调会用到）。
beforeAll(() => {
  if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = vi.fn();
  }
});

function placeCaret(node: Node, offset: number) {
  const range = document.createRange();
  range.setStart(node, offset);
  range.collapse(true);
  const selection = window.getSelection();
  if (!selection) throw new Error("jsdom selection unavailable");
  selection.removeAllRanges();
  selection.addRange(range);
  document.dispatchEvent(new Event("selectionchange"));
}

/**
 * 模拟「点参考素材缩略图」：焦点和 selection 都离开了编辑器。
 */
function moveSelectionOutside() {
  const outside = document.createElement("div");
  outside.textContent = "outside";
  document.body.appendChild(outside);
  const selection = window.getSelection();
  const range = document.createRange();
  range.selectNodeContents(outside);
  selection?.removeAllRanges();
  selection?.addRange(range);
  document.dispatchEvent(new Event("selectionchange"));
}

function mountEditor(value: string) {
  const ref = createRef<PromptMentionEditorHandle>();
  const onChange = vi.fn();
  const { container } = render(
    <PromptMentionEditor
      ref={ref}
      value={value}
      onChange={onChange}
      candidates={[]}
    />,
  );
  const editor = container.querySelector<HTMLElement>(
    ".prompt-mention-editor",
  );
  if (!editor) throw new Error("editor not rendered");
  return { ref, onChange, editor };
}

describe("PromptMentionEditor — 引用插入位置", () => {
  it("插到用户点过的句子中间，而不是末尾", () => {
    const { ref, onChange, editor } = mountEditor("一只猫在跑");
    // 「一只猫|在跑」——光标落在句子中间。
    editor.focus();
    placeCaret(editor.firstChild as Node, 3);
    moveSelectionOutside();

    ref.current?.insertTextAtCursor("@图片1 ");

    expect(editor.textContent).toBe("一只猫@图片1 在跑");
    expect(onChange).toHaveBeenCalledWith("一只猫@图片1 在跑");
  });

  it("没在编辑器里点过光标时，仍然回退到末尾", () => {
    const { ref, onChange, editor } = mountEditor("一只猫在跑");
    moveSelectionOutside();

    ref.current?.insertTextAtCursor("@图片1 ");

    expect(editor.textContent).toBe("一只猫在跑@图片1 ");
    expect(onChange).toHaveBeenCalledWith("一只猫在跑@图片1 ");
  });

  // 用户 2026-10-01 要求放开「已引用过就不再追加」的限制：同一张引用点几次就该写几次。
  // 这条从编辑器侧锁住结果——两次插入都要真的进 DOM、都要序列化进提交的提示词。
  it("同一段引用连插两次，两次都留在提示词里", () => {
    const { ref, onChange, editor } = mountEditor("一只猫在跑");
    editor.focus();
    placeCaret(editor.firstChild as Node, 3);
    moveSelectionOutside();

    ref.current?.insertTextAtCursor("@图片1 ");
    // 第二次插入前，selection 同样已经离开编辑器（点缩略图的真实情形）。
    moveSelectionOutside();
    ref.current?.insertTextAtCursor("@图片1 ");

    expect(editor.textContent).toBe("一只猫@图片1 @图片1 在跑");
    expect(onChange).toHaveBeenLastCalledWith("一只猫@图片1 @图片1 在跑");
  });
});

// 「已经引用过就不再追加」的守卫是节点侧做的：它先算一遍「追加后的提示词」，与原文
// 相同就直接 return。用户 2026-10-01 要求放开这条，所以节点侧不能再引入按提示词判重
// 的守卫；这个防回归锁盯住当时的实现方式（去重助手与两个调用点都不复存在）。
describe("引用插入不再按提示词判重", () => {
  it.each([
    "src/features/canvas/nodes/VideoNode.tsx",
    "src/features/canvas/nodes/ImageGenNode.tsx",
    "src/features/canvas/nodes/shared/referenceStripBadges.ts",
  ])("%s 里没有判重助手", (relativePath) => {
    const source = readFileSync(resolve(process.cwd(), relativePath), "utf8");
    expect(source).not.toContain("appendReferenceInsertText");
  });
});
