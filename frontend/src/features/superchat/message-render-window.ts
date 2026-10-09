// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

export const DEFAULT_MESSAGE_RENDER_LIMIT = 60;
export const MESSAGE_RENDER_BATCH_SIZE = 40;

export type MessageRenderWindow<T> = {
  items: T[];
  hiddenCount: number;
};

export function deriveMessageRenderWindow<T>(
  items: T[],
  requestedLimit: number,
): MessageRenderWindow<T> {
  const limit = Number.isFinite(requestedLimit)
    ? Math.max(1, Math.floor(requestedLimit))
    : DEFAULT_MESSAGE_RENDER_LIMIT;
  const start = Math.max(0, items.length - limit);
  return {
    items: items.slice(start),
    hiddenCount: start,
  };
}

export function nextMessageRenderLimit(
  currentLimit: number,
  totalCount: number,
  batchSize = MESSAGE_RENDER_BATCH_SIZE,
): number {
  const safeTotal = Math.max(0, Math.floor(totalCount));
  const safeCurrent = Math.max(1, Math.floor(currentLimit));
  const safeBatch = Math.max(1, Math.floor(batchSize));
  return Math.min(safeTotal, safeCurrent + safeBatch);
}
