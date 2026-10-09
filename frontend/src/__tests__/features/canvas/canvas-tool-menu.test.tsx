// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CanvasQuickActionBar } from "@/features/canvas/ui/CanvasQuickActionBar";
import { useCanvasToolStore } from "@/features/canvas/ui/canvasToolStore";

const translations: Record<string, string> = {
  "canvas.quickbar.addNode": "添加节点",
  "canvas.quickbar.history": "历史资产",
  "canvas.quickbar.shortcuts": "快捷键",
  "canvas.quickbar.help": "帮助",
  "canvas.quickbar.viewManual": "查看手册",
  "canvas.quickbar.skills": "创作技能",
  "canvas.quickbar.skillsTooltip": "搜索并插入本地创作技能",
  "canvas.quickbar.starter": "工作流起步器",
  "canvas.quickbar.starterTooltip": "插入可编辑工作流",
  "canvas.quickbar.settings": "画布设置",
  "canvas.quickbar.settingsTooltip": "调整画布交互、显示与连线",
  "canvas.toolbar.toolMove": "移动工具",
  "canvas.toolbar.toolHand": "抓手工具",
  "canvas.toolbar.toolGroupLabel": "画布指针工具",
};

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => translations[key] ?? key }),
}));

vi.mock("@/features/canvas/ui/CanvasAddNodePanel", () => ({
  CanvasAddNodePanel: () => <div data-testid="add-node-panel" />,
}));
vi.mock("@/features/canvas/ui/CanvasShortcutsPanel", () => ({
  CanvasShortcutsPanel: () => <div data-testid="shortcuts-panel" />,
}));
vi.mock("@/features/canvas/ui/CanvasHistoryAssetsModal", () => ({
  CanvasHistoryAssetsModal: () => <div data-testid="history-modal" />,
}));
vi.mock("@/features/canvas/ui/CanvasSettingsPanel", () => ({
  CanvasSettingsPanel: ({ onClose }: { onClose: () => void }) => (
    <div data-testid="canvas-settings-panel">
      <h2>画布设置</h2>
      <button type="button" onClick={onClose}>关闭画布设置</button>
    </div>
  ),
}));
vi.mock("@/features/canvas/ui/CanvasSkillQuickPickPanel", () => ({
  CanvasSkillQuickPickPanel: ({ onSelect }: { onSelect: (skill: { id: string }) => void }) => (
    <button type="button" onClick={() => onSelect({ id: "agent.test" })}>选择测试技能</button>
  ),
}));

function renderBar() {
  render(
    <CanvasQuickActionBar
      skillItems={[]}
      onAddNode={vi.fn()}
      onAddSkill={vi.fn()}
      onUseStarterWorkflow={vi.fn()}
      onUseAsset={vi.fn()}
      onDeleteNode={vi.fn()}
    />,
  );
  return screen.getByRole("button", { name: "画布指针工具" });
}

describe("画布底部工具栏 — 移动 / 抓手工具", () => {
  beforeEach(() => {
    useCanvasToolStore.setState({ tool: "move" });
  });

  it("默认是移动工具", () => {
    expect(useCanvasToolStore.getState().tool).toBe("move");
  });

  it("所有入口共享统一 Dock 表面，并把激活状态暴露给辅助技术", async () => {
    const user = userEvent.setup();
    const toolButton = renderBar();
    const dock = toolButton.closest(".canvas-quick-action-surface");

    expect(dock).toHaveClass("canvas-dock-surface");
    expect(toolButton).toHaveClass("canvas-dock-button");
    expect(toolButton).toHaveAttribute("aria-pressed", "false");

    await user.click(toolButton);
    expect(toolButton).toHaveAttribute("aria-pressed", "true");
  });

  it("点开工具按钮弹出两行菜单，选抓手切换模式并收起菜单", async () => {
    const user = userEvent.setup();
    const toolButton = renderBar();

    expect(screen.queryByRole("menu")).not.toBeInTheDocument();

    await user.click(toolButton);
    const menu = screen.getByRole("menu", { name: "画布指针工具" });
    expect(menu).toBeInTheDocument();

    const move = screen.getByRole("menuitemradio", { name: /移动工具/ });
    const hand = screen.getByRole("menuitemradio", { name: /抓手工具/ });
    expect(move).toHaveAttribute("aria-checked", "true");
    expect(hand).toHaveAttribute("aria-checked", "false");

    await user.click(hand);
    expect(useCanvasToolStore.getState().tool).toBe("hand");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("菜单里能切回移动工具", async () => {
    const user = userEvent.setup();
    useCanvasToolStore.setState({ tool: "hand" });
    const toolButton = renderBar();

    await user.click(toolButton);
    await user.click(screen.getByRole("menuitemradio", { name: /移动工具/ }));
    expect(useCanvasToolStore.getState().tool).toBe("move");
  });

  it("从创作技能入口能把所选技能交给画布", async () => {
    const user = userEvent.setup();
    const onAddSkill = vi.fn();
    render(
      <CanvasQuickActionBar
        skillItems={[]}
        onAddNode={vi.fn()}
        onAddSkill={onAddSkill}
        onUseStarterWorkflow={vi.fn()}
        onUseAsset={vi.fn()}
        onDeleteNode={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "创作技能" }));
    await user.click(screen.getByRole("button", { name: "选择测试技能" }));
    expect(onAddSkill).toHaveBeenCalledWith({ id: "agent.test" });
  });

  it("设置入口打开画布设置面板", async () => {
    const user = userEvent.setup();
    renderBar();

    await user.click(screen.getByRole("button", { name: "画布设置" }));

    expect(screen.getByTestId("canvas-settings-panel")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "画布设置" })).toBeInTheDocument();
  });
});
