import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { Application, Entity, MorphInstance } from "playcanvas";
import { MOOD_GRID, MOOD_GRID_COLUMNS, MOOD_GRID_ROWS } from "./emotionMoodGrid";

interface ExpressionHeadPreviewProps {
  affectX: number;
  affectY: number;
  primaryExpression: string;
  secondaryExpression: string;
  expressionScale: number;
  secondaryWeight: number;
  paused?: boolean;
  onCanvasReady?: (canvas: HTMLCanvasElement | null) => void;
}

interface MorphBinding {
  morph: MorphInstance;
  /** 受控形状名 → 该 morph 实例上真实存在的目标名（别名可能一对多）。 */
  names: Map<string, readonly string[]>;
}

interface HeadRig {
  app: Application;
  avatar: Entity;
  bindings: MorphBinding[];
  currentWeights: ShapeWeights;
  targetWeights: ShapeWeights;
  currentPose: { pitch: number; yaw: number; roll: number };
  targetPose: { pitch: number; yaw: number; roll: number };
  dispose: () => void;
}

type ShapeWeights = Record<string, number>;

const EXPRESSION_SHAPES: Record<string, ShapeWeights> = {
  neutral: {},
  happy: {
    mouthSmileLeft: 0.9,
    mouthSmileRight: 0.9,
    cheekSquintLeft: 0.35,
    cheekSquintRight: 0.35,
    eyeSquintLeft: 0.12,
    eyeSquintRight: 0.12,
  },
  sad: {
    mouthFrownLeft: 0.82,
    mouthFrownRight: 0.82,
    browInnerUp: 0.68,
    eyeSquintLeft: 0.12,
    eyeSquintRight: 0.12,
  },
  angry: {
    browDownLeft: 0.82,
    browDownRight: 0.82,
    eyeSquintLeft: 0.3,
    eyeSquintRight: 0.3,
    mouthPressLeft: 0.5,
    mouthPressRight: 0.5,
    jawForward: 0.14,
  },
  surprised: {
    eyeWideLeft: 0.86,
    eyeWideRight: 0.86,
    browInnerUp: 0.75,
    browOuterUpLeft: 0.72,
    browOuterUpRight: 0.72,
    jawOpen: 0.72,
    mouthFunnel: 0.25,
  },
  fear: {
    eyeWideLeft: 0.72,
    eyeWideRight: 0.72,
    browInnerUp: 0.9,
    browOuterUpLeft: 0.42,
    browOuterUpRight: 0.42,
    jawOpen: 0.4,
    mouthStretchLeft: 0.48,
    mouthStretchRight: 0.48,
  },
  confused: {
    browOuterUpLeft: 0.72,
    browDownRight: 0.42,
    eyeSquintRight: 0.22,
    mouthFrownRight: 0.24,
  },
  contempt: {
    mouthSmileLeft: 0.54,
    mouthDimpleLeft: 0.44,
    eyeSquintLeft: 0.2,
    browOuterUpLeft: 0.16,
  },
  confident: {
    mouthSmileLeft: 0.36,
    mouthSmileRight: 0.36,
    mouthPressLeft: 0.12,
    mouthPressRight: 0.12,
    eyeSquintLeft: 0.12,
    eyeSquintRight: 0.12,
  },
  disgust: {
    noseSneerLeft: 0.8,
    noseSneerRight: 0.8,
    mouthUpperUpLeft: 0.46,
    mouthUpperUpRight: 0.46,
    mouthFrownLeft: 0.32,
    mouthFrownRight: 0.32,
    eyeSquintLeft: 0.2,
    eyeSquintRight: 0.2,
  },
  shy: {
    mouthSmileLeft: 0.48,
    mouthSmileRight: 0.48,
    eyeBlinkLeft: 0.16,
    eyeBlinkRight: 0.16,
    cheekSquintLeft: 0.28,
    cheekSquintRight: 0.28,
  },
  sleepy: {
    eyeBlinkLeft: 0.66,
    eyeBlinkRight: 0.66,
    jawOpen: 0.08,
    browDownLeft: 0.12,
    browDownRight: 0.12,
  },
  anxious: {
    browInnerUp: 0.56,
    browDownLeft: 0.25,
    browDownRight: 0.25,
    eyeWideLeft: 0.24,
    eyeWideRight: 0.24,
    mouthStretchLeft: 0.3,
    mouthStretchRight: 0.3,
    mouthFrownLeft: 0.2,
    mouthFrownRight: 0.2,
  },
};

// Every semantic cell has its own facial signature. These are not labels laid over
// six basic emotions: gaze, eyelids, brows, cheeks, lips and jaw all differ.
export const MOOD_SHAPE_OVERRIDES: Readonly<Record<string, ShapeWeights>> = {
  "restrained-grief": { browInnerUp:.82, browDownLeft:.16, browDownRight:.16, eyeLookDownLeft:.36, eyeLookDownRight:.36, eyeBlinkLeft:.18, eyeBlinkRight:.18, mouthFrownLeft:.52, mouthFrownRight:.52, mouthPressLeft:.28, mouthPressRight:.28, mouthRollLower:.18 },
  "silent-tears": { browInnerUp:.95, eyeLookDownLeft:.22, eyeLookDownRight:.22, eyeWideLeft:.12, eyeWideRight:.12, mouthFrownLeft:.66, mouthFrownRight:.66, mouthLowerDownLeft:.18, mouthLowerDownRight:.18, jawOpen:.06 },
  "memory-touched": { browInnerUp:.58, browOuterUpLeft:.12, browOuterUpRight:.12, eyeLookUpLeft:.15, eyeLookUpRight:.15, eyeBlinkLeft:.08, eyeBlinkRight:.08, mouthSmileLeft:.13, mouthSmileRight:.13, mouthFrownLeft:.22, mouthFrownRight:.22, mouthPressLeft:.12, mouthPressRight:.12 },
  "mourning-suppressed": { browInnerUp:.72, browDownLeft:.22, browDownRight:.22, eyeSquintLeft:.22, eyeSquintRight:.22, mouthFrownLeft:.48, mouthFrownRight:.48, mouthPressLeft:.5, mouthPressRight:.5, mouthClose:.34, jawForward:.06 },
  "hidden-heartache": { browInnerUp:.5, browDownRight:.18, eyeLookOutLeft:.16, eyeLookOutRight:.16, eyeBlinkLeft:.12, eyeBlinkRight:.2, mouthFrownLeft:.38, mouthFrownRight:.5, mouthLeft:.08, mouthPressLeft:.3, mouthPressRight:.34 },
  "gentle-smile": { mouthSmileLeft:.48, mouthSmileRight:.48, cheekSquintLeft:.24, cheekSquintRight:.24, eyeBlinkLeft:.06, eyeBlinkRight:.06, mouthDimpleLeft:.16, mouthDimpleRight:.16, browOuterUpLeft:.06, browOuterUpRight:.06 },
  "affectionate-gaze": { mouthSmileLeft:.3, mouthSmileRight:.3, cheekSquintLeft:.18, cheekSquintRight:.18, eyeLookInLeft:.18, eyeLookInRight:.18, eyeBlinkLeft:.1, eyeBlinkRight:.1, browInnerUp:.16, mouthPucker:.05 },
  "indulgent-eyes": { mouthSmileLeft:.42, mouthSmileRight:.36, cheekSquintLeft:.38, cheekSquintRight:.38, eyeSquintLeft:.28, eyeSquintRight:.28, browOuterUpLeft:.1, mouthDimpleLeft:.22, mouthDimpleRight:.16, mouthLeft:.05 },
  "helpless-smile": { mouthSmileLeft:.28, mouthSmileRight:.46, mouthFrownLeft:.12, browOuterUpLeft:.3, browDownRight:.12, eyeLookOutLeft:.12, eyeLookOutRight:.12, cheekSquintLeft:.12, cheekSquintRight:.12, mouthRight:.08 },
  "pleasant-joy": { mouthSmileLeft:.92, mouthSmileRight:.92, cheekSquintLeft:.58, cheekSquintRight:.58, eyeSquintLeft:.25, eyeSquintRight:.25, mouthDimpleLeft:.34, mouthDimpleRight:.34, jawOpen:.16, mouthUpperUpLeft:.14, mouthUpperUpRight:.14 },
  "frosty-brows": { browDownLeft:.5, browDownRight:.5, eyeSquintLeft:.18, eyeSquintRight:.18, eyeLookOutLeft:.12, eyeLookOutRight:.12, mouthPressLeft:.42, mouthPressRight:.42, mouthFrownLeft:.14, mouthFrownRight:.14 },
  "restrained-anger": { browDownLeft:.78, browDownRight:.78, eyeSquintLeft:.32, eyeSquintRight:.32, noseSneerLeft:.16, noseSneerRight:.16, mouthPressLeft:.66, mouthPressRight:.66, jawForward:.16, mouthClose:.32 },
  "cold-indifference": { eyeBlinkLeft:.22, eyeBlinkRight:.22, eyeLookOutLeft:.18, eyeLookOutRight:.18, mouthPressLeft:.2, mouthPressRight:.2, mouthFrownLeft:.06, mouthFrownRight:.06, browDownLeft:.08, browDownRight:.08 },
  "pent-up-frustration": { browDownLeft:.68, browDownRight:.58, browInnerUp:.18, eyeSquintLeft:.28, eyeSquintRight:.2, noseSneerLeft:.24, noseSneerRight:.18, mouthPressLeft:.56, mouthPressRight:.62, mouthFrownLeft:.28, mouthFrownRight:.28, jawRight:.05 },
  "intense-anger": { browDownLeft:1, browDownRight:1, eyeWideLeft:.22, eyeWideRight:.22, eyeSquintLeft:.3, eyeSquintRight:.3, noseSneerLeft:.62, noseSneerRight:.62, mouthUpperUpLeft:.5, mouthUpperUpRight:.5, mouthFrownLeft:.56, mouthFrownRight:.56, jawOpen:.34, jawForward:.24 },
  "sudden-astonishment": { browInnerUp:.66, browOuterUpLeft:.82, browOuterUpRight:.82, eyeWideLeft:.92, eyeWideRight:.92, jawOpen:.72, mouthFunnel:.22, mouthStretchLeft:.14, mouthStretchRight:.14 },
  disbelief: { browOuterUpLeft:.72, browDownRight:.28, eyeWideLeft:.46, eyeSquintRight:.2, eyeLookOutLeft:.12, eyeLookOutRight:.12, mouthFrownRight:.18, mouthLeft:.1, jawOpen:.15 },
  "shaken-fear": { browInnerUp:1, browOuterUpLeft:.46, browOuterUpRight:.46, eyeWideLeft:.84, eyeWideRight:.84, mouthStretchLeft:.62, mouthStretchRight:.62, mouthFrownLeft:.24, mouthFrownRight:.24, jawOpen:.42, mouthLowerDownLeft:.22, mouthLowerDownRight:.22 },
  "startled-retreat": { browInnerUp:.72, eyeWideLeft:.66, eyeWideRight:.74, eyeLookOutLeft:.26, eyeLookOutRight:.26, mouthStretchLeft:.48, mouthStretchRight:.48, jawOpen:.26, mouthRight:.08, cheekPuff:.08 },
  "stunned-shock": { browOuterUpLeft:.7, browOuterUpRight:.7, eyeWideLeft:1, eyeWideRight:1, eyeLookUpLeft:.08, eyeLookUpRight:.08, jawOpen:.88, mouthFunnel:.4, mouthRollLower:.1 },
  "calm-composure": { eyeBlinkLeft:.16, eyeBlinkRight:.16, mouthSmileLeft:.07, mouthSmileRight:.07, mouthClose:.12, browOuterUpLeft:.03, browOuterUpRight:.03 },
  "distant-coldness": { eyeSquintLeft:.12, eyeSquintRight:.12, eyeLookOutLeft:.28, eyeLookOutRight:.28, mouthPressLeft:.3, mouthPressRight:.3, mouthFrownLeft:.08, mouthFrownRight:.08, browDownLeft:.1, browDownRight:.1 },
  "hesitant-words": { browInnerUp:.28, eyeLookOutLeft:.18, eyeLookOutRight:.18, mouthPressLeft:.18, mouthPressRight:.1, mouthPucker:.1, mouthLeft:.1, mouthRollLower:.16, jawOpen:.04 },
  "alert-scrutiny": { browDownLeft:.38, browDownRight:.38, eyeWideLeft:.28, eyeWideRight:.28, eyeSquintLeft:.18, eyeSquintRight:.18, eyeLookInLeft:.2, eyeLookInRight:.2, mouthPressLeft:.28, mouthPressRight:.28, jawForward:.08 },
  "tired-blankness": { eyeBlinkLeft:.68, eyeBlinkRight:.68, eyeLookDownLeft:.18, eyeLookDownRight:.18, browInnerUp:.08, mouthFrownLeft:.1, mouthFrownRight:.1, mouthLowerDownLeft:.1, mouthLowerDownRight:.1, jawOpen:.07 },
  "micro-suspicion": { browDownLeft:.34, browOuterUpRight:.08, eyeSquintLeft:.31, eyeLookOutLeft:.22, eyeLookOutRight:.22, noseSneerLeft:.09, cheekSquintLeft:.07, mouthRight:.06, mouthPressLeft:.18, mouthPressRight:.28 },
  "suppressed-delight": { browOuterUpLeft:.09, eyeLookDownLeft:.13, eyeLookDownRight:.13, eyeSquintLeft:.18, eyeSquintRight:.18, cheekSquintLeft:.32, cheekSquintRight:.32, mouthSmileLeft:.48, mouthSmileRight:.48, mouthRollUpper:.12, mouthPressLeft:.21, mouthPressRight:.21 },
  "anxious-hope": { browInnerUp:.57, browDownLeft:.13, browDownRight:.13, eyeWideLeft:.3, eyeWideRight:.3, noseSneerLeft:.06, noseSneerRight:.06, cheekSquintLeft:.08, cheekSquintRight:.08, mouthSmileLeft:.1, mouthFrownRight:.16, mouthStretchLeft:.2, mouthStretchRight:.2, jawOpen:.08 },
  "proud-relief": { browOuterUpLeft:.14, browOuterUpRight:.14, eyeSquintLeft:.15, eyeSquintRight:.15, cheekSquintLeft:.2, cheekSquintRight:.2, mouthSmileLeft:.38, mouthSmileRight:.38, mouthDimpleLeft:.19, mouthDimpleRight:.19, mouthPressLeft:.07, mouthPressRight:.07, jawForward:.04 },
  "bitter-amusement": { browInnerUp:.4, browOuterUpLeft:.23, eyeBlinkLeft:.18, eyeBlinkRight:.28, eyeLookDownLeft:.16, eyeLookDownRight:.16, noseSneerLeft:.1, cheekSquintLeft:.2, mouthSmileLeft:.31, mouthFrownRight:.32, mouthLeft:.09, jawLeft:.04 },
  "embarrassed-flush": { browInnerUp:.36, browOuterUpLeft:.12, browOuterUpRight:.12, eyeBlinkLeft:.4, eyeBlinkRight:.4, eyeLookDownLeft:.28, eyeLookDownRight:.28, eyeLookOutLeft:.12, eyeLookOutRight:.12, cheekSquintLeft:.46, cheekSquintRight:.46, mouthSmileLeft:.24, mouthSmileRight:.24, mouthPressLeft:.14, mouthPressRight:.14, jawForward:.03 },
  "jealous-restraint": { browDownLeft:.48, browDownRight:.22, eyeSquintLeft:.24, eyeSquintRight:.18, eyeLookOutLeft:.35, eyeLookOutRight:.35, noseSneerLeft:.17, cheekSquintLeft:.09, mouthFrownLeft:.2, mouthPressLeft:.45, mouthPressRight:.45, mouthLeft:.07, jawRight:.05 },
  "guilty-avoidance": { browInnerUp:.45, browOuterUpRight:.1, eyeBlinkLeft:.26, eyeBlinkRight:.32, eyeLookDownLeft:.32, eyeLookDownRight:.32, eyeLookOutLeft:.24, eyeLookOutRight:.24, noseSneerRight:.05, mouthFrownLeft:.17, mouthFrownRight:.17, mouthPressLeft:.38, mouthPressRight:.38, mouthRollLower:.2, jawForward:.02 },
  "awed-reverence": { browInnerUp:.54, browOuterUpLeft:.58, browOuterUpRight:.58, eyeWideLeft:.55, eyeWideRight:.55, eyeLookUpLeft:.32, eyeLookUpRight:.32, cheekSquintLeft:.04, cheekSquintRight:.04, mouthFunnel:.18, mouthLowerDownLeft:.1, mouthLowerDownRight:.1, jawOpen:.25 },
  "defiant-courage": { browDownLeft:.62, browDownRight:.62, eyeSquintLeft:.24, eyeSquintRight:.24, eyeLookInLeft:.13, eyeLookInRight:.13, noseSneerLeft:.12, noseSneerRight:.12, cheekSquintLeft:.1, cheekSquintRight:.1, mouthSmileLeft:.09, mouthPressLeft:.48, mouthPressRight:.48, jawForward:.22, mouthClose:.22 },
  "contemptuous-smirk": { browOuterUpLeft:.27, browDownRight:.16, eyeBlinkLeft:.28, eyeBlinkRight:.24, eyeLookDownLeft:.14, eyeLookDownRight:.14, noseSneerLeft:.24, cheekSquintLeft:.28, mouthSmileLeft:.62, mouthDimpleLeft:.36, mouthPressRight:.16, mouthLeft:.1, jawLeft:.05 },
  "disgusted-recoil": { browDownLeft:.43, browDownRight:.43, eyeSquintLeft:.38, eyeSquintRight:.38, eyeLookOutLeft:.28, eyeLookOutRight:.28, noseSneerLeft:.92, noseSneerRight:.92, cheekSquintLeft:.34, cheekSquintRight:.34, mouthUpperUpLeft:.62, mouthUpperUpRight:.62, mouthFrownLeft:.4, mouthFrownRight:.4, jawOpen:.12, jawForward:.03 },
  "panic-surge": { browInnerUp:1.08, browOuterUpLeft:.65, browOuterUpRight:.65, eyeWideLeft:1.05, eyeWideRight:1.05, eyeLookOutLeft:.34, eyeLookOutRight:.34, noseSneerLeft:.28, noseSneerRight:.28, mouthStretchLeft:.82, mouthStretchRight:.82, mouthFrownLeft:.38, mouthFrownRight:.38, jawOpen:.55, mouthLowerDownLeft:.3, mouthLowerDownRight:.3 },
  "euphoric-burst": { browOuterUpLeft:.36, browOuterUpRight:.36, eyeSquintLeft:.46, eyeSquintRight:.46, cheekSquintLeft:.88, cheekSquintRight:.88, noseSneerLeft:.08, noseSneerRight:.08, mouthSmileLeft:1.18, mouthSmileRight:1.18, mouthDimpleLeft:.48, mouthDimpleRight:.48, mouthUpperUpLeft:.28, mouthUpperUpRight:.28, jawOpen:.44 },
  "ferocious-resolve": { browDownLeft:1.15, browDownRight:1.15, eyeSquintLeft:.48, eyeSquintRight:.48, eyeLookInLeft:.14, eyeLookInRight:.14, noseSneerLeft:.88, noseSneerRight:.88, cheekSquintLeft:.26, cheekSquintRight:.26, mouthFrownLeft:.68, mouthFrownRight:.68, mouthUpperUpLeft:.58, mouthUpperUpRight:.58, mouthStretchLeft:.32, mouthStretchRight:.32, jawForward:.45, jawOpen:.3 },
  "shame-collapse": { browInnerUp:.88, browDownLeft:.2, browDownRight:.2, eyeBlinkLeft:.72, eyeBlinkRight:.72, eyeLookDownLeft:.4, eyeLookDownRight:.4, noseSneerLeft:.12, noseSneerRight:.12, cheekSquintLeft:.16, cheekSquintRight:.16, mouthFrownLeft:.58, mouthFrownRight:.58, mouthPressLeft:.5, mouthPressRight:.5, mouthRollLower:.32, jawForward:.04 },
  "grief-breakthrough": { browInnerUp:1.12, browDownLeft:.24, browDownRight:.24, eyeSquintLeft:.64, eyeSquintRight:.64, eyeBlinkLeft:.3, eyeBlinkRight:.3, noseSneerLeft:.33, noseSneerRight:.33, cheekSquintLeft:.52, cheekSquintRight:.52, mouthFrownLeft:.92, mouthFrownRight:.92, mouthStretchLeft:.3, mouthStretchRight:.3, mouthLowerDownLeft:.42, mouthLowerDownRight:.42, jawOpen:.47 },
  "nervous-laughter": { browInnerUp:.62, browOuterUpLeft:.2, browOuterUpRight:.2, eyeSquintLeft:.3, eyeSquintRight:.3, eyeLookInLeft:.16, eyeLookInRight:.16, noseSneerLeft:.08, noseSneerRight:.08, cheekSquintLeft:.54, cheekSquintRight:.46, mouthSmileLeft:.67, mouthSmileRight:.58, mouthStretchLeft:.34, mouthStretchRight:.34, mouthFrownRight:.08, jawOpen:.2, jawRight:.03 },
  "triumphant-grin": { browOuterUpLeft:.42, browDownRight:.14, eyeSquintLeft:.32, eyeSquintRight:.32, eyeLookDownLeft:.11, eyeLookDownRight:.11, noseSneerLeft:.1, cheekSquintLeft:.62, cheekSquintRight:.57, mouthSmileLeft:.98, mouthSmileRight:.88, mouthDimpleLeft:.44, mouthDimpleRight:.35, mouthUpperUpLeft:.22, mouthUpperUpRight:.22, jawForward:.2, jawOpen:.24 },
  "manic-excitement": { browInnerUp:.46, browOuterUpLeft:.9, browOuterUpRight:.7, eyeWideLeft:.9, eyeWideRight:.82, eyeLookOutLeft:.1, eyeLookOutRight:.1, noseSneerLeft:.26, noseSneerRight:.26, cheekSquintLeft:.58, cheekSquintRight:.58, mouthSmileLeft:1.05, mouthSmileRight:1.05, mouthStretchLeft:.48, mouthStretchRight:.48, mouthFunnel:.18, jawOpen:.65 },
  "tender-concern": { browInnerUp:.43, browOuterUpLeft:.05, browOuterUpRight:.05, eyeSquintLeft:.13, eyeSquintRight:.13, eyeLookInLeft:.14, eyeLookInRight:.14, cheekSquintLeft:.12, cheekSquintRight:.12, mouthFrownLeft:.15, mouthFrownRight:.15, mouthSmileLeft:.05, mouthSmileRight:.05, mouthPressLeft:.09, mouthPressRight:.09 },
  "resigned-acceptance": { browInnerUp:.25, eyeBlinkLeft:.34, eyeBlinkRight:.34, eyeLookDownLeft:.08, eyeLookDownRight:.08, cheekSquintLeft:.04, cheekSquintRight:.04, mouthSmileLeft:.13, mouthFrownRight:.11, mouthPressLeft:.18, mouthPressRight:.18, mouthRollLower:.09, jawOpen:.05 },
  "lonely-longing": { browInnerUp:.5, eyeBlinkLeft:.24, eyeBlinkRight:.24, eyeLookOutLeft:.31, eyeLookOutRight:.31, eyeLookUpLeft:.08, eyeLookUpRight:.08, cheekSquintLeft:.09, cheekSquintRight:.09, mouthFrownLeft:.3, mouthFrownRight:.3, mouthPucker:.08, mouthShrugLower:.13, jawForward:.02 },
  "guarded-curiosity": { browOuterUpLeft:.5, browDownRight:.31, eyeWideLeft:.22, eyeSquintRight:.28, eyeLookOutLeft:.25, eyeLookOutRight:.25, noseSneerRight:.11, cheekSquintRight:.08, mouthPressLeft:.17, mouthPressRight:.17, mouthPucker:.09, mouthLeft:.05, jawForward:.09 },
  "serene-contentment": { browOuterUpLeft:.025, browOuterUpRight:.025, eyeBlinkLeft:.3, eyeBlinkRight:.3, cheekSquintLeft:.14, cheekSquintRight:.14, mouthSmileLeft:.27, mouthSmileRight:.27, mouthDimpleLeft:.08, mouthDimpleRight:.08, mouthClose:.1, jawOpen:.01 },
};

const AFFECT_SHAPES = [
  "browDownLeft", "browDownRight", "browInnerUp", "browOuterUpLeft", "browOuterUpRight",
  "cheekPuff", "cheekSquintLeft", "cheekSquintRight", "eyeBlinkLeft", "eyeBlinkRight",
  "eyeLookDownLeft", "eyeLookDownRight", "eyeLookInLeft", "eyeLookInRight", "eyeLookOutLeft",
  "eyeLookOutRight", "eyeLookUpLeft", "eyeLookUpRight", "eyeSquintLeft", "eyeSquintRight",
  "eyeWideLeft", "eyeWideRight", "jawForward", "jawLeft", "jawOpen", "jawRight", "mouthClose",
  "mouthDimpleLeft", "mouthDimpleRight", "mouthFrownLeft", "mouthFrownRight", "mouthFunnel",
  "mouthLeft", "mouthLowerDownLeft", "mouthLowerDownRight", "mouthPressLeft", "mouthPressRight",
  "mouthPucker", "mouthRight", "mouthRollLower", "mouthRollUpper", "mouthShrugLower", "mouthShrugUpper",
  "mouthSmileLeft", "mouthSmileRight", "mouthStretchLeft", "mouthStretchRight", "mouthUpperUpLeft",
  "mouthUpperUpRight", "noseSneerLeft", "noseSneerRight",
] as const;

const CONTROLLED_SHAPES = Array.from(
  new Set(Object.values(EXPRESSION_SHAPES).flatMap((weights) => Object.keys(weights)).concat(
    AFFECT_SHAPES,
    ["browInnerUpLeft", "browInnerUpRight", "cheekPuffLeft", "cheekPuffRight"],
  )),
);

const PRECISION_GRID_SIZE = 21; // 21 × 21 = 441 continuously interpolated facial anchors.

/**
 * 仓库里唯一完好的 ict-facekit 头模把双侧肌群导出成 xxxLeft/xxxRight，没有不带后缀的
 * 中心名（browInnerUp / cheekPuff）。这里做别名，让同一份权重同时驱动左右两侧；资产
 * 自带中心名时仍优先用中心名，两种导出命名都能跑。
 */
export const BILATERAL_SHAPE_ALIASES: Record<string, readonly [string, string]> = {
  browInnerUp: ["browInnerUpLeft", "browInnerUpRight"],
  cheekPuff: ["cheekPuffLeft", "cheekPuffRight"],
};

/**
 * 把受控形状名解析成该 morph 实例上真实存在的目标名；实例没有的形状整条丢弃，
 * 避免 playcanvas 对每个缺失名打一次 `Cannot find morph target` 日志。
 */
export function resolveMorphBindingNames(
  available: ReadonlySet<string>,
  shapes: readonly string[] = CONTROLLED_SHAPES,
): Map<string, readonly string[]> {
  const resolved = new Map<string, readonly string[]>();
  for (const shape of shapes) {
    if (available.has(shape)) {
      resolved.set(shape, [shape]);
      continue;
    }
    const aliases = BILATERAL_SHAPE_ALIASES[shape];
    if (aliases && aliases.every((alias) => available.has(alias))) {
      resolved.set(shape, aliases);
    }
  }
  return resolved;
}

function clampWeight(value: number): number {
  return Math.max(0, Math.min(1.5, value));
}

function affectAnchorWeights(x: number, y: number): ShapeWeights {
  const negative = Math.max(0, -x);
  const positive = Math.max(0, x);
  const activation = Math.max(0, y);
  const calm = Math.max(0, -y);
  const intensity = Math.min(1, Math.hypot(x, y));
  const positiveEnergy = positive * activation;
  const negativeEnergy = negative * activation;
  const subduedNegative = negative * calm;
  const asymmetry = x * y * 0.055;
  return {
    mouthSmileLeft: positive * (0.2 + activation * 0.18) + asymmetry,
    mouthSmileRight: positive * (0.2 + activation * 0.18) - asymmetry,
    mouthDimpleLeft: positive * 0.1 + positiveEnergy * 0.1,
    mouthDimpleRight: positive * 0.1 + positiveEnergy * 0.08,
    cheekSquintLeft: positiveEnergy * 0.24,
    cheekSquintRight: positiveEnergy * 0.24,
    mouthPressLeft: negative * 0.19 + subduedNegative * 0.12,
    mouthPressRight: negative * 0.19 + subduedNegative * 0.12,
    mouthFrownLeft: negativeEnergy * 0.16 + subduedNegative * 0.06,
    mouthFrownRight: negativeEnergy * 0.16 + subduedNegative * 0.06,
    mouthStretchLeft: activation * 0.09 + negativeEnergy * 0.12,
    mouthStretchRight: activation * 0.09 + negativeEnergy * 0.12,
    mouthFunnel: activation * 0.07 + calm * positive * 0.035,
    mouthPucker: calm * positive * 0.045,
    mouthUpperUpLeft: negativeEnergy * 0.07,
    mouthUpperUpRight: negativeEnergy * 0.07,
    jawOpen: activation * (0.07 + intensity * 0.07),
    jawForward: negative * 0.075,
    eyeWideLeft: activation * (0.17 + negative * 0.06),
    eyeWideRight: activation * (0.17 + negative * 0.06),
    eyeBlinkLeft: calm * 0.17,
    eyeBlinkRight: calm * 0.17,
    eyeSquintLeft: negative * 0.12 + positiveEnergy * 0.08,
    eyeSquintRight: negative * 0.12 + positiveEnergy * 0.08,
    browInnerUp: activation * positive * 0.12,
    browOuterUpLeft: activation * 0.08 + asymmetry,
    browOuterUpRight: activation * 0.08 - asymmetry,
    browDownLeft: negative * 0.11 + negativeEnergy * 0.13,
    browDownRight: negative * 0.11 + negativeEnergy * 0.13,
    noseSneerLeft: negativeEnergy * 0.055,
    noseSneerRight: negativeEnergy * 0.055,
  };
}

function interpolatedAffectWeights(x: number, y: number): ShapeWeights {
  const gx = ((Math.max(-1, Math.min(1, x)) + 1) / 2) * (PRECISION_GRID_SIZE - 1);
  const gy = ((Math.max(-1, Math.min(1, y)) + 1) / 2) * (PRECISION_GRID_SIZE - 1);
  const x0 = Math.floor(gx), x1 = Math.min(PRECISION_GRID_SIZE - 1, x0 + 1);
  const y0 = Math.floor(gy), y1 = Math.min(PRECISION_GRID_SIZE - 1, y0 + 1);
  const tx = gx - x0, ty = gy - y0;
  const result: ShapeWeights = {};
  const samples: Array<[number, number, number]> = [
    [x0, y0, (1 - tx) * (1 - ty)], [x1, y0, tx * (1 - ty)],
    [x0, y1, (1 - tx) * ty], [x1, y1, tx * ty],
  ];
  for (const [col, row, factor] of samples) {
    const sx = (col / (PRECISION_GRID_SIZE - 1)) * 2 - 1;
    const sy = (row / (PRECISION_GRID_SIZE - 1)) * 2 - 1;
    addWeights(result, affectAnchorWeights(sx, sy), factor);
  }
  return result;
}

function addWeights(target: ShapeWeights, source: ShapeWeights, factor: number) {
  for (const [name, value] of Object.entries(source)) {
    target[name] = (target[name] ?? 0) + value * factor;
  }
}

function semanticMoodWeights(x: number, y: number): ShapeWeights {
  const gx = Math.max(0, Math.min(MOOD_GRID_COLUMNS - 1, ((x + 1) / 2) * MOOD_GRID_COLUMNS - 0.5));
  const gy = Math.max(0, Math.min(MOOD_GRID_ROWS - 1, ((1 - y) / 2) * MOOD_GRID_ROWS - 0.5));
  const x0 = Math.floor(gx), x1 = Math.min(MOOD_GRID_COLUMNS - 1, x0 + 1);
  const y0 = Math.floor(gy), y1 = Math.min(MOOD_GRID_ROWS - 1, y0 + 1);
  const tx = gx - x0, ty = gy - y0;
  const result: ShapeWeights = {};
  for (const [col, row, factor] of [
    [x0, y0, (1 - tx) * (1 - ty)], [x1, y0, tx * (1 - ty)],
    [x0, y1, (1 - tx) * ty], [x1, y1, tx * ty],
  ] as Array<[number, number, number]>) {
    const mood = MOOD_GRID[row * MOOD_GRID_COLUMNS + col];
    const expression = mood?.primaryExpression ?? "neutral";
    addWeights(result, EXPRESSION_SHAPES[expression] ?? {}, factor * 0.28);
    addWeights(result, MOOD_SHAPE_OVERRIDES[mood?.key ?? ""] ?? {}, factor);
  }
  return result;
}

function expressionWeights(props: ExpressionHeadPreviewProps): ShapeWeights {
  const scale = Math.max(0, Math.min(1.5, props.expressionScale));
  const secondaryWeight = props.secondaryExpression === "none"
    ? 0
    : Math.max(0, Math.min(1, props.secondaryWeight));
  const result: ShapeWeights = {};
  // Bilinear blending across the semantic 10×5 field removes expression jumps at cell boundaries.
  addWeights(result, semanticMoodWeights(props.affectX, props.affectY), scale * (1 - secondaryWeight));
  if (secondaryWeight > 0) {
    addWeights(result, EXPRESSION_SHAPES[props.secondaryExpression] ?? {}, scale * secondaryWeight);
  }
  addWeights(result, interpolatedAffectWeights(props.affectX, props.affectY), scale);
  // ICT-FaceKit splits two formerly bilateral ARKit channels into independent sides.
  // Keep both spellings in one contract so the MPFB fallback and ICT head render identically.
  result.browInnerUpLeft = result.browInnerUp ?? 0;
  result.browInnerUpRight = result.browInnerUp ?? 0;
  result.cheekPuffLeft = result.cheekPuff ?? 0;
  result.cheekPuffRight = result.cheekPuff ?? 0;
  for (const name of Object.keys(result)) result[name] = clampWeight(result[name]);
  return result;
}

const RIG_PROMPT_PAIRS: Array<[string, string, string]> = [
  ["browDownLeft", "browDownRight", "brows pulled down and inward"],
  ["browOuterUpLeft", "browOuterUpRight", "outer brows raised"],
  ["eyeBlinkLeft", "eyeBlinkRight", "upper eyelids lowered"],
  ["eyeSquintLeft", "eyeSquintRight", "eyes narrowed"],
  ["eyeWideLeft", "eyeWideRight", "eyes opened wide"],
  ["eyeLookUpLeft", "eyeLookUpRight", "gaze directed upward"],
  ["eyeLookDownLeft", "eyeLookDownRight", "gaze directed downward"],
  ["eyeLookInLeft", "eyeLookInRight", "gaze converged toward the viewer"],
  ["eyeLookOutLeft", "eyeLookOutRight", "gaze averted outward"],
  ["cheekSquintLeft", "cheekSquintRight", "cheeks raised beneath the eyes"],
  ["mouthSmileLeft", "mouthSmileRight", "mouth corners pulled upward"],
  ["mouthFrownLeft", "mouthFrownRight", "mouth corners pulled downward"],
  ["mouthDimpleLeft", "mouthDimpleRight", "mouth corners tightened into dimples"],
  ["mouthPressLeft", "mouthPressRight", "lips pressed together"],
  ["mouthStretchLeft", "mouthStretchRight", "mouth stretched horizontally"],
  ["mouthUpperUpLeft", "mouthUpperUpRight", "upper lip raised"],
  ["mouthLowerDownLeft", "mouthLowerDownRight", "lower lip pulled downward"],
  ["noseSneerLeft", "noseSneerRight", "nose wrinkled and nostrils lifted"],
];

const RIG_PROMPT_SINGLES: Record<string, string> = {
  browInnerUp: "inner brows raised", cheekPuff: "cheeks inflated", jawOpen: "jaw opened",
  jawForward: "jaw pushed forward", jawLeft: "jaw shifted left", jawRight: "jaw shifted right",
  mouthClose: "mouth actively closed", mouthFunnel: "lips funneled forward", mouthPucker: "lips puckered",
  mouthRollLower: "lower lip rolled inward", mouthRollUpper: "upper lip rolled inward",
  mouthShrugLower: "lower lip lifted", mouthShrugUpper: "upper lip compressed upward",
  mouthLeft: "mouth shifted left", mouthRight: "mouth shifted right",
};

function rigIntensity(value: number): string {
  if (value >= 1.05) return "extreme";
  if (value >= 0.72) return "strong";
  if (value >= 0.42) return "clear";
  if (value >= 0.2) return "subtle";
  return "trace";
}

export interface ExpressionRigContract {
  weights: Record<string, number>;
  features: string[];
  prompt: string;
  revision: string;
}

/** Single source of truth shared by the WebGL preview and generation prompt. */
export function compileExpressionRigContract(affectX: number, affectY: number, scale = 1): ExpressionRigContract {
  const weights = expressionWeights({
    affectX, affectY, expressionScale: scale,
    primaryExpression: "neutral", secondaryExpression: "none", secondaryWeight: 0,
  });
  const scored: Array<{ score: number; text: string }> = [];
  for (const [leftName, rightName, description] of RIG_PROMPT_PAIRS) {
    const left = weights[leftName] ?? 0;
    const right = weights[rightName] ?? 0;
    const score = Math.max(left, right);
    if (score < 0.055) continue;
    const asymmetry = Math.abs(left - right) >= 0.09
      ? `, visibly asymmetric (left ${left.toFixed(2)}, right ${right.toFixed(2)})`
      : `, balanced on both sides (${((left + right) / 2).toFixed(2)})`;
    scored.push({ score, text: `${rigIntensity(score)} ${description}${asymmetry}` });
  }
  for (const [name, description] of Object.entries(RIG_PROMPT_SINGLES)) {
    const score = weights[name] ?? 0;
    if (score >= 0.055) scored.push({ score, text: `${rigIntensity(score)} ${description} (${score.toFixed(2)})` });
  }
  const features = scored.sort((a, b) => b.score - a.score).slice(0, 18).map((item) => item.text);
  const roundedWeights = Object.fromEntries(Object.entries(weights)
    .filter(([, value]) => value >= 0.025)
    .map(([name, value]) => [name, Number(value.toFixed(3))]));
  // Short uplink-friendly summary: Image 2 already carries full geometry.
  // Keep only the top cues so dual-layer compact does not drown the edit intent.
  const topFeatures = features.slice(0, 5);
  return {
    weights: roundedWeights,
    features,
    revision: "expression-rig-contract.v2",
    prompt: topFeatures.length > 0 ? topFeatures.join("; ") : "",
  };
}

function applyPose(rig: HeadRig, props: ExpressionHeadPreviewProps, yaw: number) {
  rig.targetWeights = expressionWeights(props);
  rig.targetPose = {
    pitch: props.affectY * -2.5,
    yaw: yaw + props.affectX * 4,
    roll: props.affectX * -1.5,
  };
}

function advanceRig(rig: HeadRig, deltaTime: number) {
  const alpha = 1 - Math.exp(-Math.max(0, deltaTime) * 18);
  const changedNames = new Set<string>();
  for (const name of CONTROLLED_SHAPES) {
    const current = rig.currentWeights[name] ?? 0;
    const target = rig.targetWeights[name] ?? 0;
    const next = Math.abs(target - current) < 0.0005 ? target : current + (target - current) * alpha;
    if (next !== current) {
      rig.currentWeights[name] = next;
      changedNames.add(name);
    }
  }
  if (changedNames.size > 0) {
    for (const binding of rig.bindings) {
      let bindingChanged = false;
      for (const [shape, targets] of binding.names) {
        if (!changedNames.has(shape)) continue;
        const value = rig.currentWeights[shape] ?? 0;
        for (const target of targets) binding.morph.setWeight(target, value);
        bindingChanged = true;
      }
      if (bindingChanged) binding.morph.update();
    }
  }
  const pose = rig.currentPose;
  const targetPose = rig.targetPose;
  pose.pitch += (targetPose.pitch - pose.pitch) * alpha;
  pose.yaw += (targetPose.yaw - pose.yaw) * alpha;
  pose.roll += (targetPose.roll - pose.roll) * alpha;
  rig.avatar.setLocalEulerAngles(pose.pitch, pose.yaw, pose.roll);
}

export function ExpressionHeadPreview(props: ExpressionHeadPreviewProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const statusRef = useRef<HTMLDivElement | null>(null);
  const rigRef = useRef<HeadRig | null>(null);
  const latestPropsRef = useRef(props);
  const pausedRef = useRef(Boolean(props.paused));
  const dragRef = useRef<{ pointerId: number; x: number; yaw: number } | null>(null);
  const [yaw, setYaw] = useState(0);
  const [status, setStatus] = useState<"loading" | "ready" | "failed">("loading");
  latestPropsRef.current = props;
  pausedRef.current = Boolean(props.paused);

  useEffect(() => {
    props.onCanvasReady?.(canvasRef.current);
    return () => props.onCanvasReady?.(null);
  }, [props.onCanvasReady]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    let cancelled = false;
    let rig: HeadRig | null = null;

    void import("playcanvas").then((pc) => {
      if (cancelled) return;
      const app = new pc.Application(canvas, {
        graphicsDeviceOptions: { alpha: true, antialias: true, preserveDrawingBuffer: true, powerPreference: "high-performance" },
      });
      app.setCanvasFillMode(pc.FILLMODE_NONE);
      app.setCanvasResolution(pc.RESOLUTION_AUTO);
      app.scene.ambientLight = new pc.Color(0.58, 0.58, 0.58);
      app.scene.exposure = 1.02;

      const camera = new pc.Entity("expression-head-camera");
      camera.addComponent("camera", {
        clearColor: new pc.Color(0.025, 0.028, 0.035, 0),
        fov: 24,
      });
      // Full cranium + chin + a little neck, matching LibTV's contained-head composition.
      // The lower crop stays above the shoulders, so no torso or arms enter frame.
      camera.setPosition(0, 1.68, 0.7);
      camera.lookAt(0, 1.68, 0.035);
      app.root.addChild(camera);

      const key = new pc.Entity("expression-head-key");
      key.addComponent("light", {
        type: "directional",
        color: new pc.Color(1, 1, 1),
        intensity: 1.45,
        castShadows: false,
      });
      key.setEulerAngles(28, 145, 0);
      app.root.addChild(key);
      const fill = new pc.Entity("expression-head-fill");
      fill.addComponent("light", {
        type: "omni",
        color: new pc.Color(0.72, 0.72, 0.72),
        intensity: 0.72,
        range: 4,
      });
      fill.setPosition(-1.1, 1.7, 1.2);
      app.root.addChild(fill);

      const resize = () => {
        const rect = canvas.getBoundingClientRect();
        app.graphicsDevice.maxPixelRatio = Math.min(window.devicePixelRatio || 1, 1.5);
        app.resizeCanvas(Math.max(1, rect.width), Math.max(1, rect.height));
      };
      const observer = new ResizeObserver(resize);
      observer.observe(canvas);
      resize();
      app.start();

      const asset = new pc.Asset("expression-head-ict-facekit", "container", {
        url: "/expression-head/ict-facekit-head.glb?v=ict57-headcrop-perf-4",
      });
      asset.once("load", () => {
        if (cancelled) return;
        type ContainerResource = { instantiateRenderEntity?: (options?: { castShadows?: boolean }) => Entity };
        const resource = asset.resource as ContainerResource | null;
        const avatar = resource?.instantiateRenderEntity?.({ castShadows: false });
        if (!avatar) {
          setStatus("failed");
          return;
        }
        avatar.name = "ict-facekit-expression-head";
        avatar.setLocalPosition(0, 0, 0);
        const renders = avatar.findComponents("render") as import("playcanvas").RenderComponent[];
        const makeMaterial = (tone: number, gloss: number, specular: number) => {
          const material = new pc.StandardMaterial();
          material.diffuse = new pc.Color(tone, tone, tone);
          material.specular = new pc.Color(specular, specular, specular);
          material.gloss = gloss;
          material.metalness = 0;
          material.update();
          return material;
        };
        const clay = makeMaterial(0.7, 0.36, 0.14);
        const eye = makeMaterial(0.26, 0.82, 0.48);
        const detail = makeMaterial(0.09, 0.24, 0.08);
        const teeth = makeMaterial(0.86, 0.32, 0.12);
        const innerMouth = makeMaterial(0.3, 0.28, 0.08);
        const ownedMaterials = [clay, eye, detail, teeth, innerMouth];
        for (const render of renders) {
          for (const meshInstance of Array.from(render.meshInstances ?? [])) {
            const sourceMaterial = (meshInstance.material?.name ?? "").toLowerCase();
            meshInstance.material = sourceMaterial.includes("iris")
              || sourceMaterial.includes("occlusion")
              || sourceMaterial.includes("lash") ? detail
              : sourceMaterial.includes("sclera") || sourceMaterial.includes("lacrimal") ? eye
                : sourceMaterial.includes("teeth") ? teeth
                  : sourceMaterial.includes("gum") || sourceMaterial.includes("tongue") ? innerMouth
                    : clay;
          }
        }
        const meshInstances = renders.flatMap((render) => Array.from(render.meshInstances ?? []));
        app.root.addChild(avatar);
        const morphs = meshInstances
          .map((meshInstance) => meshInstance.morphInstance)
          .filter((morph): morph is MorphInstance => Boolean(morph));
        const bindings: MorphBinding[] = morphs.map((morph) => ({
          morph,
          names: resolveMorphBindingNames(new Set(morph.morph.targets.map((target) => target.name))),
        }));
        const initialPose = { pitch: 0, yaw: 0, roll: 0 };
        rig = {
          app,
          avatar,
          bindings,
          currentWeights: {},
          targetWeights: {},
          currentPose: { ...initialPose },
          targetPose: { ...initialPose },
          dispose: () => {
            observer.disconnect();
            avatar.destroy();
            for (const material of ownedMaterials) material.destroy();
            app.assets.remove(asset);
            app.destroy();
          },
        };
        const onRigUpdate = (deltaTime: number) => {
          if (rig && !pausedRef.current) advanceRig(rig, deltaTime);
        };
        app.on("update", onRigUpdate);
        rigRef.current = rig;
        app.autoRender = !pausedRef.current;
        applyPose(rig, latestPropsRef.current, 0);
        setStatus("ready");
      });
      asset.once("error", (error: unknown) => {
        console.error("[expression] realistic head asset failed", error);
        if (!cancelled) setStatus("failed");
      });
      app.assets.add(asset);
      app.assets.load(asset);
    }).catch((error) => {
      console.error("[expression] PlayCanvas failed", error);
      if (!cancelled) setStatus("failed");
    });

    return () => {
      cancelled = true;
      rigRef.current = null;
      rig?.dispose();
    };
  }, []);

  useEffect(() => {
    if (rigRef.current) applyPose(rigRef.current, props, yaw);
  }, [props.affectX, props.affectY, props.expressionScale, props.primaryExpression, props.secondaryExpression, props.secondaryWeight, yaw]);

  useLayoutEffect(() => {
    const app = rigRef.current?.app;
    if (!app) return;
    app.autoRender = !props.paused;
    if (!props.paused) app.renderNextFrame = true;
  }, [props.paused]);

  return (
    <div className="relative h-full w-full overflow-hidden">
      <canvas
        ref={canvasRef}
        aria-label="实时 3D 真人表情人头预览"
        className="h-full w-full touch-none cursor-grab active:cursor-grabbing"
        onPointerDown={(event) => {
          event.currentTarget.setPointerCapture(event.pointerId);
          dragRef.current = { pointerId: event.pointerId, x: event.clientX, yaw };
        }}
        onPointerMove={(event) => {
          const drag = dragRef.current;
          if (!drag || drag.pointerId !== event.pointerId) return;
          setYaw(Math.max(-38, Math.min(38, drag.yaw + (event.clientX - drag.x) * 0.35)));
        }}
        onPointerUp={(event) => {
          if (dragRef.current?.pointerId === event.pointerId) dragRef.current = null;
        }}
      />
      <div ref={statusRef} title={status === "ready" ? "57-MORPH · 441-GRID" : undefined} className={`pointer-events-none absolute bottom-2 right-2 rounded-full px-2 py-0.5 text-[9px] backdrop-blur transition-opacity duration-200 ${status === "failed" ? "bg-rose-950/70 text-rose-200" : status === "loading" ? "bg-black/45 text-white/60" : "hidden"}`}>
        {status === "failed" ? "头部资产加载失败" : "加载头部…"}
      </div>
    </div>
  );
}
