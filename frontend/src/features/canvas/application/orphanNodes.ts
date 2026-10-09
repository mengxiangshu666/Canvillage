// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * 「孤儿节点」= 没有任何连线（既非 source 亦非 target）的节点。
 *
 * 背景：LibTV 语料实测 19.3% 的节点是孤儿（用户反复改图后留下的废弃节点）。
 * 大画布上这类节点肉眼找不到，但会拖慢渲染、污染 minimap、误导后来者。
 * 规格见 `libtv爬取/DISTILL/analysis/B3_CONVERGENCE.md` G8；
 * 差集记录见 `libtv爬取/VILLAGE_GAP_ANALYSIS.md`。
 *
 * 判定口径：
 * - 只看 graph 连通性（edges 的 source/target 集合），不看节点类型能力。
 * - 容器节点（分组）本身不参与连线属正常形态，不计入孤儿。
 */

/** React Flow 节点 type → 中文展示名（与 domain/nodeDisplay 的口径一致）。 */
export const RF_TYPE_LABELS: Readonly<Record<string, string>> = {
  uploadNode: '上传资源',
  imageNode: 'AI 图片',
  imageGenNode: '图片节点',
  exportImageNode: '结果图片',
  beatContextNode: '镜头上下文',
  textAnnotationNode: '文本',
  groupNode: '分组',
  storyboardNode: '分格抽取结果',
  storyboardGenNode: '多版本宫格',
  videoNode: '视频',
  audioNode: '音频',
  videoStoryNode: '视频故事',
  videoComposeNode: '视频合成',
  scriptNode: '脚本生成器',
  pano360ViewerNode: '360° 全景查看器',
  threeDWorldNode: '3D 世界',
  skillNode: '技能',
  styleNode: '风格',
};

/** 容器节点：不参与连线属正常，排除在孤儿之外。 */
const CONTAINER_TYPES: ReadonlySet<string> = new Set(['groupNode']);

export interface OrphanNodeSummary {
  id: string;
  /** React Flow type（如 imageGenNode）。 */
  type: string;
  /** 中文类型名。 */
  typeLabel: string;
  /** 用户可读摘要（优先 displayName，其次 prompt / 文本）。 */
  summary: string;
  /** 节点位置（用于点击定位）。 */
  position: { x: number; y: number };
}

interface MinimalNode {
  id: string;
  type?: string;
  position: { x: number; y: number };
  data?: Record<string, unknown>;
}

interface MinimalEdge {
  source: string;
  target: string;
}

const SUMMARY_KEYS = [
  'displayName',
  'name',
  'title',
  'label',
  'prompt',
  'text',
] as const;

function resolveSummary(node: MinimalNode): string {
  const data = node.data ?? {};
  for (const key of SUMMARY_KEYS) {
    const value = data[key];
    if (typeof value === 'string' && value.trim()) {
      return value.trim().replace(/\s+/g, ' ').slice(0, 48);
    }
  }
  return '';
}

/**
 * 找出所有孤儿节点。
 * @param nodes 画布节点
 * @param edges 画布连线
 */
export function findOrphanNodes(
  nodes: readonly MinimalNode[],
  edges: readonly MinimalEdge[],
): OrphanNodeSummary[] {
  const connected = new Set<string>();
  for (const edge of edges) {
    connected.add(edge.source);
    connected.add(edge.target);
  }
  const out: OrphanNodeSummary[] = [];
  for (const node of nodes) {
    if (connected.has(node.id)) continue;
    const type = node.type ?? 'unknown';
    if (CONTAINER_TYPES.has(type)) continue;
    out.push({
      id: node.id,
      type,
      typeLabel: RF_TYPE_LABELS[type] ?? type,
      summary: resolveSummary(node),
      position: node.position,
    });
  }
  return out;
}
