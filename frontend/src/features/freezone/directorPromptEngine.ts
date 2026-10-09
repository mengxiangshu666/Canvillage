// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/** Clean-room prompt compiler distilled from the user's AIGC director knowledge base. */

export interface DirectorStylePack {
  id: string;
  name: string;
  category: string;
  medium: string;
  linework: string;
  shading: string;
  palette: string;
  texture: string;
  lighting: string;
  composition: string;
  negative: string[];
}

export interface DirectorControls {
  styleId: string;
  shot: string;
  lens: string;
  lighting: string;
  paletteOverride: string;
  textureOverride: string;
  identityLock: boolean;
  continuityLock: boolean;
}

export interface CompiledDirectorPrompt {
  prompt: string;
  negative: string[];
  profile: string;
  revision: string;
  warnings: string[];
  layers: Record<string, string>;
}

export interface DirectorPromptNodeData {
  prompt?: unknown;
  compiledPromptPreview?: unknown;
  director_source_prompt?: unknown;
}

export const DIRECTOR_PROMPT_REVISION = "xiaoshu-director-compiler.v2-unlimited";

/**
 * Return the editable source prompt behind a compiled node.
 *
 * When the visible prompt still equals our last compiled preview, the saved
 * source is authoritative. If the user edited the prompt afterwards, the new
 * text becomes the source. This makes repeated Style-DNA application
 * idempotent without hiding genuine user edits.
 */
export function directorSourcePromptFromNodeData(
  data: DirectorPromptNodeData | null | undefined,
  fallback = "Describe the intended subject, action and story beat.",
): string {
  const prompt = typeof data?.prompt === "string" ? data.prompt.trim() : "";
  const compiled = typeof data?.compiledPromptPreview === "string"
    ? data.compiledPromptPreview.trim()
    : "";
  const source = typeof data?.director_source_prompt === "string"
    ? data.director_source_prompt.trim()
    : "";
  if (prompt && (!compiled || prompt !== compiled)) return prompt;
  if (source) return source;
  return prompt || fallback;
}

export const DIRECTOR_STYLES: DirectorStylePack[] = [
  { id: "cinematic-realism", name: "电影写实", category: "写实/纪实", medium: "cinematic photorealism", linework: "no visible illustration linework", shading: "natural photographic tonal rolloff", palette: "controlled cinematic color, restrained saturation", texture: "detailed skin texture, subtle film grain", lighting: "motivated key light, practical ambience, dimensional rim separation", composition: "cinematic blocking, foreground-midground-background depth", negative: ["anime", "cartoon", "illustration", "plastic skin", "CGI look"] },
  { id: "chinese-animation", name: "国风动画", category: "二维动画", medium: "high-end Chinese animation film still", linework: "clean expressive linework", shading: "refined cel shading with painterly gradients", palette: "mineral pigments, jade green, cinnabar and muted gold", texture: "subtle rice-paper grain", lighting: "atmospheric volumetric light", composition: "layered Chinese landscape depth and elegant negative space", negative: ["photorealistic", "western cartoon", "cheap mobile-game render", "muddy colors"] },
  { id: "anime-cinematic", name: "日系电影动画", category: "二维动画", medium: "cinematic anime film frame", linework: "precise thin linework", shading: "clean cel shading with soft atmospheric gradients", palette: "harmonious seasonal palette", texture: "clean painted background, subtle film grain", lighting: "expressive natural light and luminous rim light", composition: "story-driven anime cinematography, clear silhouette", negative: ["photorealistic", "3D render", "western cartoon", "chibi", "oversized eyes"] },
  { id: "ink-wash", name: "水墨电影", category: "传统媒介", medium: "Chinese ink wash on xuan paper", linework: "expressive calligraphic brush line", shading: "layered ink wash and controlled dry brush", palette: "black ink, warm paper, one restrained accent color", texture: "xuan paper fibers, ink bleeding and dry-brush texture", lighting: "suggested light through tonal emptiness", composition: "poetic negative space and scroll-like depth", negative: ["3D render", "neon", "plastic", "photographic clutter", "hard digital edges"] },
  { id: "watercolor-story", name: "水彩绘本", category: "插画", medium: "watercolor storybook illustration", linework: "delicate pencil underdrawing", shading: "transparent layered watercolor washes", palette: "soft harmonious pastel palette", texture: "cold-press watercolor paper grain", lighting: "gentle diffuse daylight", composition: "intimate storybook framing", negative: ["photorealistic", "3D render", "harsh contrast", "vector-flat", "plastic texture"] },
  { id: "painterly-3d", name: "笔触化3D", category: "风格化3D", medium: "painterly 3D animated-film render", linework: "lineless illustrated silhouette", shading: "stylized global illumination with brush-like value grouping", palette: "curated production-design palette", texture: "hand-painted surfaces and illustrated brush texture", lighting: "stylized cinematic key and volumetric atmosphere", composition: "animated-feature staging with readable depth", negative: ["raw PBR demo", "uncanny realism", "flat cel shading", "cheap game asset", "over-sharpening"] },
  { id: "cyberpunk-neon", name: "赛博霓虹", category: "科幻", medium: "cinematic cyberpunk photography", linework: "sharp architectural edges", shading: "deep contrast with controlled neon bloom", palette: "cyan-magenta neon over dark neutral base", texture: "wet asphalt, glass, brushed metal, restrained digital noise", lighting: "motivated neon practicals, rim light, volumetric haze", composition: "dense layered city depth, strong leading lines", negative: ["rainbow color soup", "generic sci-fi armor", "illegible signage", "oversaturated skin", "flat lighting"] },
  { id: "documentary", name: "纪录片真实", category: "写实/纪实", medium: "observational documentary photography", linework: "natural photographic edges", shading: "available-light tonal range", palette: "honest local color, restrained grade", texture: "natural grain and imperfect real surfaces", lighting: "available natural light, no studio glamour", composition: "unobtrusive human-scale framing, lived-in environment", negative: ["beauty retouch", "heroic posing", "fantasy light", "commercial polish", "plastic skin"] },
  { id: "film-noir", name: "黑色电影", category: "类型片", medium: "classic film-noir cinematography", linework: "hard silhouette edges", shading: "low-key chiaroscuro, deep blacks", palette: "monochrome silver gelatin with selective warm practical", texture: "35mm grain, smoke and wet street reflections", lighting: "hard side key, venetian-blind shadows, 8:1 contrast", composition: "oblique framing, layered shadows, moral distance", negative: ["flat front light", "pastel", "cheerful commercial look", "clean digital video", "high-key lighting"] },
  { id: "luxury-commercial", name: "高端商业广告", category: "商业", medium: "premium editorial advertising photography", linework: "clean luxury silhouette", shading: "polished dimensional tonal control", palette: "minimal premium palette with one brand accent", texture: "tactile premium materials, controlled micro-detail", lighting: "large soft key, precise specular highlights, elegant rim", composition: "intentional negative space, product-and-character hierarchy", negative: ["cheap e-commerce look", "clutter", "mixed color temperature", "over-retouched skin", "random props"] },
];

export const DEFAULT_DIRECTOR_CONTROLS: DirectorControls = {
  styleId: "cinematic-realism",
  shot: "medium close-up, eye-level",
  lens: "50mm prime lens",
  lighting: "motivated soft key at 4500K, 4:1 contrast, subtle rim light",
  paletteOverride: "",
  textureOverride: "",
  identityLock: true,
  continuityLock: true,
};

function modelProfile(modelId: string): { id: string; referenceFirst: boolean; syntax: "natural" | "weighted" } {
  const id = modelId.toLowerCase();
  if (id.includes("gpt-image") || id.includes("image2")) return { id: "gpt-image-reference", referenceFirst: true, syntax: "natural" };
  if (id.includes("seedream") || id.includes("doubao")) return { id: "seedream-multi-subject", referenceFirst: true, syntax: "natural" };
  if (id.includes("midjourney") || id.includes("mj-")) return { id: "midjourney-style", referenceFirst: false, syntax: "weighted" };
  if (id.includes("flux")) return { id: "flux-concrete", referenceFirst: false, syntax: "natural" };
  return { id: "generic-reference", referenceFirst: true, syntax: "natural" };
}

export function compileDirectorPrompt(input: {
  basePrompt: string;
  modelId?: string | null;
  controls: DirectorControls;
  referenceDescription?: string;
}): CompiledDirectorPrompt {
  const style = DIRECTOR_STYLES.find((item) => item.id === input.controls.styleId) ?? DIRECTOR_STYLES[0];
  const profile = modelProfile(input.modelId ?? "");
  const palette = input.controls.paletteOverride.trim() || style.palette;
  const texture = input.controls.textureOverride.trim() || style.texture;
  const identity = input.controls.identityLock
    ? "IDENTITY LOCK: preserve exact facial identity, body proportions, age, hairstyle, costume and accessories from the reference."
    : "";
  const continuity = input.controls.continuityLock
    ? "CONTINUITY LOCK: preserve scene geography, props, screen direction, time of day and lighting logic across the sequence."
    : "";
  const reference = input.referenceDescription?.trim()
    ? `REFERENCE PRIORITY: ${input.referenceDescription.trim()}.`
    : "REFERENCE PRIORITY: use the attached image as the primary visual authority.";
  const structure = `STRUCTURE: ${input.controls.shot}; ${input.controls.lens}; ${style.composition}.`;
  const light = `LIGHTING: ${input.controls.lighting || style.lighting}.`;
  const art = `STYLE: ${style.medium}; ${style.linework}; ${style.shading}; palette: ${palette}; texture: ${texture}.`;
  const avoid = `AVOID: ${[...style.negative, "deformed hands", "extra fingers", "bad anatomy", "watermark", "unrequested text"].join(", ")}.`;
  const blocks = profile.referenceFirst
    ? [reference, identity, input.basePrompt.trim(), structure, light, art, continuity, avoid]
    : [input.basePrompt.trim(), structure, light, art, reference, identity, continuity, avoid];
  let prompt = blocks.filter(Boolean).join("\n");
  const warnings: string[] = [];
  // Never truncate or reject a user's prompt. Provider-specific context limits
  // belong to the execution adapter, not this authoring/compiler surface.
  if (/photoreal/i.test(style.medium) && /anime|cel shading/i.test(input.basePrompt)) warnings.push("检测到写实风格与动漫/赛璐璐描述冲突。");
  if (profile.syntax === "weighted") prompt += "\n--no " + style.negative.join(", ");
  return {
    prompt,
    negative: [...style.negative],
    profile: profile.id,
    revision: DIRECTOR_PROMPT_REVISION,
    warnings,
    layers: {
      format: style.category,
      medium: style.medium,
      linework: style.linework,
      shading: style.shading,
      palette,
      texture,
      lighting: input.controls.lighting || style.lighting,
      composition: `${input.controls.shot}; ${input.controls.lens}; ${style.composition}`,
    },
  };
}
