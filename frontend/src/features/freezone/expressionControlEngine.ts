// SPDX-License-Identifier: Elastic-2.0
// Clean-room UI/request contract inspired by Ammmob/PixelSmile (Apache-2.0).

export const EXPRESSION_CONTROL_REVISION = "pixelsmile-compatible.v1";
export const EXPRESSION_CONTROL_SOURCE =
  "Ammmob/PixelSmile@904624630b2675b2137300a1eaf10f1f3dc1bfa8 (Apache-2.0; contract adaptation)";

export interface FineExpression {
  key: string;
  label: string;
  emoji: string;
  prompt: string;
}

export const FINE_EXPRESSIONS: FineExpression[] = [
  { key: "neutral", label: "中性", emoji: "😐", prompt: "neutral relaxed expression, calm eyes and relaxed mouth" },
  { key: "angry", label: "愤怒", emoji: "😠", prompt: "controlled anger, focused eyes, tightened jaw and tense brows" },
  { key: "confused", label: "困惑", emoji: "🤨", prompt: "credible confusion, asymmetric raised brow and searching gaze" },
  { key: "contempt", label: "轻蔑", emoji: "😏", prompt: "subtle contempt, asymmetric half-smile and narrowed confident eyes" },
  { key: "confident", label: "自信", emoji: "😌", prompt: "quiet confidence, steady eyes, composed jaw and restrained smile" },
  { key: "disgust", label: "厌恶", emoji: "🤢", prompt: "clear disgust, wrinkled nose and slightly raised upper lip" },
  { key: "fear", label: "恐惧", emoji: "😨", prompt: "credible fear, alert widened eyes and anxious facial tension" },
  { key: "happy", label: "开心", emoji: "😊", prompt: "genuine happiness, bright eyes and naturally raised cheeks" },
  { key: "sad", label: "悲伤", emoji: "😔", prompt: "restrained sadness, lowered gaze and subtle eye and brow tension" },
  { key: "shy", label: "害羞", emoji: "☺️", prompt: "shyness, softened averted gaze, subtle blush and restrained smile" },
  { key: "sleepy", label: "困倦", emoji: "😪", prompt: "natural sleepiness, heavy eyelids and relaxed facial muscles" },
  { key: "surprised", label: "惊讶", emoji: "😲", prompt: "genuine surprise, widened eyes, raised brows and naturally parted lips" },
  { key: "anxious", label: "焦虑", emoji: "😟", prompt: "credible anxiety, uncertain gaze and controlled tension around eyes and mouth" },
];

export interface ExpressionMix {
  primary: FineExpression;
  secondary: FineExpression | null;
  scale: number;
  secondaryWeight: number;
}

export function expressionByKey(key: string): FineExpression {
  return FINE_EXPRESSIONS.find((item) => item.key === key) ?? FINE_EXPRESSIONS[0];
}

export function clampExpressionScale(value: number): number {
  return Math.max(0, Math.min(1.5, value));
}

export function clampBlendWeight(value: number): number {
  return Math.max(0, Math.min(1, value));
}

export function describeExpressionMix(mix: ExpressionMix): string {
  const scale = clampExpressionScale(mix.scale);
  const secondaryWeight = mix.secondary ? clampBlendWeight(mix.secondaryWeight) : 0;
  if (!mix.secondary || secondaryWeight === 0) {
    return `${mix.primary.label} ${Math.round(scale * 100)}%`;
  }
  return `${mix.primary.label} ${Math.round((1 - secondaryWeight) * 100)}% + ${mix.secondary.label} ${Math.round(secondaryWeight * 100)}% · 强度 ${Math.round(scale * 100)}%`;
}

export function compileExpressionMixContract(mix: ExpressionMix): string {
  const scale = clampExpressionScale(mix.scale);
  const secondaryWeight = mix.secondary ? clampBlendWeight(mix.secondaryWeight) : 0;
  const strength =
    scale >= 1.15 ? "strong" : scale <= 0.55 ? "subtle" : "clear";
  if (mix.secondary && secondaryWeight > 0) {
    return `${strength} ${mix.primary.prompt}, lightly blended with ${mix.secondary.prompt} (${Math.round(secondaryWeight * 100)}%)`;
  }
  return `${strength} ${mix.primary.prompt}`;
}
