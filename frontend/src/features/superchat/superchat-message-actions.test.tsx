// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { MessageBubble } from "./superchat-panel";

describe("superchat message actions", () => {
  it("keeps one quiet trigger and exposes secondary actions on demand", async () => {
    const onTogglePin = vi.fn();
    const onDelete = vi.fn();

    render(
      <MessageBubble
        message={{
          id: "message-1",
          role: "user",
          text: "把这一镜改成夜景",
          timestamp: 1,
        }}
        variant="freezone"
        onOpenDetail={vi.fn()}
        onOpenMedia={vi.fn()}
        pinned={false}
        onDelete={onDelete}
        onTogglePin={onTogglePin}
      />,
    );

    expect(screen.getByText("把这一镜改成夜景")).toBeInTheDocument();
    expect(screen.getAllByRole("button")).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "消息操作" }));

    expect(await screen.findByRole("menuitem", { name: "复制" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "朗读" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "查看详情" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("menuitem", { name: "置顶消息" }));
    expect(onTogglePin).toHaveBeenCalledWith("message-1");

    fireEvent.click(screen.getByRole("button", { name: "消息操作" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "删除消息" }));
    expect(onDelete).toHaveBeenCalledWith("message-1");
  });

  it("shows a quiet receipt only when durable knowledge changed the turn", () => {
    render(
      <MessageBubble
        message={{
          id: "message-memory",
          role: "assistant",
          text: "已按你长期偏好的克制悬疑方向完成方案。",
          timestamp: 1,
          raw: {
            metadata: {
              knowledge_receipt: {
                used_count: 4,
                scope_counts: { project: 2, user: 1, professional: 1 },
                items: [{
                  memory_id: 7,
                  title: "动作镜头配方",
                  summary: "用时间轴组织动作因果链。",
                  kind: "experience_recipe",
                  scope_kind: "professional",
                  status: "validated",
                  source: "growth_distiller",
                  confidence: 0.86,
                  evidence_count: 3,
                  retrieved_count: 4,
                  applied_count: 2,
                  positive_count: 2,
                  negative_count: 0,
                  candidate_recall: false,
                  execution_rule: true,
                  influence: "execution_rule",
                }],
              },
            },
          },
        }}
        variant="freezone"
        onOpenDetail={vi.fn()}
        onOpenMedia={vi.fn()}
        pinned={false}
        onDelete={vi.fn()}
        onTogglePin={vi.fn()}
      />,
    );

    expect(screen.getByText("本轮使用了 4 条项目与长期经验")).toBeInTheDocument();
    fireEvent.click(screen.getByText("本轮使用了 4 条项目与长期经验"));
    expect(screen.getByText("动作镜头配方")).toBeInTheDocument();
    expect(screen.getByText("用时间轴组织动作因果链。")).toBeInTheDocument();
    expect(screen.getByText("执行规则已注入")).toBeInTheDocument();
  });
});
