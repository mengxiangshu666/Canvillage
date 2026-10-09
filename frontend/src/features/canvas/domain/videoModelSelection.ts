import type { ModelOption } from "@/features/canvas/ui/ProviderModelPicker";

/**
 * Compatibility values used only when an older catalog entry omitted its
 * capability fields entirely. An explicit empty array from the model center
 * remains empty and therefore never resurrects unsupported controls.
 */
export const LEGACY_IMAGE_RESOLUTION_OPTIONS = ["1K", "2K", "4K"] as const;
export const LEGACY_IMAGE_ASPECT_RATIO_OPTIONS = ["1:1", "16:9", "9:16", "4:3", "3:4"] as const;

export function isLiveModel(model: ModelOption): boolean {
  return model.enabled !== false && model.disabled !== true;
}

/**
 * Match both current catalog ids and ids persisted by older canvas versions.
 * The UI stores the opaque catalog id, while historical result nodes may have
 * stored the API/upstream id directly. Resolving all three forms here keeps
 * every node action on the same live model instead of silently switching to a
 * different default.
 */
function modelMatchesPersistedId(model: ModelOption, persistedId: string): boolean {
  const requested = persistedId.trim().toLowerCase();
  if (!requested) return false;
  return [model.id, model.apiModel, model.upstreamModel]
    .filter((value): value is string => typeof value === "string")
    .some((value) => value.trim().toLowerCase() === requested);
}

/** Resolve an exact node binding even when the model is currently disabled.
 *
 * Execution selectors deliberately reject disabled models. Presentation and
 * diagnostics still need their capability contract so an explicit empty
 * declaration is not replaced by family defaults in the node UI.
 */
export function findPersistedModel(
  models: ModelOption[],
  persistedId: string | null,
): ModelOption | undefined {
  if (!persistedId) return undefined;
  return models.find((model) => modelMatchesPersistedId(model, persistedId));
}

export function selectLiveModel(
  models: ModelOption[],
  persistedId: string | null,
): ModelOption | undefined {
  if (persistedId) {
    const persisted = findPersistedModel(models, persistedId);
    // An explicit node binding is an invariant, not a hint.  If it is stale
    // or disabled, leave it unresolved so the caller can block the operation
    // and ask for a deliberate model choice instead of silently switching.
    if (persisted) return isLiveModel(persisted) ? persisted : undefined;
    return undefined;
  }
  return (
    models.find((model) => model.isDefault === true && isLiveModel(model))
    ?? models.find(isLiveModel)
  );
}

export const selectVideoModel = selectLiveModel;

/**
 * Pick a live image model that can consume a reference image.
 *
 * Editing actions (redraw, erase, outpaint, upscale, relight, multi-angle
 * and template grids) are image-to-image operations.  They must not inherit a
 * text-only image model merely because it is the model-center default: that
 * would leave the UI looking usable and fail only after the paid task starts.
 * Missing ``supportedModes`` is treated as legacy metadata and remains
 * compatible; an explicit list is authoritative.
 */
export function imageEditModelSupportsMode(
  model: Pick<ModelOption, "supportedModes" | "capabilitySource">,
): boolean {
  const modes = model.supportedModes;
  // An explicitly unknown contract must not be treated as a capable model.
  // Only truly legacy entries (which omitted the field altogether) retain the
  // compatibility behavior.
  if (modes === undefined) return model.capabilitySource !== "unknown";
  return modes.some((mode) => mode === "imageToImage" || mode === "image_to_image");
}

export function selectLiveImageEditModel(
  models: ModelOption[],
  persistedId: string | null,
): ModelOption | undefined {
  const candidates = models.filter(imageEditModelSupportsMode).filter(isLiveModel);
  if (persistedId) {
    const persisted = findPersistedModel(models, persistedId);
    // Keep an explicit binding exact.  A text-only, stale, or disabled model
    // must not be replaced by an unrelated edit-capable default.
    if (!persisted || !isLiveModel(persisted) || !imageEditModelSupportsMode(persisted)) {
      return undefined;
    }
    return persisted;
  }
  return candidates.find((model) => model.isDefault === true) ?? candidates[0];
}

/** Read a node's model reference without assuming one generation schema. */
export function persistedCanvasModelId(
  data: Record<string, unknown> | null | undefined,
): string | null {
  if (!data) return null;
  for (const key of ["model", "generationModelId", "upscaleModelId", "generationModel"]) {
    const value = data[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return null;
}

export function imageEditModelDisabledReason(
  model: Pick<ModelOption, "supportedModes" | "capabilitySource">,
): string | null {
  if (imageEditModelSupportsMode(model)) return null;
  return model.capabilitySource === "unknown" && model.supportedModes === undefined
    ? "上游图生图能力尚未确认，当前操作不会冒险提交"
    : "该模型仅支持文生图，当前操作需要图生图能力";
}

export function modelResolutionOptions(
  model: Pick<ModelOption, "resolutionOptions" | "capabilitySource"> | null | undefined,
): string[] {
  if (!model) return [];
  if (model.resolutionOptions !== undefined) {
    return [...new Set(model.resolutionOptions.filter((value) => value.trim().length > 0))];
  }
  // The model center uses an explicit `unknown` source when capability
  // discovery failed.  Advertising legacy presets in that state creates
  // controls that the upstream contract never confirmed.
  if (model.capabilitySource === "unknown") return [];
  return [...LEGACY_IMAGE_RESOLUTION_OPTIONS];
}

export function modelAspectRatioOptions(
  model: Pick<ModelOption, "aspectRatioOptions" | "capabilitySource"> | null | undefined,
): string[] {
  if (!model) return [];
  if (model.aspectRatioOptions !== undefined) {
    return [...new Set(model.aspectRatioOptions.filter((value) => value.trim().length > 0))];
  }
  if (model.capabilitySource === "unknown") return [];
  return [...LEGACY_IMAGE_ASPECT_RATIO_OPTIONS];
}

export function modelSupportsAspectRatio(
  model: Pick<ModelOption, "aspectRatioOptions" | "supportsCustomAspectRatio" | "capabilitySource"> | null | undefined,
  requested: string,
): boolean {
  const value = requested.trim();
  if (!value || value === "original") return true;
  return modelAspectRatioOptions(model).includes(value) || model?.supportsCustomAspectRatio === true;
}

export function resolveModelResolution(
  model: Pick<ModelOption, "resolutionOptions" | "supportsCustomResolution" | "parameterDefaults" | "capabilitySource"> | null | undefined,
  requested?: string | null,
): string | undefined {
  if (!model) return undefined;
  const options = modelResolutionOptions(model);
  const candidate = requested?.trim();
  if (candidate && (options.includes(candidate) || model.supportsCustomResolution === true)) {
    return candidate;
  }
  const declaredDefault = model.parameterDefaults?.resolution?.trim();
  if (declaredDefault && (options.includes(declaredDefault) || model.supportsCustomResolution === true)) {
    return declaredDefault;
  }
  return options[0];
}
