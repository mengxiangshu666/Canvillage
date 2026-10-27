import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AgentCliSkillDialog } from "./AgentCliSkillDialog";

function renderDialog() {
  const onOpenSkillDrawer = vi.fn();
  const onOpenSkillStore = vi.fn();
  render(
    <AgentCliSkillDialog
      open
      onOpenChange={vi.fn()}
      projectId="project-a"
      canvasId="canvas-a"
      mountedSkillCount={2}
      totalSkillCount={9}
      autoMatching
      connected
      onOpenSkillDrawer={onOpenSkillDrawer}
      onOpenSkillStore={onOpenSkillStore}
    />,
  );
  return { onOpenSkillDrawer, onOpenSkillStore };
}

describe("AgentCliSkillDialog", () => {
  it("shows the Codex plugin marketplace commands by default", () => {
    renderDialog();

    expect(screen.getByRole("dialog")).toHaveAttribute("data-agent-dialog", "v4");
    expect(screen.getByRole("dialog")).toHaveClass("village-agent-dialog-v4--cli");
    expect(
      screen.getByText('codex plugin marketplace add ".\\agent_codex_plugin"'),
    ).toBeInTheDocument();
    expect(screen.getByText(/codex plugin add village-canvas@village-canvas/)).toBeInTheDocument();
    expect(screen.getByText("本机在线")).toBeInTheDocument();
  });

  it("switches to the real CLI command and keeps the skill actions", () => {
    const { onOpenSkillDrawer, onOpenSkillStore } = renderDialog();

    fireEvent.click(screen.getByRole("button", { name: "CLI & Skill" }));

    expect(screen.getByText(/village_canvas_cli_hidden\.ps1/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "打开技能栏" }));
    fireEvent.click(screen.getByRole("button", { name: "技能商店" }));
    expect(onOpenSkillDrawer).toHaveBeenCalledOnce();
    expect(onOpenSkillStore).toHaveBeenCalledOnce();
  });
});
