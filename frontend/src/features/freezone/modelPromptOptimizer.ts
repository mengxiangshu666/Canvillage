// SPDX-License-Identifier: Elastic-2.0
/** Model-aware, deterministic prompt optimizer for image nodes. */

export const MODEL_PROMPT_OPTIMIZER_REVISION = "xiaoshu-model-prompt-optimizer.v1";

export interface ModelPromptOptimizationInput {
  prompt: string;
  modelId: string;
  apiModel?: string | null;
  modelLabel?: string | null;
  aspectRatio: string;
  size: string;
  quality?: string | null;
  cameraSummary?: string | null;
  styleSummary?: string | null;
  hasReferences?: boolean;
}

export interface ModelPromptOptimization {
  optimizedPrompt: string;
  profileId: string;
  profileLabel: string;
  changes: string[];
  warnings: string[];
  revision: string;
}

interface PromptProfile {
  id: string;
  label: string;
  maxChars: number;
  format: (blocks: PromptBlocks) => string;
}

interface PromptBlocks {
  intent: string;
  camera: string;
  output: string;
  references: string;
  style: string;
  constraints: string;
}

const VAGUE_REWRITES: Array<[RegExp, string, string]> = [
  [/高级感/g, "克制的高级质感、准确的材质细节、统一而不过饱和的色彩", "把“高级感”改成可见的材质与色彩要求"],
  [/电影感/g, "电影级构图、动机明确的光线、清晰的前中后景层次", "把“电影感”拆成构图、光线和空间层次"],
  [/氛围感/g, "明确的色温、光线方向、空间层次和情绪色彩", "把“氛围感”改成可执行的光色要求"],
  [/很好看|好看/g, "构图均衡、主体清晰、视觉完成度高", "把“好看”改成构图和完成度要求"],
  [/超清|高清|清晰/g, "主体细节清楚、边缘自然、纹理真实且不过度锐化", "把“清晰”改成细节与锐化边界"],
  [/真实感|真实/g, "符合物理规律的光影、真实材质和自然皮肤纹理", "把“真实”改成光影与材质约束"],
  [/不要崩/g, "保持正确解剖结构、五官稳定、手指数量正确", "把“不要崩”改成明确的解剖约束"],
];

function concretize(prompt: string): { text: string; changes: string[] } {
  let text = prompt.trim().replace(/\s+/g, " ");
  const changes: string[] = [];
  for (const [pattern, replacement, change] of VAGUE_REWRITES) {
    if (!pattern.test(text)) continue;
    pattern.lastIndex = 0;
    text = text.replace(pattern, replacement);
    changes.push(change);
  }
  return { text, changes };
}

function resolveProfile(input: ModelPromptOptimizationInput): PromptProfile {
  const id = `${input.modelId} ${input.apiModel ?? ""} ${input.modelLabel ?? ""}`.toLowerCase();
  if (/seedream|doubao|即梦/.test(id)) {
    return {
      id: "seedream-structured-zh",
      label: "Seedream / 即梦多参考结构",
      maxChars: 1200,
      format: (b) => [`【创作目标】${b.intent}`, b.references, `【画面与镜头】${b.camera}`, b.style, `【输出规格】${b.output}`, `【硬约束】${b.constraints}`].filter(Boolean).join("\n"),
    };
  }
  if (/gpt-image|image2|openai/.test(id)) {
    return {
      id: "gpt-image-instruction",
      label: "GPT Image 指令层级",
      maxChars: 1400,
      format: (b) => [`TASK: ${b.intent}`, b.references, `COMPOSITION AND CAMERA: ${b.camera}`, b.style, `OUTPUT: ${b.output}`, `MUST PRESERVE / AVOID: ${b.constraints}`].filter(Boolean).join("\n"),
    };
  }
  if (/qwen|通义|wanx/.test(id)) {
    return {
      id: "qwen-image-edit-zh",
      label: "Qwen Image 中文编辑指令",
      maxChars: 1400,
      format: (b) => [`任务：${b.intent}`, b.references, `画面结构与摄影：${b.camera}`, b.style, `输出：${b.output}`, `保持与禁止：${b.constraints}`].filter(Boolean).join("\n"),
    };
  }
  if (/nano.?banana|gemini/.test(id)) {
    return {
      id: "gemini-image-conversational",
      label: "Nano Banana / Gemini 编辑说明",
      maxChars: 1500,
      format: (b) => [`请完成以下图像任务：${b.intent}`, b.references, `画面与摄影要求：${b.camera}`, b.style, `交付规格：${b.output}`, `除明确要求外必须保持：${b.constraints}`].filter(Boolean).join("\n"),
    };
  }
  if (/flux/.test(id)) {
    return {
      id: "flux-concrete-visual",
      label: "FLUX 具体视觉描述",
      maxChars: 900,
      format: (b) => [`SUBJECT / ACTION: ${b.intent}`, `COMPOSITION / CAMERA: ${b.camera}`, `STYLE / LIGHT: ${b.style || "follow the requested visual language"}`, b.references, `OUTPUT: ${b.output}`, `CONSTRAINTS: ${b.constraints}`].filter(Boolean).join("\n"),
    };
  }
  if (/midjourney|\bmj\b/.test(id)) {
    return {
      id: "midjourney-compact",
      label: "Midjourney 紧凑视觉短语",
      maxChars: 650,
      format: (b) => [b.intent, b.camera, b.style, b.references, b.constraints, `--ar ${b.output.split(" · ")[0]}`].filter(Boolean).join(", "),
    };
  }
  return {
    id: "generic-image-structured",
    label: "通用图像模型结构化提示词",
    maxChars: 1200,
    format: (b) => [`主体与动作：${b.intent}`, `构图与摄影：${b.camera}`, b.style, b.references, `输出规格：${b.output}`, `约束：${b.constraints}`].filter(Boolean).join("\n"),
  };
}

export function optimizePromptForModel(input: ModelPromptOptimizationInput): ModelPromptOptimization {
  const source = input.prompt.trim();
  if (!source) throw new Error("请先输入提示词");
  const profile = resolveProfile(input);
  const concrete = concretize(source);
  const camera = input.cameraSummary?.trim() || "保持自然透视；主体、景别和机位以创作目标为准";
  const references = input.hasReferences
    ? "参考图优先：参考图是人物身份、服装、道具和视觉连续性的最高依据；只修改任务明确要求改变的内容。"
    : "";
  const style = input.styleSummary?.trim() ? `视觉风格：${input.styleSummary.trim()}` : "";
  const output = `${input.aspectRatio} · ${input.size}${input.quality ? ` · ${input.quality}` : ""} · 单张完整画面，无拼图，无说明文字`;
  const constraints = "不擅自增加人物或道具，不改变未要求变化的人物身份、服装、场景关系和构图逻辑；避免畸形五官、错误手指、水印和无关文字。";
  let optimizedPrompt = profile.format({ intent: concrete.text, camera, output, references, style, constraints });
  const warnings: string[] = [];
  if (optimizedPrompt.length > profile.maxChars) {
    warnings.push(`当前结果 ${optimizedPrompt.length} 字符，超过 ${profile.label} 建议的 ${profile.maxChars} 字符；已保留原意，生成前可继续精简剧情。`);
  }
  if (!input.cameraSummary) warnings.push("尚未选择摄像机参数，优化器只使用了自然透视默认值。");
  if (concrete.changes.length === 0) concrete.changes.push("保留原始创作意图，按目标模型重排指令层级");
  concrete.changes.push(`套用 ${profile.label}`);
  concrete.changes.push(`写入画幅 ${input.aspectRatio}、品质 ${input.size}${input.quality ? `/${input.quality}` : ""}`);
  optimizedPrompt = optimizedPrompt.trim();
  return {
    optimizedPrompt,
    profileId: profile.id,
    profileLabel: profile.label,
    changes: concrete.changes,
    warnings,
    revision: MODEL_PROMPT_OPTIMIZER_REVISION,
  };
}
