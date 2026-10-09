// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * 节点能力目录 → 真实执行通道的分发器。
 *
 * 目录里每个能力都带一个 `execution` 描述子，但在此之前只有 `powerTool` 那一类能在
 * 「节点能力」菜单里点（菜单 onSelect 硬编码 `openPowerTool`），其余条目虽然都写了
 * 执行器、而且执行器在工具栏上都有真实按钮，却只能在菜单里看到一行文字。本模块把
 * 描述子变成唯一一份可执行的分发，让「目录里声明了执行器」和「真的有入口」不再各写
 * 一遍、各自漂移。
 *
 * 通道分类：
 *  - `canvas_event` → 挂到 `canvasEventBus`，由持有该能力的节点订阅并执行（节点才有
 *    `handleCaptureFrame` 这类闭包）。
 *  - `async_task` → 交给调用方注入的任务执行器（`taskHandlers`）。任务提交必须在持有
 *    节点状态的那个组件里做：模型选择、下游节点落位、在途守卫都是闭包里的状态，搬运
 *    出来就会和节点上的按钮走成两条不同的路。
 *  - `browser_ui` → 纯浏览器动作（下载 / 打开查看器），不写画布数据，本模块内完成。
 *  - `node_action` → 由节点主体的主 CTA（提交按钮）执行，不是菜单项。
 *  - `unwired` / `catalog_only` → 没有通道，拒绝并说明原因；永不出现在菜单里。
 */
import {
  NODE_TOOL_TYPES,
  type CanvasNode,
  type NodeToolType,
} from '@/features/canvas/domain/canvasNodes';
import type { NodeCapability } from '@/features/canvas/domain/nodeCapabilityCatalog';
import { resolveMediaDownloadFilename } from '@/features/canvas/domain/mediaDownloadFilename';
import { canvasEventBus } from './canvasServices';
import { downloadUrlAsFile } from '@/lib/browserDownload';
import { resolveImageDisplayUrl } from './imageData';

/** 分发回执：调用方据此判断是「已投递」还是「此刻不可执行」，禁止静默失败。 */
export interface NodeCapabilityDispatchReceipt {
  ok: boolean;
  capabilityId: string;
  channel: string;
  /** 不可执行时的原因，可直接展示给用户。 */
  reason?: string;
}

/**
 * 任务型能力的本地执行器。
 *
 * 每个调用上下文只提供它真的持有状态的执行器（工具栏提供它自己的三个），缺失即拒绝
 * —— 不回落、不猜。`reject(reason)` 让执行器把「没视频」「没工程 id」这类前置条件
 * 失败也变成一句可展示的话，而不是静默 return。
 */
export interface NodeCapabilityTaskRun {
  reject: (reason: string) => void;
}

export type NodeCapabilityTaskHandler = (
  run: NodeCapabilityTaskRun,
) => void | Promise<void>;

export type NodeCapabilityTaskHandlers = Record<
  string,
  NodeCapabilityTaskHandler | undefined
>;

export interface NodeCapabilityRunOptions {
  taskHandlers?: NodeCapabilityTaskHandlers;
}

function nonEmptyString(value: unknown): string | null {
  return typeof value === 'string' && value.trim().length > 0 ? value.trim() : null;
}

function nodeVideoUrl(node: CanvasNode): string | null {
  const data = node.data as Record<string, unknown>;
  return (
    nonEmptyString(data.videoUrl) ??
    nonEmptyString(data.resultVideoUrl) ??
    nonEmptyString(data.sourceVideoUrl)
  );
}

function nodeDisplayName(node: CanvasNode): string {
  return (
    nonEmptyString((node.data as Record<string, unknown>).displayName) ??
    `video-${node.id}`
  );
}

function reject(
  capability: NodeCapability,
  channel: string,
  reason: string,
): NodeCapabilityDispatchReceipt {
  return { ok: false, capabilityId: capability.id, channel, reason };
}

function accept(
  capability: NodeCapability,
  channel: string,
): NodeCapabilityDispatchReceipt {
  return { ok: true, capabilityId: capability.id, channel };
}

/** `tool-dialog/open` 的 `tool_type` 必须落在真实工具枚举里。 */
function resolveNodeToolType(value: unknown): NodeToolType | null {
  const raw = nonEmptyString(value);
  if (!raw) return null;
  return (Object.values(NODE_TOOL_TYPES) as string[]).includes(raw)
    ? (raw as NodeToolType)
    : null;
}

function runCanvasEvent(
  node: CanvasNode,
  capability: NodeCapability,
  event: string,
  params: Record<string, unknown>,
): NodeCapabilityDispatchReceipt {
  switch (event) {
    case 'tool-dialog/open': {
      const toolType = resolveNodeToolType(params.tool_type);
      if (!toolType) {
        return reject(capability, event, '该能力声明的工具类型不在画布工具枚举内');
      }
      canvasEventBus.publish('tool-dialog/open', { nodeId: node.id, toolType });
      return accept(capability, event);
    }
    case 'video-node/capture-frame': {
      const mode = params.mode;
      if (mode !== 'first' && mode !== 'last' && mode !== 'current') {
        return reject(capability, event, '该能力声明的抽帧位置无效');
      }
      canvasEventBus.publish('video-node/capture-frame', { nodeId: node.id, mode });
      return accept(capability, event);
    }
    case 'video-node/set-operation': {
      const operation = params.operation;
      if (
        operation !== 'clip' &&
        operation !== 'subtitle-smart' &&
        operation !== 'subtitle-box'
      ) {
        return reject(capability, event, '该能力声明的视频操作无效');
      }
      canvasEventBus.publish('video-node/set-operation', { nodeId: node.id, operation });
      return accept(capability, event);
    }
    case 'video-viewer/open': {
      const videoUrl = nodeVideoUrl(node);
      if (!videoUrl) {
        return reject(capability, event, '该节点还没有可查看的视频');
      }
      canvasEventBus.publish('video-viewer/open', {
        videoUrl,
        title: nodeDisplayName(node),
      });
      return accept(capability, event);
    }
    default:
      // 目录写了事件名但画布层没人订阅 —— 宁可拒绝，也不要假装点成功了。
      return reject(capability, event, `画布没有订阅 ${event} 的处理器`);
  }
}
async function runBrowserUi(
  node: CanvasNode,
  capability: NodeCapability,
  action: string,
): Promise<NodeCapabilityDispatchReceipt> {
  switch (action) {
    case 'video_download': {
      const videoUrl = nodeVideoUrl(node);
      if (!videoUrl) {
        return reject(capability, `browser_ui:${action}`, '该节点还没有可下载的视频');
      }
      const filename = resolveMediaDownloadFilename({
        sourceFileName: (node.data as Record<string, unknown>).sourceFileName,
        displayName: (node.data as Record<string, unknown>).displayName,
        fallback: `video-${node.id}`,
        extension: '.mp4',
      });
      try {
        await downloadUrlAsFile(resolveImageDisplayUrl(videoUrl), filename);
      } catch (error) {
        return reject(
          capability,
          `browser_ui:${action}`,
          error instanceof Error ? error.message : String(error),
        );
      }
      return accept(capability, `browser_ui:${action}`);
    }
    case 'video_fullscreen': {
      const videoUrl = nodeVideoUrl(node);
      if (!videoUrl) {
        return reject(capability, `browser_ui:${action}`, '该节点还没有可查看的视频');
      }
      canvasEventBus.publish('video-viewer/open', {
        videoUrl,
        title: nodeDisplayName(node),
      });
      return accept(capability, `browser_ui:${action}`);
    }
    default:
      return reject(capability, `browser_ui:${action}`, `未知的浏览器动作 ${action}`);
  }
}

/**
 * 执行一个节点能力。
 *
 * `powerhub` 描述子由调用方打开面板 —— 那是一次 store 写入 + 渲染浮层，不是可投递
 * 通道，所以这里对它返回 `ok: false` 并说明原因，调用方按 `powerTool` 分流。
 */
export async function runNodeCapability(
  node: CanvasNode,
  capability: NodeCapability,
  options: NodeCapabilityRunOptions = {},
): Promise<NodeCapabilityDispatchReceipt> {
  const execution = capability.execution;
  if (!execution) {
    return reject(
      capability,
      capability.powerTool ? 'powerhub' : 'unwired',
      capability.powerTool ? '该能力由 PowerHub 面板执行' : '该能力没有声明执行通道',
    );
  }
  switch (execution.kind) {
    case 'powerhub':
      return reject(capability, 'powerhub', '该能力由 PowerHub 面板执行');
    case 'canvas_event':
      return runCanvasEvent(node, capability, execution.event, execution.params ?? {});
    case 'async_task': {
      const channel = `async_task:${execution.taskType}`;
      const handler = options.taskHandlers?.[capability.id];
      if (!handler) {
        return reject(capability, channel, '当前上下文没有这个任务的执行者');
      }
      let refusal: string | null = null;
      const run: NodeCapabilityTaskRun = {
        reject: (reason) => {
          refusal = reason;
        },
      };
      try {
        await handler(run);
      } catch (error) {
        return reject(
          capability,
          channel,
          error instanceof Error ? error.message : String(error),
        );
      }
      if (refusal) return reject(capability, channel, refusal);
      return accept(capability, channel);
    }
    case 'node_action':
      return reject(
        capability,
        `node_action:${execution.action}`,
        '该能力由节点主体的提交按钮执行',
      );
    case 'browser_ui':
      return await runBrowserUi(node, capability, execution.action);
    case 'catalog_only':
    case 'unwired':
      return reject(capability, execution.kind, execution.reason);
    default:
      return reject(capability, 'unknown', '未知的执行通道');
  }
}
