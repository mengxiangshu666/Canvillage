// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { render, screen, within } from "@testing-library/react";
import { createElement } from "react";
import { describe, expect, it, vi } from "vitest";

import { MessageBubble, shouldRenderAttachmentChip } from "./superchat-panel";
import type { ChatAttachment, ChatMessage } from "./types";

describe("superchat user reference previews", () => {
  const imageAttachment: ChatAttachment = {
    id: "canvas-ref-image-1",
    type: "canvas_image",
    kind: "image",
    mimeType: "image/png",
    url: "/static/projects/p/image-1.png",
    source: "canvas_node",
  };

  it("keeps the referenced image and text inside the same user message", () => {
    const message: ChatMessage = {
      id: "user-turn-1",
      role: "user",
      text: "这是啥",
      attachments: [imageAttachment],
      timestamp: Date.now(),
    };

    render(createElement(MessageBubble, {
      message,
      variant: "freezone",
      onOpenDetail: vi.fn(),
      onOpenMedia: vi.fn(),
      pinned: false,
      onDelete: vi.fn(),
      onTogglePin: vi.fn(),
    }));

    const article = screen.getByRole("article");
    expect(within(article).getByText("这是啥")).toBeInTheDocument();
    expect(within(article).getByLabelText("本轮图片引用")).toBeInTheDocument();
    expect(article.querySelector("img")).toHaveAttribute("src", imageAttachment.url);
  });

  it("shows media only when the message surface explicitly enables previews", () => {
    expect(shouldRenderAttachmentChip(imageAttachment)).toBe(false);
    expect(shouldRenderAttachmentChip(imageAttachment, true)).toBe(true);
  });
});
