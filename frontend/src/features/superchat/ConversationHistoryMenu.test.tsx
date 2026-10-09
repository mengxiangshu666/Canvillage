// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ConversationHistoryMenu } from "./ConversationHistoryMenu";

const conversations = [
  {
    id: "conversation-a",
    title: "打戏方案",
    created_at: "2026-08-19T08:00:00Z",
    updated_at: "2026-08-19T08:10:00Z",
    message_count: 4,
    preview: "已经完成角色和场景拆分",
  },
  {
    id: "conversation-b",
    title: "分镜调整",
    created_at: "2026-08-19T07:00:00Z",
    updated_at: "2026-08-19T07:10:00Z",
    message_count: 2,
    preview: "调整第三个镜头",
  },
];

describe("ConversationHistoryMenu", () => {
  it("does not delete when the confirmation is cancelled", async () => {
    const onDelete = vi.fn().mockResolvedValue(true);
    render(
      <ConversationHistoryMenu
        conversations={conversations}
        activeConversationId="conversation-a"
        loading={false}
        busy={false}
        deletingId={null}
        onRefresh={vi.fn()}
        onSwitch={vi.fn().mockReturnValue(true)}
        onDelete={onDelete}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "历史对话" }));
    fireEvent.click(await screen.findByRole("button", { name: "删除对话：分镜调整" }));
    fireEvent.click(screen.getByRole("button", { name: "取消" }));

    await waitFor(() => expect(screen.queryByText("删除这个历史会话？")).not.toBeInTheDocument());
    expect(onDelete).not.toHaveBeenCalled();
  });

  it("confirms a real delete without switching the conversation", async () => {
    const onDelete = vi.fn().mockResolvedValue(true);
    const onSwitch = vi.fn().mockReturnValue(true);
    render(
      <ConversationHistoryMenu
        conversations={conversations}
        activeConversationId="conversation-a"
        loading={false}
        busy={false}
        deletingId={null}
        onRefresh={vi.fn()}
        onSwitch={onSwitch}
        onDelete={onDelete}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "历史对话" }));
    fireEvent.click(await screen.findByRole("button", { name: "删除对话：打戏方案" }));

    expect(screen.getByText("删除这个历史会话？")).toBeInTheDocument();
    expect(screen.getByText("会话消息和执行记录会一起删除。")).toBeInTheDocument();
    expect(onSwitch).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    await waitFor(() => expect(onDelete).toHaveBeenCalledWith("conversation-a"));
    await waitFor(() => expect(screen.queryByText("删除这个历史会话？")).not.toBeInTheDocument());
  });

  it("keeps the active running conversation protected", async () => {
    render(
      <ConversationHistoryMenu
        conversations={conversations}
        activeConversationId="conversation-a"
        loading={false}
        busy
        deletingId={null}
        onRefresh={vi.fn()}
        onSwitch={vi.fn().mockReturnValue(false)}
        onDelete={vi.fn().mockResolvedValue(true)}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "历史对话" }));

    expect(await screen.findByRole("button", { name: "删除对话：打戏方案" }))
      .toBeDisabled();
    expect(screen.getByRole("button", { name: "删除对话：分镜调整" }))
      .not.toBeDisabled();
  });
});
