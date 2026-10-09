// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  CANVAS_STARTER_WORKFLOWS,
  getCanvasStarterWorkflow,
  type CanvasBuiltinStarterWorkflowId,
  type CanvasStarterWorkflowDefinition,
  type CanvasStarterWorkflowId,
} from './starterWorkflows';

export type CanvasStarterWorkflowCategory =
  | 'community'
  | 'story'
  | 'reference'
  | 'finish'
  | 'compose'
  | 'space'
  | 'product'
  | 'transition'
  | 'motion'
  | 'audio'
  | 'analysis';

export interface CanvasStarterWorkflowPresentation {
  category: CanvasStarterWorkflowCategory;
  inputSummary: string;
  modelHint: string;
  agentKeywords: readonly string[];
}

export const CANVAS_STARTER_WORKFLOW_CATEGORIES: readonly {
  id: CanvasStarterWorkflowCategory;
  label: string;
}[] = [
  { id: 'community', label: '社区高频组合' },
  { id: 'story', label: '短剧叙事' },
  { id: 'reference', label: '参考控制' },
  { id: 'product', label: '商业展示' },
  { id: 'transition', label: '转场衔接' },
  { id: 'motion', label: '动作重绘' },
  { id: 'audio', label: '声画音频' },
  { id: 'analysis', label: '解析再创作' },
  { id: 'finish', label: '画面定稿' },
  { id: 'compose', label: '剪辑成片' },
  { id: 'space', label: '空间取景' },
];

const PRESENTATION_BY_ID: Record<CanvasBuiltinStarterWorkflowId, CanvasStarterWorkflowPresentation> = {
  'story-continuity-film': {
    category: 'story', inputSummary: '剧情目标 + 可选角色/场景/道具正式资产', modelHint: '起步骨架；先完成镜头合同，再选择媒体模型',
    agentKeywords: ['短剧', '剧情', '连续', '角色', '剧本', '成片'],
  },
  'single-reference-video': {
    category: 'reference', inputSummary: '1 张主体或场景参考图', modelHint: '图生视频',
    agentKeywords: ['单图', '图生视频', '参考图', '首帧', '照片'],
  },
  'multi-reference-video': {
    category: 'reference', inputSummary: '人物 + 造型 + 动作参考', modelHint: '多参考视频',
    agentKeywords: ['多图', '多参考', '一致性', '人脸', '服装', '角色锁定'],
  },
  'refine-then-video': {
    category: 'finish', inputSummary: '原始参考图', modelHint: '先修图，再图生视频',
    agentKeywords: ['定稿', '修图', '光影', '质感', '精修'],
  },
  'storyboard-to-video': {
    category: 'story', inputSummary: '剧情、镜头目标或角色场景素材', modelHint: '先分镜，再逐镜生成',
    agentKeywords: ['分镜', '镜头', '九宫格', '镜头表', '镜头脚本'],
  },
  'video-composition': {
    category: 'compose', inputSummary: '至少 2 段视频，可附音频', modelHint: '本地时间线合成',
    agentKeywords: ['合成', '剪辑', '拼接', '时间线'],
  },
  'panorama-shot-planning': {
    category: 'space', inputSummary: '环境图、全景图或导演世界素材', modelHint: '先取景，再生成',
    agentKeywords: ['全景', '场景', '取景', '空间', '世界观'],
  },
  'product-showcase-video': {
    category: 'product', inputSummary: '1 张产品主图或商品场景图', modelHint: '图生视频 · 从当前直连视频模型中选择',
    agentKeywords: ['产品', '商品', '广告', '展示', '香水', '耳环', '电商'],
  },
  'first-last-frame-transition': {
    category: 'transition', inputSummary: '首帧图 + 尾帧图', modelHint: '首尾帧 · 从当前直连视频模型中选择',
    agentKeywords: ['首尾帧', '转场', '衔接', '前后', '变换', '切换'],
  },
  'motion-reference-redraw': {
    category: 'motion', inputSummary: '恰好 1 条源视频', modelHint: '源视频重绘 · 需选择支持视频编辑的直连模型',
    agentKeywords: ['动作复刻', '运镜复刻', '源视频', '重绘', '视频转绘', '动作模仿'],
  },
  'script-voice-video': {
    category: 'audio', inputSummary: '主题/剧情目标；可选角色与画面素材', modelHint: '脚本 + TTS + 视频合成',
    agentKeywords: ['配音', '旁白', '台词', '声画', '脚本配音', '解说', '口播'],
  },
  'music-driven-video': {
    category: 'audio', inputSummary: '音乐风格 + 画面 brief', modelHint: '音乐生成 + 视频节奏',
    agentKeywords: ['音乐', 'bgm', '节奏', '踩点', '配乐', '音乐驱动'],
  },
  'video-analysis-recut': {
    category: 'analysis', inputSummary: '1 条源视频', modelHint: '视频解析 + 脚本重构 + 重绘',
    agentKeywords: ['视频解析', '解析视频', '拆解视频', '复刻视频', '再创作', '二创', '重新剪辑'],
  },
  'image-to-3d-shot': {
    category: 'space', inputSummary: '1 张场景/空间参考图', modelHint: '3D 世界 + 空间取景',
    agentKeywords: ['3d', '3D', '空间', '取景', '图片转3d', '世界生成', '三维'],
  },
  'original-vertical-short-film': {
    category: 'story', inputSummary: '一句话创意 + 可选角色/场景/道具参考', modelHint: '原创竖屏短片 · 先脚本和分镜，再生成关键帧与视频',
    agentKeywords: ['原创', '竖屏短片', '短片', '一句话创意', '创意到视频', '原创视频'],
  },
  'product-advertisement': {
    category: 'product', inputSummary: '产品主图 + 产品卖点 + 可选品牌场景', modelHint: '产品广告 · 先广告脚本和分镜，再生成关键帧与视频',
    agentKeywords: ['产品广告', '产品宣传', '商业广告', '广告片', '卖点', '商品广告'],
  },
};

const COMMUNITY_PRESENTATION: CanvasStarterWorkflowPresentation = {
  category: 'community',
  inputSummary: '没有必须准备的素材；插入后按节点提示自行上传',
  modelHint: '社区高频结构 · 模型自选',
  // 关键词刻意留空：社区配方是按「节点怎么搭」聚出来的，不是按创作意图。给它编几个
  // 意图词会让 [[recommendCanvasStarterWorkflows]] 在「做一个香水广告」这类请求里
  // 推出一条叫「社区高频组合 · imageGenNode + videoNode」的卡片 —— 那张卡回答不了
  // 用户的问题，只会把真正对得上意图的内置路线挤出前三。
  agentKeywords: [],
};

const COMMUNITY_KEYWORDS: readonly { type: string; keyword: string }[] = [
  { type: 'audioNode', keyword: '配乐' },
  { type: 'textAnnotationNode', keyword: '文字' },
  { type: 'scriptNode', keyword: '脚本分镜' },
  { type: 'videoStoryNode', keyword: '分镜表' },
];

const COMMUNITY_NODE_LABEL: Record<string, string> = {
  imageGenNode: '图片',
  videoNode: '视频',
  audioNode: '音频',
  textAnnotationNode: '文字',
  scriptNode: '脚本',
  videoStoryNode: '分镜表',
};

function communityPresentation(
  workflow: CanvasStarterWorkflowDefinition,
): CanvasStarterWorkflowPresentation {
  const types = new Set(workflow.nodes.map((node) => String(node.type)));
  const inputs = workflow.nodes
    .map((node) => COMMUNITY_NODE_LABEL[String(node.type)] ?? String(node.type))
    .filter((label, index, all) => all.indexOf(label) === index);
  const keywords = COMMUNITY_KEYWORDS.filter((entry) => types.has(entry.type))
    .map((entry) => entry.keyword);
  return {
    ...COMMUNITY_PRESENTATION,
    inputSummary: `${inputs.join(' + ')}（${workflow.nodes.length} 个节点，接线已连好）`,
    agentKeywords: keywords,
  };
}

export function starterWorkflowPresentation(
  id: CanvasStarterWorkflowId,
): CanvasStarterWorkflowPresentation {
  const known = (PRESENTATION_BY_ID as Record<string, CanvasStarterWorkflowPresentation>)[id];
  if (known) {
    return known;
  }
  // 未知 id 只可能来自生成的社区配方（内置 17 条在上面那张表里，漏一条是编译错误）。
  // 真查不到时也不能抛 —— 起步器面板会在渲染循环里调它，抛一次整张面板白屏。
  const workflow = getCanvasStarterWorkflow(id);
  return workflow ? communityPresentation(workflow) : { ...COMMUNITY_PRESENTATION };
}

/**
 * 空画布首启卡片上直接给按钮的几条起步路线。
 *
 * 挑选口径是「意图各异 + 节点数少到能一眼看懂」：原创短片（从零开始）、
 * 分镜到视频（先分镜）、单参考图（最快出片）、多段合成（有素材之后）。
 * 剩下的工作流不是被藏起来 —— 卡片上的「浏览全部」就在同一个位置。
 *
 * 放在这里而不是写进组件，是为了让「按钮标题」只有一个来源：
 * 卡片用 `getCanvasStarterWorkflow(id)` 取标题，目录改了标题卡片跟着改。
 */
export const CANVAS_STARTER_QUICK_START_IDS: readonly CanvasStarterWorkflowId[] = [
  'original-vertical-short-film',
  'storyboard-to-video',
  'single-reference-video',
  'video-composition',
];

/** The quick-start entries that still resolve to a real workflow definition. */
export function starterQuickStartWorkflows(): readonly CanvasStarterWorkflowDefinition[] {
  return CANVAS_STARTER_QUICK_START_IDS.flatMap((id) => {
    const workflow = getCanvasStarterWorkflow(id);
    return workflow ? [workflow] : [];
  });
}

export function recommendCanvasStarterWorkflows(
  request: string,
  limit = 3,
): CanvasStarterWorkflowDefinition[] {
  const normalized = request.trim().toLowerCase();
  if (!normalized || limit <= 0) return [];
  return CANVAS_STARTER_WORKFLOWS
    .map((workflow, index) => ({
      workflow,
      index,
      score: starterWorkflowPresentation(workflow.id).agentKeywords.reduce(
        (score, keyword) => score + (normalized.includes(keyword.toLowerCase()) ? 1 : 0),
        0,
      ),
    }))
    .filter(({ score }) => score > 0)
    .sort((left, right) => right.score - left.score || left.index - right.index)
    .slice(0, limit)
    .map(({ workflow }) => workflow);
}

export function starterWorkflowAgentBrief(workflow: CanvasStarterWorkflowDefinition) {
  const presentation = starterWorkflowPresentation(workflow.id);
  return {
    id: workflow.id,
    title: workflow.title,
    description: workflow.description,
    required_inputs: presentation.inputSummary,
    model_hint: presentation.modelHint,
    template_kind: workflow.template_kind ?? 'atomic_capability',
    delivery_level: workflow.delivery_level ?? 'shot_draft',
    required_roles: workflow.required_roles ?? [],
    outputs: workflow.outputs ?? [],
    does_not_produce: workflow.does_not_produce ?? [],
    quality_gates: workflow.quality_gates ?? [],
  };
}
