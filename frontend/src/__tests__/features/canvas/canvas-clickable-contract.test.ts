// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import * as ts from "typescript";
import { describe, expect, it } from "vitest";

const sourceRoots = ["src/features/canvas", "src/features/freezone"];
const actionAttributes = new Set([
  "onClick",
  "onPointerDown",
  "onMouseDown",
  "onDoubleClick",
  "onChange",
  "onSubmit",
]);

function sourceFiles(): string[] {
  const files: string[] = [];
  const visit = (directory: string) => {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const path = join(directory, entry.name);
      if (entry.isDirectory()) visit(path);
      else if (entry.name.endsWith(".tsx") && !entry.name.endsWith(".test.tsx")) files.push(path);
    }
  };
  sourceRoots.forEach(visit);
  return files;
}

function buttonContractViolations(file: string): string[] {
  const source = readFileSync(file, "utf8");
  const sourceFile = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const violations: string[] = [];

  const visit = (node: ts.Node) => {
    const opening = ts.isJsxElement(node)
      ? node.openingElement
      : ts.isJsxSelfClosingElement(node)
        ? node
        : null;
    if (opening?.tagName.getText(sourceFile) === "button") {
      const attributes = new Map<string, ts.JsxAttribute>();
      let hasSpread = false;
      for (const attribute of opening.attributes.properties) {
        if (ts.isJsxSpreadAttribute(attribute)) {
          hasSpread = true;
        } else {
          attributes.set(attribute.name.getText(sourceFile), attribute);
        }
      }
      const typeText = attributes.get("type")?.initializer?.getText(sourceFile) ?? "";
      const hasAction = [...actionAttributes].some((name) => attributes.has(name));
      const isFormAction = /submit|reset/.test(typeText);
      const isPermanentlyDisabled = attributes.has("disabled") && !attributes.get("disabled")?.initializer;
      if ((!hasAction && !isFormAction && !hasSpread) || isPermanentlyDisabled) {
        const line = sourceFile.getLineAndCharacterOfPosition(opening.getStart(sourceFile)).line + 1;
        violations.push(`${file}:${line}`);
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(sourceFile);
  return violations;
}

describe("canvas clickable controls", () => {
  it("keeps every visible button connected to a real action", () => {
    const violations = sourceFiles().flatMap(buttonContractViolations);
    expect(violations, `inert canvas buttons:\n${violations.join("\n")}`).toEqual([]);
  });

  it("does not ship placeholder actions labelled as unfinished", () => {
    const forbiddenMarkers = [
      { label: "待实现", pattern: /待实现/ },
      { label: "暂时隐藏", pattern: /暂时隐藏/ },
      { label: "{false &&", pattern: /\{\s*false\s*&&/ },
      { label: "comingSoon", pattern: /comingSoon/ },
    ];
    const violations = sourceFiles().flatMap((file) => {
      const source = readFileSync(file, "utf8");
      return forbiddenMarkers
        .filter(({ pattern }) => pattern.test(source))
        .map(({ label }) => `${file}: ${label}`);
    });
    expect(violations, `unfinished canvas controls:\n${violations.join("\n")}`).toEqual([]);
  });

  it("keeps storyboard stitching connected to a real export flow", () => {
    const toolbar = readFileSync("src/features/canvas/ui/StoryboardGroupToolbar.tsx", "utf8");
    expect(toolbar).toContain("mergeStoryboardImages");
    expect(toolbar).toContain("uploadLocalImageToBackend");
    expect(toolbar).toContain("addDerivedExportNode");
    expect(toolbar).not.toContain("stitchComingSoon");
  });

  it("keeps storyboard download / regenerate wired to real flows", () => {
    // 分镜组上的「批量下载」「重新生成分镜图」必须有真实能力：前者真的存图，
    // 后者真的把缺图/失败的成员重新排队（都走量产实现，不是空按钮）。
    const toolbar = readFileSync("src/features/canvas/ui/StoryboardGroupToolbar.tsx", "utf8");
    expect(toolbar).toContain("downloadStoryboardGroupImages");
    expect(toolbar).toContain("regenerateStoryboardGroupImages");
    // 出图是节点挂载后自提交的，重新排队后必须把组带回视口。
    expect(toolbar).toContain("requestFocusNode");
  });
});
