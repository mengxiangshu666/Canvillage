import type { FreezoneStoryScriptRow, ScriptContractIssue } from '@/api/scriptContract';
import { parsePromptSegment, splitPromptSegmentChunks } from '@/features/canvas/domain/promptSegments';
import { cellText, isScriptNoValue, rowImagePrompt, scriptCharacterStateText } from './scriptViews';

export interface ScriptKeyframePlanItem {
  role: string;
  generation_strategy?: 'independent' | 'state_edit';
  framing?: string;
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
      ...(raw?.generation_strategy ? { generation_strategy: raw.generation_strategy } : {}),
      ...(typeof raw?.framing === 'string' && !isScriptNoValue(raw.framing) ? { framing: raw.framing.trim() } : {}),
    };
    if (isScriptNoValue(item.state)) return;
    const stateKey = normalizedKey(item.state);
    if (!stateKey) return;
    const roleKey = normalizedKey(item.role);
    const purposeKey = normalizedKey(item.purpose);
    const framingKey = normalizedKey(item.framing ?? '');
    if (stateKey === startKey && !purposeKey && !framingKey && roleKey === 'action_state' && item.generation_strategy !== 'independent') {
      rejections.push({ index, reason: 'opening_state' });
      return;
    }
    const signature = JSON.stringify([roleKey, stateKey, purposeKey, framingKey, item.generation_strategy || 'state_edit']);
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

export function scriptKeyframeInputIssue(item: ScriptKeyframePlanItem): string | null {
  if (item.generation_strategy && !['independent', 'state_edit'].includes(item.generation_strategy)) return '关键画面的生成方式无效，请选择独立构图或动作改图';
  if (item.generation_strategy === 'independent' && isScriptNoValue(item.framing ?? '')) return '独立构图缺少景别、视点和取景关系，请补充后再出图';
  if (item.generation_strategy === 'independent' && isScriptNoValue(item.purpose)) return '独立构图缺少新增信息的说明，请补充本图用途后再出图';
  return null;
}

export function scriptKeyframePlanIssues(rows: readonly FreezoneStoryScriptRow[]): ScriptContractIssue[] {
  return rows.flatMap((row, rowIndex) => [...scriptKeyframePlanReport(row).rejections.map((rejection) => ({
    rule_id: 'script.keyframe.duplicate_plan.v1', severity: 'advisory' as const,
    message: rejection.reason === 'opening_state'
      ? `状态画面 ${rejection.index + 1} 与首帧文字状态相同且没有新增用途，已拒绝重复补图；真实画面差异未检查`
      : `状态画面 ${rejection.index + 1} 的状态、职责和用途与状态画面 ${(rejection.duplicateOf ?? 0) + 1} 相同，已拒绝重复补图；真实画面差异未检查`,
    row_index: rowIndex, shot_no: cellText(row, 'display_shot_no') || cellText(row, 'shot_no') || String(rowIndex + 1),
    field: 'keyframe_plan', fixed: false,
    detail: { keyframe_index: rejection.index, reason: rejection.reason,
      ...(rejection.duplicateOf !== undefined ? { duplicate_of: rejection.duplicateOf } : {}) },
  })), ...scriptKeyframePlan(row).flatMap((item, index) => {
    const message = scriptKeyframeInputIssue(item);
    return message ? [{ rule_id: 'script.keyframe.input.v1', severity: 'blocking' as const, message,
      row_index: rowIndex, shot_no: cellText(row, 'display_shot_no') || cellText(row, 'shot_no') || String(rowIndex + 1),
      field: 'keyframe_plan', fixed: false, detail: { keyframe_index: index } }] : [];
  })]);
}

export function scriptKeyframePlanPrompt(row: FreezoneStoryScriptRow): string {
  const plan = scriptKeyframePlan(row);
  if (plan.length === 0) return '';
  return `本镜状态画面计划（仍是一个视频节点，按顺序作为参考，不把静帧当成硬切时间轴）：${plan
    .map((item, index) => `${index + 1}. ${item.role}：${item.state}${item.framing ? `；构图：${item.framing}` : ''}${item.purpose ? `（用途：${item.purpose}）` : ''}${item.required ? '【关键】' : ''}`)
    .join('；')}。状态之间必须用可见的因果动作、接触、重心或镜头运动连接。`;
}

export function scriptKeyframeStatePrompt(
  referenceInstructions: string,
  item: ScriptKeyframePlanItem,
  index: number,
  context = '',
): string {
  return [
    `关键画面 ${index + 1}：只画这一瞬间，不画拼贴或时间轴。`,
    `可见状态：${item.state}。`,
    item.framing ? `本张构图：${item.framing}。` : '',
    referenceInstructions,
    item.purpose ? `新增信息与用途：${item.purpose}。如用途与可见状态冲突，以可见状态为准，不恢复已释放的接触或已改变的装备。` : '',
    item.generation_strategy === 'independent'
      ? '按本张构图独立组织单幅画面，共同资产仅锁定身份、空间和设计；不沿用首图机位，不把资产多视图拼版画进结果。'
      : '先看@图片1中实际可见的姿态、接触与支撑，再落实本张目标。沿用身份、场景、道具与美术设计；姿态、人物间距和接触关系由目标状态决定，不复制首图动作或仅靠裁切伪装变化。',
    '将真实间隙、接触部位、受力与支撑画成可读的几何关系，不同时表现互斥阶段，不提前画后续结果，不添加目标以外的动作。左右手、肩、脚指角色自身解剖侧，画面左右指屏幕位置，两者不可互换。',
    context,
  ].filter(Boolean).join('\n');
}

/** Opening camera and pose come from the image; each extra view owns its framing. */
export function scriptKeyframeVisualContext(row: FreezoneStoryScriptRow): string {
  const visual = splitPromptSegmentChunks(rowImagePrompt(row))
    .filter(segment => /^(明确的场景|光影|视觉风格|技术参数)/.test(parsePromptSegment(segment).label));
  const costume = scriptCharacterStateText(row, 'start');
  return [
    visual.length ? '共同场景与美术基准（本张取景与可见状态优先）：' : '',
    ...visual,
    costume ? `服装装备基准：${costume}。仅当目标可见状态明确发生变化时改变相应部位，身份设计保持一致。` : '',
  ].filter(Boolean).join('\n');
}
