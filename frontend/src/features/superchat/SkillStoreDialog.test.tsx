// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { SkillStoreCatalog } from "@/api/skill-store";

import { SkillStoreDialog } from "./SkillStoreDialog";

const getSkillStoreCatalog = vi.fn();
const importSkillStoreFile = vi.fn();
const installSkillStoreItem = vi.fn();
const uninstallSkillStoreItem = vi.fn();
const deleteSkillStoreItem = vi.fn();

vi.mock("@/api/skill-store", () => ({
  getSkillStoreCatalog: (...args: unknown[]) => getSkillStoreCatalog(...args),
  importSkillStoreFile: (...args: unknown[]) => importSkillStoreFile(...args),
  installSkillStoreItem: (...args: unknown[]) => installSkillStoreItem(...args),
  uninstallSkillStoreItem: (...args: unknown[]) => uninstallSkillStoreItem(...args),
  deleteSkillStoreItem: (...args: unknown[]) => deleteSkillStoreItem(...args),
  skillStoreAgentId: (skillId: string) => `store:${skillId}`,
}));

const catalog: SkillStoreCatalog = {
  total: 2,
  installed: 1,
  custom: 1,
  categories: ["分镜镜头", "风格美学"],
  items: [
    {
      id: "libtv.storyboard",
      skill_key: "storyboard",
      name: "电影分镜大师",
      description: "把剧本拆成可生成镜头。",
      category: "分镜镜头",
      source: "libtv_reference",
      source_label: "LibTV 参考技能",
      version: "1.0.0",
      tags: ["分镜镜头"],
      activation: "读取剧本并创建镜头节点",
      contract: {
        schema_version: "canvas_skill_contract.v1",
        maturity: "production_ready",
        readiness_score: 92,
        readiness_issues: [],
        purpose: "把剧本拆成镜头",
        inputs: "剧本和角色设定",
        workflow: ["读取剧本", "创建镜头", "核对回执"],
        output_contract: "可生成的分镜节点",
        quality_gate: ["镜头可执行"],
        canvas_commands: ["create_shot_sequence"],
        completion_rule: "核对画布回执",
      },
      installed: false,
      enabled: false,
      builtin: true,
      removable: false,
    },
    {
      id: "custom.lighting",
      skill_key: "lighting",
      name: "灯光导演",
      description: "规划场景灯光。",
      category: "风格美学",
      source: "custom",
      source_label: "我的技能",
      version: "1.0.0",
      tags: ["风格美学"],
      activation: "读取场景并回写灯光提示词",
      contract: {
        schema_version: "canvas_skill_contract.v1",
        maturity: "workflow_ready",
        readiness_score: 68,
        readiness_issues: ["缺少质量验收条件"],
        purpose: "规划灯光",
        inputs: "场景节点",
        workflow: ["读取场景", "回写提示词"],
        output_contract: "灯光提示词",
        quality_gate: [],
        canvas_commands: ["update_node_prompt"],
        completion_rule: "核对画布回执",
      },
      installed: true,
      enabled: true,
      builtin: false,
      removable: true,
    },
  ],
};

describe("SkillStoreDialog", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getSkillStoreCatalog.mockResolvedValue(catalog);
    installSkillStoreItem.mockResolvedValue({ ...catalog.items[0], installed: true, enabled: true });
    importSkillStoreFile.mockResolvedValue({ items: [catalog.items[1]], imported: 1 });
  });

  it("lists skills as LibTV-style rows with slug and search", async () => {
    render(
      <SkillStoreDialog
        open
        onOpenChange={vi.fn()}
        mountedSkillIds={[]}
        onToggleMount={vi.fn()}
      />,
    );

    expect(await screen.findByText("电影分镜大师")).toBeInTheDocument();
    expect(screen.getByText("灯光导演")).toBeInTheDocument();
    expect(screen.getByText("/storyboard")).toBeInTheDocument();
    expect(screen.getByText("Skill")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("搜索 Skill")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "通用" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "收藏" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "我的" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "添加技能" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "筛选技能范围" })).toBeInTheDocument();
  });

  it("shows a floating cover card when a row is hovered", async () => {
    render(
      <SkillStoreDialog
        open
        onOpenChange={vi.fn()}
        mountedSkillIds={[]}
        onToggleMount={vi.fn()}
      />,
    );

    const row = (await screen.findByText("电影分镜大师")).closest('[role="button"]');
    expect(row).not.toBeNull();
    fireEvent.mouseEnter(row as HTMLElement);

    const card = await waitFor(() => {
      const found = document.querySelector('[data-skill-hover-card="libtv.storyboard"]');
      expect(found).not.toBeNull();
      return found as HTMLElement;
    });
    expect(card.textContent).toContain("电影分镜大师");
    expect(card.textContent).toContain("可执行 92");

    fireEvent.mouseLeave(row as HTMLElement);
    await waitFor(() =>
      expect(document.querySelector('[data-skill-hover-card="libtv.storyboard"]')).toBeNull(),
    );
  });

  it("expands a row into details and enables the skill", async () => {
    render(
      <SkillStoreDialog
        open
        onOpenChange={vi.fn()}
        mountedSkillIds={[]}
        onToggleMount={vi.fn()}
      />,
    );

    await screen.findByText("电影分镜大师");
    fireEvent.click(screen.getByText("电影分镜大师"));

    expect(await screen.findByText("可执行 92")).toBeInTheDocument();
    expect(screen.getByText("输出：可生成的分镜节点")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "启用" }));
    await waitFor(() => expect(installSkillStoreItem).toHaveBeenCalledWith("libtv.storyboard"));
  });

  it("uploads a custom SKILL.md from the add menu", async () => {
    render(
      <SkillStoreDialog
        open
        onOpenChange={vi.fn()}
        mountedSkillIds={[]}
        onToggleMount={vi.fn()}
      />,
    );
    await screen.findByText("电影分镜大师");

    fireEvent.click(screen.getByRole("button", { name: "添加技能" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "上传技能" }));

    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["# Skill"], "SKILL.md", { type: "text/markdown" });
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => expect(importSkillStoreFile).toHaveBeenCalledWith(file));
  });

  it("filters to my skills from the scope menu", async () => {
    render(
      <SkillStoreDialog
        open
        onOpenChange={vi.fn()}
        mountedSkillIds={[]}
        onToggleMount={vi.fn()}
      />,
    );
    await screen.findByText("电影分镜大师");

    fireEvent.click(screen.getByRole("button", { name: "筛选技能范围" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /我的技能/ }));

    expect(screen.queryByText("电影分镜大师")).not.toBeInTheDocument();
    expect(screen.getByText("灯光导演")).toBeInTheDocument();
  });
});