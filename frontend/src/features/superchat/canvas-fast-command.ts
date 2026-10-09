// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

import type { StructureCommandEnvelope, StructureOperation } from "./structure-proposal-store";

export interface CanvasFastCommandNode {
  id: string;
  type?: string | null;
  label?: string | null;
  position?: { x: number; y: number } | null;
}

export interface CanvasFastCommandContext {
  projectId: string;
  canvasId: string;
  selectedNodeId?: string | null;
  selectedNodeLabel?: string | null;
  selectedNodeType?: string | null;
  selectedNodePosition?: { x: number; y: number } | null;
  pinnedNodeIds?: readonly string[];
  nodes?: readonly CanvasFastCommandNode[];
}

export interface CanvasFastCommandPlan {
  reply: string;
  envelope: StructureCommandEnvelope;
}

const NODE_LABEL_MAX = 120;
const NODE_TEXT_MAX = 50_000;
const NODE_PROMPT_MAX = 50_000;
const QUOTE_PAIRS: ReadonlyArray<readonly [string, string]> = [
  ["“", "”"],
  ['"', '"'],
  ["‘", "’"],
  ["'", "'"],
];

/** The store id is the command source of truth; React Flow flags are a fallback. */
export function resolveLiveSelectedNodeId(
  nodes: ReadonlyArray<{ id: string; selected?: boolean }>,
  selectedNodeId?: string | null,
): string | null {
  const storeSelection = selectedNodeId?.trim();
  if (storeSelection && nodes.some((node) => node.id === storeSelection)) {
    return storeSelection;
  }
  const selected = nodes.find((node) => node.selected === true)?.id?.trim();
  if (selected) return selected;
  return null;
}

function compactText(value: string): string {
  return value.trim().replace(/[。！!？?]+$/u, "").trim();
}

function unquote(value: string): string {
  let result = value.trim();
  for (const [left, right] of QUOTE_PAIRS) {
    if (result.startsWith(left) && result.endsWith(right) && result.length >= left.length + right.length) {
      result = result.slice(left.length, -right.length).trim();
      break;
    }
  }
  return result;
}

function nextCommandId(): string {
  return `fast-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

function plan(
  context: CanvasFastCommandContext,
  reply: string,
  commands: StructureCommandEnvelope["commands"],
): CanvasFastCommandPlan {
  return {
    reply,
    envelope: {
      schema: "canvas_chat_commands.v1",
      project_id: context.projectId,
      canvas_id: context.canvasId,
      command_id: nextCommandId(),
      canvas_command_emitted: true,
      commands,
    },
  };
}

function escapedAlternation(values: readonly string[]): string {
  return values.map((value) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|");
}

function extractField(request: string, names: readonly string[]): string {
  const field = escapedAlternation(names);
  const prefix = `(?:${field})\\s*(?:(?:为|是|写成|写为|改为|改成|设为|设置为|叫做)\\s*)?[：:]?\\s*`;
  for (const [left, right] of QUOTE_PAIRS) {
    const pattern = new RegExp(`${prefix}${left.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}([^${right}]+)${right.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`, "iu");
    const quoted = request.match(pattern)?.[1]?.trim();
    if (quoted) return quoted;
  }
  const trailing = request.match(new RegExp(
    `${prefix}(.+?)(?=(?:[，,；;]\\s*|\\s{2,})(?:标题|名称|名字|提示词|prompt|正文|内容|文本|比例|宽高比|画幅|尺寸|分辨率|画质|质量|时长|模型|声音|音频)\\b|$)`,
    "iu",
  ))?.[1];
  return trailing ? unquote(trailing.replace(/[，,；;]+$/u, "")) : "";
}

function extractTitle(request: string, fallback: string): string {
  const title = extractField(request, ["标题", "名称", "名字"]);
  return title || fallback;
}

function extractPrompt(request: string): string {
  return extractField(request, ["提示词", "prompt", "描述", "画面"]);
}

function extractAspectRatio(request: string): string | undefined {
  const declared = request.match(
    /(?:比例|宽高比|画幅)\s*(?:为|是|设为|设置为)?\s*[：:]?\s*(auto|\d{1,2}\s*[:：]\s*\d{1,2})/iu,
  )?.[1];
  const standalone = request.match(/(?:^|[\s，,；;])(auto|1\s*[:：]\s*1|3\s*[:：]\s*4|4\s*[:：]\s*3|9\s*[:：]\s*16|16\s*[:：]\s*9|21\s*[:：]\s*9)(?=$|[\s，,；;])/iu)?.[1];
  const value = declared || standalone;
  return value?.replace(/\s+/gu, "").replace("：", ":").toLowerCase();
}

function extractModel(request: string): string | undefined {
  const model = extractField(request, ["模型", "model"]);
  return model && model.length <= 200 ? model : undefined;
}

function extractImageSize(request: string): string | undefined {
  const declared = request.match(/(?:尺寸|分辨率|画质|质量)\s*(?:为|是|设为|设置为)?\s*[：:]?\s*(1k|2k|4k)/iu)?.[1];
  return declared?.toUpperCase();
}

function extractVideoQuality(request: string): string | undefined {
  const declared = request.match(/(?:分辨率|画质|质量)\s*(?:为|是|设为|设置为)?\s*[：:]?\s*(480p|720p|768p|1080p|2k|4k)/iu)?.[1];
  const standalone = request.match(/(?:^|[\s，,；;])(480p|720p|768p|1080p|2k|4k)(?=$|[\s，,；;])/iu)?.[1];
  return (declared || standalone)?.toLowerCase();
}

function extractDuration(request: string): number | undefined {
  const value = request.match(/(?:时长\s*(?:为|是|设为|设置为)?\s*[：:]?\s*)?(\d+(?:\.\d+)?)\s*秒/iu)?.[1];
  if (!value) return undefined;
  const seconds = Number(value);
  return Number.isFinite(seconds) && seconds > 0 && seconds <= 3_600 ? seconds : undefined;
}

function extractGenerateAudio(request: string): boolean | undefined {
  if (/(?:关闭|不要|不需要|无需|去掉|禁用|静音|无)\s*(?:声音|音频)|(?:无声|静音版)/u.test(request)) return false;
  if (/(?:开启|打开|保留|需要|带上?|包含|生成|有)\s*(?:声音|音频)|(?:有声版)/u.test(request)) return true;
  return undefined;
}

function nodeById(context: CanvasFastCommandContext, nodeId: string | null | undefined): CanvasFastCommandNode | null {
  if (!nodeId) return null;
  return context.nodes?.find((node) => node.id === nodeId) ?? null;
}

function uniquePinnedNodeIds(context: CanvasFastCommandContext): string[] {
  const selected = context.selectedNodeId?.trim();
  return [...new Set((context.pinnedNodeIds ?? []).map((id) => id.trim()).filter(Boolean))]
    .filter((id) => id !== selected);
}

function connectedPair(context: CanvasFastCommandContext, request: string): { source: string; target: string } | null {
  const selected = context.selectedNodeId?.trim();
  const pinned = uniquePinnedNodeIds(context);
  if (selected && pinned.length === 1) {
    const referencedFirst = /(?:把|将)?(?:引用|固定|添加到agent|加入agent)(?:的)?节点.{0,12}(?:连接|连线|接到|连到).{0,12}(?:当前|这个|选中)(?:的)?节点/u.test(request);
    return referencedFirst
      ? { source: pinned[0], target: selected }
      : { source: selected, target: pinned[0] };
  }
  if (!selected && pinned.length === 2) return { source: pinned[0], target: pinned[1] };
  return null;
}

function creationCommand(
  type: "image" | "video" | "audio",
  request: string,
): { command: StructureOperation; reply: string } | null {
  const prompt = type === "audio"
    ? extractField(request, ["文本", "内容", "歌词", "提示词", "prompt"])
    : extractPrompt(request);
  if (prompt.length > NODE_PROMPT_MAX) return null;
  const fallback = type === "image" ? "图片" : type === "video" ? "视频" : "音频";
  const displayName = extractTitle(request, fallback);
  if (displayName.length > NODE_LABEL_MAX) return null;
  const model = extractModel(request);
  const placement = { anchor: "viewport_center" as const, layout: "grid" as const };

  if (type === "audio") {
    return {
      reply: `已创建「${displayName}」音频节点`,
      command: {
        type: "create_canvas_node",
        node_type: "audioNode",
        display_name: displayName,
        ...(prompt ? { text: prompt } : {}),
        ...(model ? { model } : {}),
        connect_selected: false,
        placement,
      },
    };
  }

  const aspectRatio = extractAspectRatio(request);
  if (type === "image") {
    const imageSize = extractImageSize(request);
    return {
      reply: `已创建「${displayName}」图片节点`,
      command: {
        type: prompt ? "create_image_prompt_node" : "create_canvas_node",
        ...(prompt ? { prompt } : { node_type: "imageGenNode" }),
        display_name: displayName,
        ...(aspectRatio ? { aspect_ratio: aspectRatio } : {}),
        ...(imageSize ? { image_size: imageSize } : {}),
        ...(model ? { model } : {}),
        connect_selected: false,
        placement,
      },
    };
  }

  const durationSec = extractDuration(request);
  const videoQuality = extractVideoQuality(request);
  const generateAudio = extractGenerateAudio(request);
  return {
    reply: `已创建「${displayName}」视频节点`,
    command: {
      type: prompt ? "create_video_prompt_node" : "create_canvas_node",
      ...(prompt ? { prompt } : { node_type: "videoNode" }),
      display_name: displayName,
      ...(aspectRatio ? { aspect_ratio: aspectRatio } : {}),
      ...(videoQuality ? { video_quality: videoQuality } : {}),
      ...(durationSec ? { duration_sec: durationSec } : {}),
      ...(generateAudio !== undefined ? { generate_audio: generateAudio } : {}),
      ...(model ? { model } : {}),
      connect_selected: false,
      placement,
    },
  };
}

type CanvasCreationType = "image" | "video" | "audio";

const CREATION_TYPE_PATTERNS: ReadonlyArray<readonly [CanvasCreationType, RegExp]> = [
  ["image", /(?:图片|图像|生图|文生图)(?:(?:的)?节点|\s*(?:一个|1个))/u],
  ["video", /(?:视频|图生视频|文生视频)(?:(?:的)?节点|\s*(?:一个|1个))/u],
  ["audio", /(?:音频|声音|音乐|配音)(?:(?:的)?节点|\s*(?:一个|1个))/u],
];

function requestedCreationTypes(request: string): CanvasCreationType[] {
  if (/(?:不要|别|禁止|无需|不需要).{0,10}(?:创建|新建|添加|插入|搭建|建立|建造)/u.test(request)) {
    return [];
  }
  const creationVerb = /(?:创建|新建|添加|加上?|插入|搭建|建立|建造|放置|给我(?:来|弄|做)|帮我(?:来|弄|做))/u;
  const genericNodeCreation = new RegExp(`${creationVerb.source}.{0,16}(?:新)?节点`, "u");
  const explicitTypedNodeCreation = new RegExp(
    `${creationVerb.source}.{0,20}(?:图片|图像|生图|文生图|视频|图生视频|文生视频|音频|声音|音乐|配音).{0,6}(?:的)?节点`,
    "u",
  );
  if (!genericNodeCreation.test(request) && !explicitTypedNodeCreation.test(request)) return [];

  return CREATION_TYPE_PATTERNS
    .map(([type, pattern]) => ({ type, index: request.search(pattern) }))
    .filter((item) => item.index >= 0)
    .sort((left, right) => left.index - right.index)
    .map((item) => item.type);
}

function creationSummary(types: readonly CanvasCreationType[]): string {
  const labels = types.map((type) => type === "image" ? "图片节点" : type === "video" ? "视频节点" : "音频节点");
  return `已创建${labels.join("和")}`;
}

/** Parse commands whose meaning is deterministic from the live canvas state. */
export function buildCanvasFastCommandPlan(
  text: string,
  context: CanvasFastCommandContext,
): CanvasFastCommandPlan | null {
  const request = compactText(text);
  const nodeId = context.selectedNodeId?.trim();
  if (!request) return null;

  const explicitTextNode = /(?:创建|新建|添加|加上?|插入)(?:一个|1个)?(?:普通)?(?:文本|文字|备注|便签)(?:的)?节点/u.test(request);
  const genericNodeWithContent = /(?:创建|新建|添加|加上?)(?:一个|1个)?节点/u.test(request)
    && /(?:正文|内容|文本)\s*(?:为|是|写成|写为)?\s*[：:]/u.test(request);
  if (explicitTextNode || genericNodeWithContent) {
    const displayName = extractTitle(request, /(?:备注|便签)/u.test(request) ? "备注" : "文本");
    const hasBodyValue = /(?:正文|内容|文本)\s*(?:(?:为|是|写成|写为|改为|改成)\s*|[：:]\s*|[“"'‘])/u.test(request);
    const body = hasBodyValue ? extractField(request, ["正文", "内容", "文本"]) : "";
    if (displayName.length > NODE_LABEL_MAX || body.length > NODE_TEXT_MAX) return null;
    return plan(context, `已创建「${displayName}」文本节点`, [{
      type: "create_canvas_node",
      node_type: "textAnnotationNode",
      display_name: displayName,
      text: body,
      connect_selected: false,
      placement: { anchor: "viewport_center", layout: "grid" },
    }]);
  }

  const creationTypes = requestedCreationTypes(request);
  if (creationTypes.length > 0) {
    const created = creationTypes.map((type) => creationCommand(type, request));
    if (created.some((item) => item === null)) return null;
    const valid = created.filter((item): item is NonNullable<typeof item> => item !== null);
    return plan(
      context,
      valid.length === 1 ? valid[0].reply : creationSummary(creationTypes),
      valid.map((item) => item.command),
    );
  }

  const pair = connectedPair(context, request);
  if (pair && /(?:删除|移除|断开|取消).{0,24}(?:连线|连接|边)|(?:连线|连接).{0,24}(?:删除|移除|断开|取消)/u.test(request)) {
    return plan(context, "已断开指定节点连线", [{ type: "remove_edge", ...pair }]);
  }
  if (pair && /(?:连接|连线|接到|连到|串联)/u.test(request)) {
    return plan(context, "已连接指定节点", [{ type: "connect_nodes", ...pair }]);
  }

  if (!nodeId) return null;
  const nodeLabel = context.selectedNodeLabel?.trim() || nodeById(context, nodeId)?.label?.trim() || "当前节点";

  if (/^(?:请)?(?:帮我)?(?:删除|删掉|移除)(?:一下)?(?:当前|这个|选中)(?:的)?节点$/u.test(request)) {
    return plan(context, `已删除「${nodeLabel}」`, [{ type: "delete_node", node_id: nodeId }]);
  }
  if (/^(?:请)?(?:帮我)?(?:复制|克隆|拷贝)(?:一下)?(?:当前|这个|选中)(?:的)?节点$/u.test(request)) {
    return plan(context, `已复制「${nodeLabel}」`, [{ type: "duplicate_node", node_id: nodeId }]);
  }
  if (/^(?:请)?(?:帮我)?(?:聚焦|定位(?:到)?|跳转到|选中)(?:一下)?(?:当前|这个|选中)(?:的)?节点$/u.test(request)) {
    return plan(context, `已定位「${nodeLabel}」`, [{ type: "focus_node", node_id: nodeId }]);
  }

  const promptUpdate = request.match(
    /^(?:请)?(?:帮我)?(?:把|将|修改|更新)?(?:当前|这个|选中)(?:的)?节点(?:的)?(?:提示词|prompt|描述)\s*(?:改为|改成|修改为|更新为|设为|设置为|是)?\s*[：:]?\s*(.+)$/iu,
  );
  if (promptUpdate?.[1]) {
    const prompt = unquote(promptUpdate[1]);
    if (!prompt || prompt.length > NODE_PROMPT_MAX) return null;
    return plan(context, `已更新「${nodeLabel}」的提示词`, [{
      type: "update_node_prompt",
      node_id: nodeId,
      prompt,
    }]);
  }

  const contentUpdate = request.match(
    /^(?:请)?(?:帮我)?(?:把|将|修改|更新)?(?:当前|这个|选中)(?:的)?节点(?:的)?(?:正文|内容|文本)\s*(?:改为|改成|修改为|更新为|设为|设置为|是)?\s*[：:]?\s*(.+)$/u,
  );
  if (contentUpdate?.[1]) {
    const content = unquote(contentUpdate[1]);
    if (!content || content.length > NODE_TEXT_MAX) return null;
    if (context.selectedNodeType === "textAnnotationNode") {
      return plan(context, `已更新「${nodeLabel}」的正文`, [{
        type: "update_node_data",
        node_id: nodeId,
        node_data: { content },
      }]);
    }
    return plan(context, `已更新「${nodeLabel}」的提示词`, [{
      type: "update_node_prompt",
      node_id: nodeId,
      prompt: content,
    }]);
  }

  const rename = request.match(
    /^(?:请)?(?:帮我)?(?:把)?(?:当前|这个|选中)(?:的)?节点(?:改名为|重命名为|改名|重命名|名字改成|名称改成|叫做|改成)[：:\s]*(.+)$/u,
  );
  if (rename?.[1]?.trim()) {
    const displayName = unquote(rename[1]);
    if (!displayName || displayName.length > NODE_LABEL_MAX) return null;
    return plan(context, `已将节点改名为「${displayName}」`, [{
      type: "update_node_label",
      node_id: nodeId,
      display_name: displayName,
    }]);
  }

  const position = context.selectedNodePosition ?? nodeById(context, nodeId)?.position;
  if (!position) return null;
  const absolute = request.match(
    /^(?:请)?(?:帮我)?(?:把)?(?:当前|这个|选中)(?:的)?节点(?:移动|挪|放)(?:到)?\s*[xX]\s*[=:：]?\s*(-?\d+(?:\.\d+)?)\s*[,，\s]+[yY]\s*[=:：]?\s*(-?\d+(?:\.\d+)?)$/u,
  );
  if (absolute) {
    return plan(context, `已移动「${nodeLabel}」`, [{
      type: "move_node",
      node_id: nodeId,
      x: Number(absolute[1]),
      y: Number(absolute[2]),
    }]);
  }

  const distanceMatch = request.match(/(\d+(?:\.\d+)?)\s*(?:像素|px)?$/iu);
  const distance = distanceMatch ? Math.min(5_000, Number(distanceMatch[1])) : undefined;
  const directions: Array<readonly [RegExp, number, number, string]> = [
    [/(?:向|往)?右(?:边)?(?:移动|挪|移)(?:一下)?(?:\s*\d+(?:\.\d+)?\s*(?:像素|px)?)?$/iu, distance ?? 320, 0, "右侧"],
    [/(?:向|往)?左(?:边)?(?:移动|挪|移)(?:一下)?(?:\s*\d+(?:\.\d+)?\s*(?:像素|px)?)?$/iu, -(distance ?? 320), 0, "左侧"],
    [/(?:向|往)?下(?:边)?(?:移动|挪|移)(?:一下)?(?:\s*\d+(?:\.\d+)?\s*(?:像素|px)?)?$/iu, 0, distance ?? 240, "下方"],
    [/(?:向|往)?上(?:边)?(?:移动|挪|移)(?:一下)?(?:\s*\d+(?:\.\d+)?\s*(?:像素|px)?)?$/iu, 0, -(distance ?? 240), "上方"],
  ];
  for (const [direction, dx, dy, label] of directions) {
    if (!/^(?:请)?(?:帮我)?(?:把)?(?:当前|这个|选中)(?:的)?节点/u.test(request)) continue;
    if (!direction.test(request)) continue;
    return plan(context, `已将「${nodeLabel}」移动到${label}`, [{
      type: "move_node",
      node_id: nodeId,
      x: position.x + dx,
      y: position.y + dy,
    }]);
  }
  return null;
}
