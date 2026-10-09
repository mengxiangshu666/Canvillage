// SPDX-License-Identifier: Elastic-2.0
/**
 * 「有顺序的按顺序来」：出图 / 出视频的槽位闸门。
 *
 * 背景：全局信号量（`generationConcurrency.ts`）只保证同时在跑的付费提交不超过
 * `GENERATION_CONCURRENCY_DEFAULT` 个，并不知道谁该等谁。可是一批自动出图里，
 * 下游节点（拿上游出的图当参考图再出）如果跟上游同时提交，就会读到上游**还没出好**的
 * 空内容，白花一次钱。所以提交前先问一句「我的上游还在排队 / 正在出图吗」：
 *
 * - 上游已占着信号量的槽（正在出图）→ `running`，等它 release 再跑；
 * - 上游还挂着「挂载即出图」的排队标记（下一个 tick 就提交）→ `waiting`，短等；
 * - 都没有 → `go`，跟别的节点并发跑。
 *
 * 判据只认两件**实时**事实：信号量里的在跑 / 在队集合，以及节点上那几个自动出图标记
 * （{@link AUTO_GENERATION_FLAGS}）。刻意不看笼统的 `isGenerating`——页面被强杀过一次后，
 * 节点数据里那个标记会跟着画布一直持久化下来，拿它当依赖会凭空挡住下游十几分钟。
 */
import { useCanvasStore } from '@/stores/canvasStore';
import type { CanvasNode } from '../domain/canvasNodes';
import { isGenerationNodeBusy, type GenerationGate } from './generationConcurrency';

/**
 * 「挂载后自己会提交」的标记。图片节点（含脚本生成的整组分镜、智能分镜、表情矩阵）都用它，
 * 所以看到它就说明上游的提交马上要来了，下游等一小会儿是划算的。
 */
const AUTO_GENERATION_FLAGS = ['canvas_auto_generate_once', 'expression_auto_generate'] as const;

/**
 * 「自动出图」被上游挡住时的重查间隔。
 *
 * 正常路径不靠它：上游的图一落地就会改写上游节点数据 → 下游节点重渲染 → effect 带着
 * 新闭包重跑一次，闸门这时已经是 `go`。这个定时重查只兜「上游永远不出图」（比如它压根
 * 没挂载上）的情况，免得下游把自己的自动出图卡死。
 */
export const UPSTREAM_GATE_RETRY_MS = 30_000;

function isArmedForAutoGeneration(node: CanvasNode): boolean {
  const data = (node.data ?? {}) as Record<string, unknown>;
  return AUTO_GENERATION_FLAGS.some((flag) => data[flag] === true);
}

/**
 * 沿连线往上做传递闭包，收集 `nodeId` 的全部祖先节点 id（含环保护）。
 * 只认有向边，不看 React Flow 的 parentId（分组容器不是「上游」）。
 */
export function ancestorNodeIds(
  nodes: readonly CanvasNode[],
  edges: ReadonlyArray<{ source: string; target: string }>,
  nodeId: string,
): string[] {
  const known = new Set(nodes.map((node) => node.id));
  const parentsByChild = new Map<string, string[]>();
  for (const edge of edges) {
    // 指向已被删掉的节点的边不算上游——它不可能再产出任何东西。
    if (!known.has(edge.source)) continue;
    const parents = parentsByChild.get(edge.target);
    if (parents) parents.push(edge.source);
    else parentsByChild.set(edge.target, [edge.source]);
  }
  const seen = new Set<string>([nodeId]);
  const queue = [...(parentsByChild.get(nodeId) ?? [])];
  const ancestors: string[] = [];
  for (let cursor = 0; cursor < queue.length; cursor += 1) {
    const candidate = queue[cursor];
    if (seen.has(candidate)) continue;
    seen.add(candidate);
    ancestors.push(candidate);
    const parents = parentsByChild.get(candidate);
    if (parents) {
      for (const parent of parents) {
        if (!seen.has(parent)) queue.push(parent);
      }
    }
  }
  return ancestors;
}

/** 此刻能不能提交：见文件头的状态说明。 */
export function upstreamGenerationGate(nodeId: string): GenerationGate {
  const { nodes, edges } = useCanvasStore.getState();
  const ancestors = ancestorNodeIds(nodes, edges, nodeId);
  if (ancestors.length === 0) return 'go';
  const byId = new Map(nodes.map((node) => [node.id, node]));
  let armed = false;
  for (const ancestorId of ancestors) {
    // 已经进信号量（在跑 / 在队）的上游最要紧：它下一秒就可能产出新图，必须等。
    if (isGenerationNodeBusy(ancestorId)) return 'running';
    const ancestor = byId.get(ancestorId);
    if (ancestor && isArmedForAutoGeneration(ancestor)) armed = true;
  }
  return armed ? 'waiting' : 'go';
}

/** 上游（含传递）还有活没落地吗——给测试和调试用的布尔版。 */
export function upstreamGenerationPending(nodeId: string): boolean {
  return upstreamGenerationGate(nodeId) !== 'go';
}
