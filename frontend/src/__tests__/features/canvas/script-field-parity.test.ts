// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { SCRIPT_TABLE_FIELDS } from "@/features/canvas/nodes/script/scriptFields";

/**
 * 跨层字段对账：前端分镜表列 ↔ 后端 `FreezoneStoryScriptRow`。
 *
 * 这两份清单没有代码级共享（一份是 TS 列定义，一份是 pydantic 模型），加一个字段要改
 * 五处：pydantic 模型 / LLM 任务字段清单 / `api/scriptContract.ts` 行类型 / `scriptFields.ts` 列 /
 * 资产视图聚合。少改一处不会让任何既有测试变红 —— 表格照样渲染，只是那一列永远空。
 * `prop_tags` 就是这么补进来的，所以把对账钉在这里。
 */

// vitest 的 cwd 是 `frontend/`，后端源码在上一层仓库根。
const frontendRoot = process.cwd();
const repoRoot = resolve(frontendRoot, "..");
const read = (path: string) => readFileSync(resolve(frontendRoot, path), "utf8");
const readRepo = (path: string) => readFileSync(resolve(repoRoot, path), "utf8");

/** 后端模型里由后端自己回填、本来就不作为表格列的几个字段。 */
const BACKEND_ONLY_FIELDS = new Set([
  "keyframe_index",
  "shot_id",
  "shot_order",
  "display_shot_no",
  // Model-authored classification drives contract validation, not an editable column.
  "content_intent",
  // Typed planning maps/lists are not scalar table cells.
  "sequence_ids", "character_state_start", "character_state_end",
  "scene_descriptions", "prop_descriptions", "duration_policy",
]);

function backendRowFieldNames(): string[] {
  const source = readRepo("src/novelvideo/ports/story_script.py");
  const classBody = source.match(
    /class FreezoneStoryScriptRow\(BaseModel\):\n([\s\S]*?)\n\n\n/,
  )?.[1];
  if (!classBody) throw new Error("未在 story_script.py 里找到 FreezoneStoryScriptRow 类体");
  const names = [...classBody.matchAll(/^ {4}([a-z_][a-z0-9_]*)\s*:/gm)].map(
    (match) => match[1],
  );
  if (names.length === 0) throw new Error("FreezoneStoryScriptRow 类体解析出 0 个字段");
  return names;
}

describe("分镜表字段跨层对账", () => {
  it("前端列集合覆盖后端所有可编辑字段", () => {
    const backend = new Set(backendRowFieldNames());
    const frontend = new Set(SCRIPT_TABLE_FIELDS.map((field) => field.key));

    const missingInFrontend = [...backend].filter(
      (name) => !frontend.has(name) && !BACKEND_ONLY_FIELDS.has(name),
    );
    const missingInBackend = [...frontend].filter((name) => !backend.has(name));

    // 报表式断言：失败时直接看出是哪个字段掉了一边，而不是一句 toEqual 的差集。
    expect({ 后端有前端没有: missingInFrontend, 前端有后端没有: missingInBackend }).toEqual({
      后端有前端没有: [],
      前端有后端没有: [],
    });
  });

  it("LLM 任务里的输出字段清单覆盖每个后端字段的中文名", () => {
    const task = readRepo("src/novelvideo/freezone/text_node.py");
    const fieldList = task.match(/输出字段必须覆盖：([^"]+)"/)?.[1];
    if (!fieldList) throw new Error("未在 text_node.py 里找到「输出字段必须覆盖」清单");

    const labels = new Set(
      fieldList.split("、").map((label) => label.replace(/[。．.,，;；]+$/, "")),
    );
    // 两类跳过：① 角色图/参考由后端回填，任务只要求模型留空；② 第 2 组的角色三连列
    // 不在任务清单里（模型只被点名写「角色1」一族，多角色靠它在同一行里自行补）。
    // 前两条都不是「字段掉了」，逐字段断言的是「列存在就有人在让它产出」。
    for (const field of SCRIPT_TABLE_FIELDS) {
      if (field.key.startsWith("character_image_") || field.key === "reference") continue;
      if (field.key.endsWith("_2")) continue;
      expect({ key: field.key, listed: labels.has(field.label) }).toEqual({
        key: field.key,
        listed: true,
      });
    }
  });

  it("前端行类型声明了后端模型的每个字段（否则回写只存在于类型层）", () => {
    const ops = read("src/api/scriptContract.ts");
    const rowType = ops.match(
      /export interface FreezoneStoryScriptRow \{([\s\S]*?)\n\}/,
    )?.[1];
    if (!rowType) throw new Error("未在 scriptContract.ts 里找到 FreezoneStoryScriptRow 接口");

    const declared = new Set(
      [...rowType.matchAll(/(?:^|;)\s*([a-z_][a-z0-9_]*)\??:/gm)].map(
        (match) => match[1],
      ),
    );
    const missing = backendRowFieldNames().filter((name) => !declared.has(name));
    expect(missing).toEqual([]);
  });
});
