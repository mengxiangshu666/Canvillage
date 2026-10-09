import type { FreezoneStoryScriptRow } from '@/api/scriptContract';
import type { FreezoneStoryDirectorPlan } from '@/api/scriptContract';
import { readScriptShotId } from '@/features/canvas/domain/scriptShotIdentity';
import type { ScriptAssetLedger } from './scriptAssets';
import { storyboardImageNodesForScript, type ScriptStoryboardGraph } from './scriptStoryboardMembers';
import { storyboardMemberImageUrl } from './generateScriptStoryboard';
import { buildScriptRowSnapshots, computeStoryboardStaleness, storyboardMemberSnapshot } from './scriptStaleness';
import { buildScriptRowKeys, rowReferenceImageUrl } from './scriptViews';

export interface ScriptPreviewFrame {
  url: string | null;
  label: string;
}

/** A portrait or generation input is never a rendered shot. */
export function scriptPreviewFrames(scriptNodeId: string, rows: FreezoneStoryScriptRow[], graph: ScriptStoryboardGraph, ledger?: ScriptAssetLedger): ScriptPreviewFrame[] {
  const members = storyboardImageNodesForScript(scriptNodeId, graph);
  const snapshots = members.map(storyboardMemberSnapshot).filter((value): value is NonNullable<typeof value> => value != null);
  const directorPlan = (graph.nodes.find((node) => node.id === scriptNodeId)?.data?.scriptResult as { director_plan?: FreezoneStoryDirectorPlan | null } | undefined)?.director_plan;
  const stale = computeStoryboardStaleness({ members: snapshots, rows: buildScriptRowSnapshots(rows, ledger, directorPlan) }).staleNodeIdsSet;
  return buildScriptRowKeys(rows).map((key, index) => {
    const matches = members.filter(node => readScriptShotId(node.data) === key);
    if (!matches.length) return { url: rowReferenceImageUrl(rows[index]), label: '脚本参考帧 · 尚无生成分镜' };
    const ready = matches.filter(node => !stale.has(node.id) && !node.data.generationError && !node.data.isGenerating && !node.data.canvas_auto_generate_once && storyboardMemberImageUrl(node));
    if (ready.length === 1) return { url: storyboardMemberImageUrl(ready[0]), label: '已生成分镜 · 运动与声音未验' };
    if (ready.length > 1) return { url: null, label: '这一镜有多个分镜结果，请先确认使用哪张' };
    return { url: null, label: stale.size && matches.some(node => stale.has(node.id)) ? '分镜已过期，请更新这一镜' : matches.some(node => node.data.generationError) ? '分镜生成失败' : '分镜尚未生成完成' };
  });
}
