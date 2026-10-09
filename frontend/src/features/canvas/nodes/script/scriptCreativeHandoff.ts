// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { FreezoneStoryScriptRow } from '@/api/ops';
import type { FreezoneStoryDirectorPlan } from '@/api/scriptContract';
import type { VideoCreativeHandoff } from '@/features/canvas/domain/canvasNodes';
import type { ScriptShotRefEntry } from './scriptShotRefs';
import { parsePromptSegment, splitPromptSegmentChunks } from '@/features/canvas/domain/promptSegments';
import { scriptKeyframePlan } from './scriptKeyframePlan';
import { cellText, collectScriptScenes, isScriptNoValue, rowImagePrompt } from './scriptViews';

export interface ScriptReferenceResponsibility {
  responsibility: string;
  prohibited: string;
}

export type ScriptDirectorContext = NonNullable<VideoCreativeHandoff['directorContext']>;

const textValue = (value: unknown): string | undefined => {
  if (typeof value !== 'string') return undefined;
  const valueText = value.trim();
  return valueText && !isScriptNoValue(valueText) ? valueText : undefined;
};

function directorSequence(sequence: NonNullable<FreezoneStoryDirectorPlan['sequences']>[number]): ScriptDirectorContext['sequences'][number] | null {
  const sequenceId = textValue(sequence.sequence_id);
  const shotNos = Array.isArray(sequence.shot_nos)
    ? [...new Set(sequence.shot_nos.filter((value): value is number => Number.isInteger(value) && value > 0))]
    : [];
  if (!sequenceId || shotNos.length === 0) return null;
  const result: ScriptDirectorContext['sequences'][number] = { sequenceId, shotNos };
  for (const [target, source] of [
    ['title', 'title'], ['dramaticGoal', 'dramatic_goal'], ['resistance', 'resistance'],
    ['escalation', 'escalation'], ['turn', 'turn'], ['release', 'release'],
    ['stagingPlan', 'staging_plan'], ['performancePlan', 'performance_plan'],
  ] as const) {
    const value = textValue(sequence[source]);
    if (value) result[target] = value;
  }
  return result;
}

/** Keep the authored plan structured, selecting sequences by actual shot membership. */
export function buildScriptDirectorContext(
  plan: FreezoneStoryDirectorPlan | null | undefined,
  shotNumbers: readonly (string | number)[] = [],
): ScriptDirectorContext | undefined {
  if (!plan) return undefined;
  const wanted = new Set(shotNumbers.map(Number).filter((value) => Number.isInteger(value) && value > 0));
  const sequences = (plan.sequences ?? [])
    .map(directorSequence)
    .filter((sequence): sequence is ScriptDirectorContext['sequences'][number] =>
      sequence !== null && sequence.shotNos.some((shot) => wanted.has(shot)),
    );
  const context: ScriptDirectorContext = { sequences };
  for (const [target, source] of [
    ['storyPromise', 'story_promise'], ['protagonistGoal', 'protagonist_goal'],
    ['coreConflict', 'core_conflict'], ['endingChange', 'ending_change'],
    ['rhythmCurve', 'rhythm_curve'], ['soundPlan', 'sound_plan'],
  ] as const) {
    const value = textValue(plan[source]);
    if (value) context[target] = value;
  }
  if (plan.visual_bible) {
    const visualBible: NonNullable<ScriptDirectorContext['visualBible']> = {};
    for (const [target, source] of [
      ['visualStyle', 'visual_style'], ['texture', 'texture'], ['colorProgression', 'color_progression'],
      ['lighting', 'lighting'], ['cameraLanguage', 'camera_language'],
    ] as const) {
      const value = textValue(plan.visual_bible[source]);
      if (value) visualBible[target] = value;
    }
    if (Object.keys(visualBible).length) context.visualBible = visualBible;
  }
  return sequences.length || Object.keys(context).length > 1 ? context : undefined;
}

/** Stable design only; whole-film lighting can include changes belonging to later shots. */
export function scriptDirectorVisualContext(handoff: VideoCreativeHandoff | undefined): string {
  const visual = handoff?.directorContext?.visualBible;
  if (!visual) return '';
  const context = [
    visual.visualStyle ? `全片视觉风格：${visual.visualStyle}` : '',
    visual.texture ? `材质质感：${visual.texture}` : '',
  ].filter(Boolean).join('；');
  return context ? `共同美术基准（只补充本镜未明确的设计，本镜风格、光线、动作和目标状态优先，不展开全片变化）：${context}` : '';
}

/** Carry authored light/material design into video without repeating the opening pose. */
export function scriptShotVisualContext(row: FreezoneStoryScriptRow): string {
  const visual = splitPromptSegmentChunks(rowImagePrompt(row)).map(parsePromptSegment)
    .filter(segment => /^(光影|视觉风格|质感)/.test(segment.label) && !isScriptNoValue(segment.body));
  const lines = visual.map(segment => `${segment.label}：${segment.body}`);
  const mood = textValue(row.lighting_mood);
  if (mood && !visual.some(segment => segment.body === mood)) lines.push(`本镜光影氛围：${mood}`);
  return lines.length
    ? `本镜已定美术（光色与质感按以下设计；运动稿中有因果的光线变化继续发生，不重置参考中的身份、空间或姿态）：\n${lines.join('\n')}`
    : '';
}

/** Only authored fixed geography; sequence staging can include future shots. */
export function scriptSceneSpatialContext(handoff: VideoCreativeHandoff | undefined): string {
  const scenes = Object.entries(handoff?.sceneDescriptions ?? {})
    .map(([name, description]) => `${name}：${description}`).join('\n');
  return scenes ? `本镜固定空间基准（地标、出入口、尺度与布局）：\n${scenes}\n地理方向以固定地标为准，不把屏幕左右当永久地理方向，不镜像布局或凭空增加通路。人物位置、路线、视线、接触与支撑按本镜动作和当前目标状态落实，固定场景不要求恢复首帧姿态；只呈现本镜取景和观看目的需要的关系，保留计划中的遮挡与揭示时点，不强行展示全部地标，不在画面绘制拓扑标签或路线箭头。` : '';
}

/** One vocabulary for what each numbered reference may and may not decide. */
export function scriptReferenceResponsibility(
  role: NonNullable<VideoCreativeHandoff['referenceResponsibilities']>[number]['role'],
): ScriptReferenceResponsibility {
  switch (role) {
    case 'scene':
      return {
        responsibility: '锁定地标、出入口、尺度与可通行区域；拓扑图的通路、人物路线和摄影机轨迹按各自标签及图例识别；本镜动作与运镜以剧本为准',
        prohibited: '不把通路箭头误当运镜，不在成片画出标注',
      };
    case 'prop':
      return {
        responsibility: '锁定结构、材质与比例；持有者、位置、接触和损坏变化按本镜动作发展',
        prohibited: '不因参考图复原已经发生的状态变化',
      };
    case 'character':
      return {
        responsibility: '锁定人物身份、五官、体型和设计；姿态、表情与当前服装装备按本镜状态和表演发展',
        prohibited: '不复制多视图拼版、标注或文字，不让参考图覆盖本镜状态',
      };
    case 'opening_frame':
      return {
        responsibility: '是本镜起始画面参考，用于开场构图、站位与动作状态，随后按本镜动作自然发展',
        prohibited: '不要求全程固定开场姿态',
      };
    case 'frame_design':
      return {
        responsibility: '提供本镜首图的身份、空间、道具、光线和设计基准，姿态、间距与接触按本张目标状态改变',
        prohibited: '不复制首帧姿态，不恢复已改变的接触或装备',
      };
    case 'state_frame':
      return {
        responsibility: '只指导这一阶段的姿态、接触或空间关系',
        prohibited: '不覆盖开场，不做静帧拼贴或凭空跳变；状态间的动作、镜头变化及停顿按剧本执行，不从参考图自动推导硬切或保持时长',
      };
    case 'end_frame':
      return {
        responsibility: '是本镜结束画面，连续动作发展到此状态',
        prohibited: '不作为开场，不倒放或提前跳到结果',
      };
    case 'continuity_frame':
      return {
        responsibility: '是上一镜实际尾帧，承接已发生的姿态、位置和运动方向',
        prohibited: '不重演上一镜动作',
      };
    case 'motion':
      return {
        responsibility: '提供姿态、动作或摄影机运动参考，具体执行按本镜剧本发展',
        prohibited: '不替换人物身份、资产设计或本镜动作结果',
      };
    case 'style':
      return {
        responsibility: '提供画面质感、色彩和光线参考',
        prohibited: '不替换人物身份、空间布局或本镜动作',
      };
    default:
      return {
        responsibility: '仅提供本镜已有的构图、环境和光线参考',
        prohibited: '不承担角色身份、资产设计或下一镜动作',
      };
  }
}

/** Compile one bounded director handoff reused by storyboard, keyframes and video. */
export function buildScriptCreativeHandoff(
  row: FreezoneStoryScriptRow,
  references: readonly ScriptShotRefEntry[] = [],
  directorPlan?: FreezoneStoryDirectorPlan | null,
  shotNumber?: string | number,
  sharedSceneDescriptions: Record<string, string> = {},
): VideoCreativeHandoff {
  const text = (key: string): string => {
    const value = cellText(row, key);
    return isScriptNoValue(value) ? '' : value;
  };
  const sequenceIds = Array.isArray(row.sequence_ids)
    ? row.sequence_ids
        .filter((value): value is string => typeof value === 'string' && !isScriptNoValue(value))
        .map(value => value.trim())
    : [];
  const handoff: VideoCreativeHandoff = {};
  const sceneDescriptions = Object.fromEntries(collectScriptScenes([row])
    .map((scene) => [scene.name, isScriptNoValue(scene.description) ? sharedSceneDescriptions[scene.name] : scene.description])
    .filter((entry): entry is [string, string] => typeof entry[1] === 'string' && !isScriptNoValue(entry[1])));
  if (Object.keys(sceneDescriptions).length) handoff.sceneDescriptions = sceneDescriptions;
  for (const [field, key] of [
    ['shotPurpose', 'shot_purpose'], ['cutReason', 'cut_reason'],
    ['filmLanguage', 'film_language'], ['contentIntent', 'content_intent'],
  ] as const) {
    const value = text(key);
    if (value) handoff[field] = value;
  }
  if (sequenceIds.length) handoff.sequenceIds = [...new Set(sequenceIds)];
  const directorContext = buildScriptDirectorContext(directorPlan, [row.shot_no ?? shotNumber ?? '']);
  if (directorContext) handoff.directorContext = directorContext;
  if (directorPlan) {
    const matchedIds = directorContext?.sequences.map((sequence) => sequence.sequenceId) ?? [];
    if (matchedIds.length) handoff.sequenceIds = [...new Set(matchedIds)];
    else delete handoff.sequenceIds;
  }
  const keyframePlan = scriptKeyframePlan(row);
  if (keyframePlan.length) handoff.keyframePlan = keyframePlan;
  if (references.length) {
    handoff.referenceResponsibilities = references.map((reference, index) => {
      const role = reference.assetId ? reference.role : 'reference';
      const wording = scriptReferenceResponsibility(role);
      return {
        scope: 'storyboard',
        imageNumber: index + 1,
        role,
        name: reference.name,
        ...wording,
      };
    });
  }
  return handoff;
}
