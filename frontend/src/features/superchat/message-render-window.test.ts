// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  DEFAULT_MESSAGE_RENDER_LIMIT,
  deriveMessageRenderWindow,
  nextMessageRenderLimit,
} from "./message-render-window";

describe("message render window", () => {
  it("keeps the newest messages and reports hidden history", () => {
    const messages = Array.from({ length: 100 }, (_, index) => index + 1);
    const window = deriveMessageRenderWindow(messages, 60);

    expect(window.items).toHaveLength(60);
    expect(window.items[0]).toBe(41);
    expect(window.items[window.items.length - 1]).toBe(100);
    expect(window.hiddenCount).toBe(40);
  });

  it("keeps short conversations intact", () => {
    const messages = [1, 2, 3];
    expect(deriveMessageRenderWindow(messages, DEFAULT_MESSAGE_RENDER_LIMIT)).toEqual({
      items: messages,
      hiddenCount: 0,
    });
  });

  it("loads history in bounded batches", () => {
    expect(nextMessageRenderLimit(60, 150)).toBe(100);
    expect(nextMessageRenderLimit(100, 115)).toBe(115);
  });

  it("normalizes invalid limits", () => {
    expect(deriveMessageRenderWindow([1, 2, 3], Number.NaN).items).toHaveLength(3);
    expect(nextMessageRenderLimit(0, 20, 0)).toBe(2);
  });
});
