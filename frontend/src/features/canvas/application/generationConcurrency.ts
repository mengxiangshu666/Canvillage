// SPDX-License-Identifier: Elastic-2.0

export const GENERATION_BATCH_MAX = 12;
/**
 * 出图 / 出视频的并发度：500。
 *
 * 超出这个数量的付费提交进入本页面的生成队列。唯一的例外是「有顺序」的下游节点：它的
 * 上游还在排队 / 正在出图时，`gate` 会让它等上游落地后再提交（见
 * `withGlobalGenerationSlot` 与 `application/generationDependencies.ts`）——「有顺序的
 * 按顺序来」靠的是闸门，不是额外的限流。
 *
 * 数值仍集中在这里，因为 `runGenerationQueue` 的单节点批量默认并发也吃它；同时它还是
 * `generationQueueUnitHint` /「·排队」徽标的阈值来源。
 */
export const GENERATION_CONCURRENCY_DEFAULT = 500;

/**
 * 闸门状态（每次派位都会重新问一次，必须是**实时**状态，不能是提交那一刻的快照）：
 * - `go`      现在就能跑。
 * - `waiting` 上游只是挂着「挂载即出图」的排队标记、还没进信号量。上游提交在即，短等即可，
 *             超过 {@link QUEUED_UPSTREAM_WAIT_LIMIT_MS} 就放行——上游要是根本没挂载上
 *             （画布虚拟化），不能把下游一起拖死。
 * - `running` 上游已经占着槽在出图。等它 release 后再派位，这就是「按顺序」的正路。
 */
export type GenerationGate = 'go' | 'waiting' | 'running';

/** 上游只是「排队」时最多等多久（正常情况上游下一个 tick 就提交，60s 纯属兜底）。 */
const QUEUED_UPSTREAM_WAIT_LIMIT_MS = 60_000;
/** 上游「正在出图」时最多等多久——兜住依赖成环 / 上游僵死，宁可顺序不准也不能卡死画布。 */
const RUNNING_UPSTREAM_WAIT_LIMIT_MS = 15 * 60 * 1000;
/** 被挡住时的重新派位间隔：上游不吃本信号量（如服务端 WorkflowRun）时靠它唤醒。 */
const BLOCKED_REPOLL_MS = 1_000;

export interface GenerationSlotOptions {
  /** 本次提交属于哪个节点。同一节点一次批量里的 N 个 run 共用同一个 id。 */
  nodeId?: string;
  /** 「现在还轮不到我」的实时判据；省略即永远可跑。 */
  gate?: () => GenerationGate;
}

let globalActive = 0;
interface GlobalWaiter {
  resolve: () => void;
  reject: (reason: unknown) => void;
  signal?: AbortSignal;
  onAbort?: () => void;
  nodeId?: string;
  gate?: () => GenerationGate;
  blockedSince: number | null;
}
const globalWaiters: GlobalWaiter[] = [];
/** 正在占槽的节点 → 占的槽数（同一节点的 N 张批量会同时占多个）。 */
const inFlightNodes = new Map<string, number>();
/** 已入队、还没拿到槽的节点 id。 */
const queuedNodes = new Set<string>();
/**
 * 已经决定要出图、但还没走到信号量的节点 id。
 *
 * 为什么需要它：节点的自动提交是「挂载后先消费排队标记、再走一大段异步准备、最后才
 * `withGlobalGenerationSlot`」。这段准备期里上游既没有排队标记、也还没进信号量，
 * 下游的闸门会误判成「上游闲着」而跟着一起提交——真机核验里 A→B 的链就是这样并发跑掉的。
 * 这个集合在 handleSubmit 一开头就登记（纯内存，不落盘），整批结束才撤，把窗口补上；
 * 也因为它只活在内存里，页面被强杀后不会像持久化的 isGenerating 那样留下假信号。
 */
const intentNodes = new Set<string>();
let repollTimer: ReturnType<typeof setTimeout> | null = null;

function generationAbortReason(signal?: AbortSignal): unknown {
  return signal?.reason ?? new DOMException("Generation queue aborted", "AbortError");
}

/** 这个节点此刻是不是「有活」：已决定出图、在等槽、或占着槽。 */
export function isGenerationNodeBusy(nodeId: string): boolean {
  return intentNodes.has(nodeId)
    || queuedNodes.has(nodeId)
    || (inFlightNodes.get(nodeId) ?? 0) > 0;
}

/** 提交入口第一步就登记：这个节点接下来要出图，下游先别动。 */
export function markGenerationIntent(nodeId: string): void {
  if (nodeId) intentNodes.add(nodeId);
}

/** 整批结束（含早退 / 失败 / 取消）时撤掉登记，并立刻放行被它挡住的下游。 */
export function clearGenerationIntent(nodeId: string): void {
  if (!nodeId || !intentNodes.delete(nodeId)) return;
  pumpGlobalQueue();
}

function markGenerationStarted(nodeId?: string): void {
  if (!nodeId) return;
  queuedNodes.delete(nodeId);
  inFlightNodes.set(nodeId, (inFlightNodes.get(nodeId) ?? 0) + 1);
}

function markGenerationFinished(nodeId?: string): void {
  if (!nodeId) return;
  const remaining = (inFlightNodes.get(nodeId) ?? 0) - 1;
  if (remaining > 0) inFlightNodes.set(nodeId, remaining);
  else inFlightNodes.delete(nodeId);
}

/** 撤掉某节点的「排队中」标记（只有它自己没有任何在跑的槽时才撤）。 */
function releaseQueuedNode(nodeId?: string): void {
  if (!nodeId) return;
  if (globalWaiters.some((waiter) => waiter.nodeId === nodeId)) return;
  if ((inFlightNodes.get(nodeId) ?? 0) > 0) return;
  queuedNodes.delete(nodeId);
}

function readGate(gate?: () => GenerationGate): GenerationGate {
  if (!gate) return "go";
  try {
    return gate() ?? "go";
  } catch (error) {
    // 闸门读的是画布实时状态；读挂了按「放行」处理，宁可并发也不能把队列卡死。
    console.warn("[generation-queue] gate probe failed", error);
    return "go";
  }
}

/** 这个等待者现在是不是还被上游挡着（顺便打上「从什么时候开始被挡」的时间戳）。 */
function waiterGateBlocked(waiter: GlobalWaiter, now: number): boolean {
  const state = readGate(waiter.gate);
  if (state === "go") {
    waiter.blockedSince = null;
    return false;
  }
  if (waiter.blockedSince === null) waiter.blockedSince = now;
  const limit = state === "running"
    ? RUNNING_UPSTREAM_WAIT_LIMIT_MS
    : QUEUED_UPSTREAM_WAIT_LIMIT_MS;
  if (now - waiter.blockedSince < limit) return true;
  console.warn(
    `[generation-queue] 上游等待超过 ${Math.round(limit / 1000)}s，放行被挡住的提交`,
    waiter.nodeId ?? "",
  );
  return false;
}

function detachWaiterAbort(waiter: GlobalWaiter): void {
  if (waiter.signal && waiter.onAbort) {
    waiter.signal.removeEventListener("abort", waiter.onAbort);
  }
}

/** 找下一个能开跑的等待者：被上游挡住的一律跳过（不占坑），队伍后面的照常派位。 */
function nextEligibleWaiterIndex(now: number): number {
  for (let index = 0; index < globalWaiters.length; index += 1) {
    const waiter = globalWaiters[index];
    if (waiter.signal?.aborted) {
      globalWaiters.splice(index, 1);
      detachWaiterAbort(waiter);
      releaseQueuedNode(waiter.nodeId);
      waiter.reject(generationAbortReason(waiter.signal));
      index -= 1;
      continue;
    }
    if (!waiterGateBlocked(waiter, now)) return index;
  }
  return -1;
}

function scheduleBlockedRepoll(): void {
  if (repollTimer !== null || globalWaiters.length === 0) return;
  repollTimer = setTimeout(() => {
    repollTimer = null;
    pumpGlobalQueue();
  }, BLOCKED_REPOLL_MS);
  // Node / Vitest 环境里别让这个定时器拖住进程退出。
  (repollTimer as { unref?: () => void }).unref?.();
}

function pumpGlobalQueue(): void {
  const now = Date.now();
  while (globalActive < GENERATION_CONCURRENCY_DEFAULT) {
    const index = nextEligibleWaiterIndex(now);
    if (index < 0) break;
    const waiter = globalWaiters.splice(index, 1)[0];
    detachWaiterAbort(waiter);
    // 先加计数再 resolve：两者在同一个同步块里完成，不会出现「瞬间多放一个付费提交」的窗口。
    markGenerationStarted(waiter.nodeId);
    globalActive += 1;
    waiter.resolve();
  }
  scheduleBlockedRepoll();
}

async function acquireGlobalSlot(
  signal: AbortSignal | undefined,
  options: GenerationSlotOptions,
): Promise<void> {
  if (signal?.aborted) throw generationAbortReason(signal);
  // 队里没人时的常见情况：有空槽且闸门放行 → 当场开跑，省掉一次入队往返。
  // 队里有人在等就必须排队（先到先得），否则被上游挡住的等待者会被后来者无限插队。
  if (
    globalWaiters.length === 0
    && globalActive < GENERATION_CONCURRENCY_DEFAULT
    && readGate(options.gate) === "go"
  ) {
    markGenerationStarted(options.nodeId);
    globalActive += 1;
    return;
  }
  await new Promise<void>((resolve, reject) => {
    const waiter: GlobalWaiter = {
      resolve,
      reject,
      signal,
      nodeId: options.nodeId,
      gate: options.gate,
      blockedSince: null,
    };
    if (signal) {
      waiter.onAbort = () => {
        const index = globalWaiters.indexOf(waiter);
        if (index >= 0) globalWaiters.splice(index, 1);
        releaseQueuedNode(waiter.nodeId);
        reject(generationAbortReason(signal));
      };
      signal.addEventListener("abort", waiter.onAbort, { once: true });
    }
    globalWaiters.push(waiter);
    if (options.nodeId) queuedNodes.add(options.nodeId);
    // 队里可能正好有空槽（前面的人被上游挡着），入队后立刻问一次派位。
    pumpGlobalQueue();
  });
}

function releaseGlobalSlot(nodeId?: string): void {
  markGenerationFinished(nodeId);
  globalActive = Math.max(0, globalActive - 1);
  pumpGlobalQueue();
}

/** One semaphore shared by every image/video node in this browser tab. */
export async function withGlobalGenerationSlot<T>(
  task: () => Promise<T>,
  signal?: AbortSignal,
  options: GenerationSlotOptions = {},
): Promise<T> {
  await acquireGlobalSlot(signal, options);
  try {
    if (signal?.aborted) throw generationAbortReason(signal);
    return await task();
  } finally {
    releaseGlobalSlot(options.nodeId);
  }
}

/**
 * 批量张数选择器上「要不要排队」的提示语：只有超过并发度的那部分才排队。用常数推导，
 * 避免以后再出现写死的「每批 3 张」。
 */
export function generationQueueUnitHint(option: number, unit: string): string {
  if (option <= GENERATION_CONCURRENCY_DEFAULT) return '';
  return GENERATION_CONCURRENCY_DEFAULT > 1
    ? `（每批${GENERATION_CONCURRENCY_DEFAULT}${unit}）`
    : '（排队，逐个生成）';
}

export function globalGenerationQueueSnapshot(): {
  active: number;
  queued: number;
  blocked: number;
  nodes: string[];
} {
  return {
    active: globalActive,
    queued: globalWaiters.length,
    blocked: globalWaiters.filter((waiter) => waiter.blockedSince !== null).length,
    nodes: [...inFlightNodes.keys()],
  };
}

export function clampGenerationBatchCount(value: number): number {
  if (!Number.isFinite(value)) return 1;
  return Math.min(GENERATION_BATCH_MAX, Math.max(1, Math.floor(value)));
}

/**
 * Runs at most `concurrency` paid submissions at once while accepting a larger
 * user batch. Every item is attempted exactly once; results preserve input
 * order and failures do not prevent queued items from starting.
 */
export async function runGenerationQueue<T>(
  items: readonly T[],
  worker: (item: T, index: number) => Promise<void>,
  concurrency = GENERATION_CONCURRENCY_DEFAULT,
  onSettled?: (completed: number, total: number) => void,
  signal?: AbortSignal,
): Promise<PromiseSettledResult<void>[]> {
  const total = Math.min(items.length, GENERATION_BATCH_MAX);
  if (total === 0) return [];
  const limit = Math.min(total, Math.max(1, Math.floor(concurrency)));
  const results: Array<PromiseSettledResult<void> | undefined> = Array(total);
  let nextIndex = 0;
  let completed = 0;

  const consume = async () => {
    while (true) {
      if (signal?.aborted) return;
      const index = nextIndex;
      nextIndex += 1;
      if (index >= total) return;
      try {
        await worker(items[index], index);
        results[index] = { status: "fulfilled", value: undefined };
      } catch (reason) {
        results[index] = { status: "rejected", reason };
      } finally {
        completed += 1;
        onSettled?.(completed, total);
      }
    }
  };

  await Promise.all(Array.from({ length: limit }, () => consume()));
  if (signal?.aborted) {
    const reason = generationAbortReason(signal);
    for (let index = 0; index < total; index += 1) {
      if (results[index]) continue;
      results[index] = { status: "rejected", reason };
      completed += 1;
      onSettled?.(completed, total);
    }
  }
  return results as PromiseSettledResult<void>[];
}
