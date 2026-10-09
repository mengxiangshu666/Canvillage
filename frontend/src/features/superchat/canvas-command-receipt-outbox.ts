import type { ChatScope } from "@/features/superchat/types";
import type { CanvasCommandReceipt } from "@/features/superchat/canvas-command-receipts";
import { safeLocalStorageSet } from "@/lib/localStorageQuota";

const OUTBOX_PREFIX = "superchat:canvas-command-receipts:v1:";
const OUTBOX_LIMIT = 64;
const OUTBOX_TTL_MS = 24 * 60 * 60 * 1000;
export const CANVAS_RECEIPT_MAX_FAILED_ATTEMPTS = 5;

export interface PendingCanvasReceiptDelivery {
  receipt: CanvasCommandReceipt;
  scope: ChatScope;
  turnId: string;
  queuedAt: number;
  failedAttempts?: number;
  nextAttemptAt?: number;
}

const memoryOutbox = new Map<string, PendingCanvasReceiptDelivery[]>();

function storageKey(scopeKey: string): string {
  return `${OUTBOX_PREFIX}${scopeKey}`;
}

function validDelivery(value: unknown): value is PendingCanvasReceiptDelivery {
  if (!value || typeof value !== "object") return false;
  const delivery = value as Partial<PendingCanvasReceiptDelivery>;
  return Boolean(
    delivery.receipt?.receiptId
      && delivery.turnId?.trim()
      && delivery.scope?.kind
      && Number.isFinite(delivery.queuedAt)
      && (delivery.failedAttempts === undefined || Number.isSafeInteger(delivery.failedAttempts))
      && (delivery.nextAttemptAt === undefined || Number.isFinite(delivery.nextAttemptAt)),
  );
}

function writeOutbox(scopeKey: string, deliveries: PendingCanvasReceiptDelivery[]): void {
  const key = storageKey(scopeKey);
  memoryOutbox.set(key, deliveries);
  if (deliveries.length === 0) {
    try {
      localStorage.removeItem(key);
    } catch {
      // The in-memory copy still retries within this page session.
    }
    return;
  }
  safeLocalStorageSet(key, JSON.stringify(deliveries));
}

export function clearCanvasReceiptOutbox(scopeKey: string): void {
  writeOutbox(scopeKey, []);
}

export function loadCanvasReceiptOutbox(
  scopeKey: string,
  now = Date.now(),
): PendingCanvasReceiptDelivery[] {
  const key = storageKey(scopeKey);
  let candidates = memoryOutbox.get(key) ?? [];
  try {
    const raw = localStorage.getItem(key);
    if (raw) {
      const parsed = JSON.parse(raw) as unknown;
      if (Array.isArray(parsed)) candidates = parsed.filter(validDelivery);
    }
  } catch {
    // Use the in-memory copy when storage is restricted or corrupted.
  }
  const fresh = candidates
    .filter((delivery) => now - delivery.queuedAt <= OUTBOX_TTL_MS)
    .slice(-OUTBOX_LIMIT);
  if (fresh.length !== candidates.length) writeOutbox(scopeKey, fresh);
  else memoryOutbox.set(key, fresh);
  return fresh;
}

export function enqueueCanvasReceiptDelivery(
  scopeKey: string,
  delivery: PendingCanvasReceiptDelivery,
): void {
  const current = loadCanvasReceiptOutbox(scopeKey, delivery.queuedAt);
  const previous = current.find(
    (item) => item.receipt.receiptId === delivery.receipt.receiptId,
  );
  const nextDelivery = previous
    ? {
        ...delivery,
        ...(previous.failedAttempts !== undefined
          ? { failedAttempts: previous.failedAttempts }
          : {}),
        ...(previous.nextAttemptAt !== undefined
          ? { nextAttemptAt: previous.nextAttemptAt }
          : {}),
      }
    : delivery;
  const next = [
    ...current.filter((item) => item.receipt.receiptId !== delivery.receipt.receiptId),
    nextDelivery,
  ].slice(-OUTBOX_LIMIT);
  writeOutbox(scopeKey, next);
}

export function deferCanvasReceiptDelivery(
  scopeKey: string,
  receiptId: string,
  now = Date.now(),
): PendingCanvasReceiptDelivery | null {
  const current = loadCanvasReceiptOutbox(scopeKey, now);
  let deferred: PendingCanvasReceiptDelivery | null = null;
  const next = current.map((delivery) => {
    if (delivery.receipt.receiptId !== receiptId) return delivery;
    const previousAttempts = Math.max(0, Number(delivery.failedAttempts) || 0);
    if (previousAttempts >= CANVAS_RECEIPT_MAX_FAILED_ATTEMPTS) {
      deferred = delivery;
      return delivery;
    }
    const failedAttempts = previousAttempts + 1;
    const nextAttemptAt = failedAttempts < CANVAS_RECEIPT_MAX_FAILED_ATTEMPTS
      ? now + Math.min(500 * (2 ** (failedAttempts - 1)), 8_000)
      : undefined;
    const { nextAttemptAt: _previousNextAttemptAt, ...deliveryWithoutSchedule } = delivery;
    deferred = {
      ...deliveryWithoutSchedule,
      failedAttempts,
      ...(nextAttemptAt !== undefined ? { nextAttemptAt } : {}),
    };
    return deferred;
  });
  writeOutbox(scopeKey, next);
  return deferred;
}

export function resetCanvasReceiptDeliveryRetries(
  scopeKey: string,
  now = Date.now(),
): void {
  const current = loadCanvasReceiptOutbox(scopeKey, now);
  writeOutbox(
    scopeKey,
    current.map(({ failedAttempts: _failedAttempts, nextAttemptAt: _nextAttemptAt, ...delivery }) => (
      delivery
    )),
  );
}

export function acknowledgeCanvasReceiptDelivery(
  scopeKey: string,
  receiptId: string,
): void {
  const current = loadCanvasReceiptOutbox(scopeKey);
  writeOutbox(
    scopeKey,
    current.filter((delivery) => delivery.receipt.receiptId !== receiptId),
  );
}
