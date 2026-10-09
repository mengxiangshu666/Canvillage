import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AgentCliSkillDialog } from "./AgentCliSkillDialog";

describe("AgentCliSkillDialog", () => {
  it("shows the real CLI command and real skill actions", () => {
    const onOpenSkillDrawer = vi.fn();
    const onOpenSkillStore = vi.fn();
    render(<AgentCliSkillDialog
      open
      onOpenChange={vi.fn()}
      projectId="project-a"
      canvasId="canvas-a"
      mountedSkillCount={2}
      totalSkillCount={9}
      autoMatching
      onOpenSkillDrawer={onOpenSkillDrawer}
      onOpenSkillStore={onOpenSkillStore}
    />);
    expect(screen.getByRole("dialog")).toHaveAttribute("data-agent-dialog", "v4");
    expect(screen.getByRole("dialog")).toHaveClass("village-agent-dialog-v4--cli");
    expect(screen.getByText(/village_canvas_cli_hidden\.ps1/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "打开技能栏" }));
    fireEvent.click(screen.getByRole("button", { name: "技能商店" }));
    expect(onOpenSkillDrawer).toHaveBeenCalledOnce();
    expect(onOpenSkillStore).toHaveBeenCalledOnce();
  });
});
