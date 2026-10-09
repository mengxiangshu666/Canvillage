import type { FreezoneStoryScriptRow, ScriptContractIssue } from '@/api/scriptContract';
import { parsePromptSegment, splitPromptSegmentChunks } from '@/features/canvas/domain/promptSegments';
import { cellText, isScriptNoValue, rowImagePrompt, scriptCharacterStateText } from './scriptViews';

export interface ScriptKeyframePlanItem {
  role: string;
  state: string;
  purpose: string;
  required: boolean;
}

export interface ScriptKeyframePlanRejection {
  index: number;
  reason: 'opening_state' | 'duplicate_plan';
  duplicateOf?: number;
}

export interface ScriptKeyframePlanReport {
  plan: ScriptKeyframePlanItem[];
  rejections: ScriptKeyframePlanRejection[];
}

const normalizedKey = (value: string) => {
  const normalized = value.normalize('NFKC').trim().toLowerCase();
  return isScriptNoValue(normalized) ? '' : normalized.replace(/[\s,，。;；!?！？、]+/g, '').replace(/\.+$/, '');
};

/** 只接受模型/用户明确写出的可见状态，避免把任意对象串进提示词。 */
export function scriptKeyframePlanReport(row: FreezoneStoryScriptRow): ScriptKeyframePlanReport {
  const value = row.keyframe_plan;
  if (!Array.isArray(value)) return { plan: [], rejections: [] };
  // ponytail: text repeats only; real visual equivalence needs image review.
  const start = cellText(row, 'start_state');
  const startKey = isScriptNoValue(start) ? '' : normalizedKey(start);
  const seen = new Map<string, { index: number; item: ScriptKeyframePlanItem }>();
  const plan: ScriptKeyframePlanItem[] = [];
  const rejections: ScriptKeyframePlanRejection[] = [];
  value.forEach((raw, index) => {
    const item = {
      role: typeof raw?.role === 'string' ? raw.role.trim() : 'action_state',
      state: typeof raw?.state === 'string' ? raw.state.trim() : '',
      purpose: typeof raw?.purpose === 'string' ? raw.purpose.trim() : '',
      required: raw?.required === true,
    };
    if (isScriptNoValue(item.state)) return;
    const stateKey = normalizedKey(item.state);
    if (!stateKey) return;
    const roleKey = normalizedKey(item.role);
    const purposeKey = normalizedKey(item.purpose);
    if (stateKey === startKey && !purposeKey && roleKey === 'action_state') {
      rejections.push({ index, reason: 'opening_state' });
      return;
    }
    const signature = JSON.stringify([roleKey, stateKey, purposeKey]);
    const previous = seen.get(signature);
    if (previous) {
      if (item.required) previous.item.required = true;
      rejections.push({ index, reason: 'duplicate_plan', duplicateOf: previous.index });
      return;
    }
    seen.set(signature, { index, item });
    plan.push(item);
  });
  return { plan: plan.slice(0, 4), rejections };
}

export function scriptKeyframePlan(row: FreezoneStoryScriptRow): ScriptKeyframePlanItem[] {
  return scriptKeyframePlanReport(row).plan;
}

export function scriptKeyframePlanIssues(rows: readonly FreezoneStoryScriptRow[]): ScriptContractIssue[] {
  return rows.flatMap((row, rowIndex) => scriptKeyframePlanReport(row).rejections.map((rejection) => ({
    rule_id: 'script.keyframe.duplicate_plan.v1', severity: 'advisory' as const,
    message: rejection.reason === 'opening_state'
      ? `状态画面 ${rejection.index + 1} 与首帧文字状态相同且没有新增用途，已拒绝重复补图；真实画面差异未检查`
      : `状态画面 ${rejection.index + 1} 的状态、职责和用途与状态画面 ${(rejection.duplicateOf ?? 0) + 1} 相同，已拒绝重复补图；真实画面差异未检查`,
    row_index: rowIndex, shot_no: cellText(row, 'display_shot_no') || cellText(row, 'shot_no') || String(rowIndex + 1),
    field: 'keyframe_plan', fixed: false,
    detail: { keyframe_index: rejection.index, reason: rejection.reason,
      ...(rejection.duplicateOf !== undefined ? { duplicate_of: rejection.duplicateOf } : {}) },
  })));
}

export function scriptKeyframePlanPrompt(row: FreezoneStoryScriptRow): string {
  const plan = scriptKeyframePlan(row);
  if (plan.length === 0) return '';
  return `本镜状态画面计划（仍是一个视频节点，按顺序作为参考，不把静帧当成硬切时间轴）：${plan
    .map((item, index) => `${index + 1}. ${item.role}：${item.state}${item.purpose ? `（用途：${item.purpose}）` : ''}${item.required ? '【关键】' : ''}`)
    .join('；')}。状态之间必须用可见的因果动作、接触、重心或镜头运动连接。`;
}

export function scriptKeyframeStatePrompt(
  referenceInstructions: string,
  item: ScriptKeyframePlanItem,
  index: number,
  context = '',
): string {
  return [
    `状态关键画面 ${index + 1}：只画这一瞬间，不画拼贴或时间轴。`,
    `可见状态：${item.state}。`,
    referenceInstructions,
    item.purpose ? `画面用途（说明为什么需要这张图，不另设动作）：${item.purpose}。如用途与可见状态冲突，以可见状态为准，不恢复已释放的接触或已改变的装备。` : '',
    `先看@图片1中实际可见的姿态、接触与支撑，再落实上方目标状态。沿用角色身份、场景结构、道具设计、光线与美术风格；姿态、人物间距和接触关系由目标状态决定。它是本镜内部的${item.role}参考，不是新的视频镜头。`,
    '先落实目标状态里的接触是否存在、身体支撑、位置与装备变化，再保留共同设计；不要只调整表情、裁切或镜头远近来伪装动作阶段变化。',
    '只画目标这一阶段实际存在的接触、支撑与间距；把真实间隙、受力或脱离后的重心落实为清楚可见的几何关系，不同时表现互斥阶段，不添加目标以外的动作。',
    '左右手、肩、脚指角色自身解剖侧，画面左右指屏幕位置，两者不可互换。只表现目标状态已发生的变化，不提前画后续结果。',
    context,
    '成图核对：可见状态是本张唯一的动作目标；首帧和共同摄影基准帮助保持设计，不把人物恢复为首帧姿态。',
  ].filter(Boolean).join('\n');
}

/** Keep the shot's visual design, excluding the opening pose and expression. */
export function scriptKeyframeVisualContext(row: FreezoneStoryScriptRow): string {
  const visual = splitPromptSegmentChunks(rowImagePrompt(row))
    .filter(segment => /^(画面构图|明确的场景|光影|视觉风格|技术参数)/.test(parsePromptSegment(segment).label));
  const costume = scriptCharacterStateText(row, 'start');
  return [
    !isScriptNoValue(cellText(row, 'start_state'))
      ? `首帧状态对照（脚本文字，不代替@图片1里的实际姿态，也不是这张补图的目标）：${cellText(row, 'start_state')}。本图目标以上方“可见状态”为准。` : '',
    visual.length ? '以下是共同摄影与美术基准；其中起始人物位置、间距和遮挡随目标状态改变，不把构图描述当成固定姿态。' : '',
    ...visual,
    costume ? `服装装备基准：${costume}。仅当目标可见状态明确发生变化时改变相应部位，身份设计保持一致。` : '',
  ].filter(Boolean).join('\n');
}
