import type { ModelOption } from '@/features/canvas/ui/ProviderModelPicker';

import type {
  AspectRatioOption,
  ImageGenerationMode,
  ImageModelDefinition,
  ResolutionOption,
} from './types';

const COMMON_ASPECT_RATIOS: AspectRatioOption[] = [
  { value: '1:1', label: '1:1' },
  { value: '16:9', label: '16:9' },
  { value: '9:16', label: '9:16' },
  { value: '4:3', label: '4:3' },
  { value: '3:4', label: '3:4' },
];

const COMMON_RESOLUTIONS: ResolutionOption[] = [
  { value: '1K', label: '1K' },
  { value: '2K', label: '2K' },
  { value: '4K', label: '4K' },
];

const QUALITY_LABELS: Record<string, string> = {
  low: '低画质',
  medium: '标准画质',
  high: '高画质',
  auto: '自动',
};

export const UNCONFIGURED_IMAGE_MODEL: ImageModelDefinition = {
  id: '',
  mediaType: 'image',
  displayName: '未配置生图模型',
  providerId: 'direct',
  description: '请先在模型中心添加并检测生图模型。',
  eta: '未配置',
  // An empty contract is intentional: controls must not advertise presets
  // before the model center has supplied a live upstream capability list.
  defaultAspectRatio: '',
  defaultResolution: '',
  aspectRatios: [],
  resolutions: [],
  qualityOptions: [],
  supportsCustomAspectRatio: false,
  supportsCustomResolution: false,
  capabilitySource: 'unknown',
  supportedModes: ['text_to_image'],
  resolveRequest: () => ({ requestModel: '', modeLabel: '未配置' }),
};

function normalizeImageModes(model: ModelOption): ImageGenerationMode[] {
  // A missing field is an old catalog entry, so retain the legacy two-mode
  // behavior. Once the backend publishes an explicit list, preserve it
  // exactly; adding text-to-image here used to resurrect an unsupported mode
  // for image-edit-only upstream models.
  if (model.supportedModes == null && model.capabilitySource === 'unknown') {
    return [];
  }
  if (model.supportedModes == null) {
    return ['text_to_image', 'image_to_image'];
  }
  const modes = new Set(model.supportedModes);
  const result: ImageGenerationMode[] = [];
  if (modes.has('textToImage') || modes.has('text_to_image')) {
    result.push('text_to_image');
  }
  if (modes.has('imageToImage') || modes.has('image_to_image')) {
    result.push('image_to_image');
  }
  return result;
}

export function imageModelSupportsMode(
  model: Pick<ImageModelDefinition, 'supportedModes' | 'capabilitySource'>,
  mode: ImageGenerationMode,
): boolean {
  // An explicitly unknown runtime contract is not evidence of support.  Keep
  // the legacy permissive behavior only for catalogs that predate capability
  // fields altogether.
  if (model.supportedModes == null) return model.capabilitySource !== 'unknown';
  return model.supportedModes.includes(mode);
}

/** Adapt the live backend image catalog to the mature canvas control contract. */
export function runtimeImageModelFromOption(model: ModelOption): ImageModelDefinition {
  const supportedModes = normalizeImageModes(model);
  // ``undefined`` means an old catalog without capability fields.  An empty
  // array is an explicit upstream declaration and must remain empty; falling
  // back to common presets here resurrects controls the model said it lacks.
  const hasExplicitUnknownContract = model.capabilitySource === 'unknown';
  const aspectRatios = model.aspectRatioOptions == null
    ? hasExplicitUnknownContract
      ? []
      : COMMON_ASPECT_RATIOS
    : model.aspectRatioOptions.map((value) => ({ value, label: value }));
  const resolutions = model.resolutionOptions == null
    ? hasExplicitUnknownContract
      ? []
      : COMMON_RESOLUTIONS
    : model.resolutionOptions.map((value) => ({ value, label: value }));
  const qualityOptions = (model.qualityOptions ?? []).map((value) => ({
    value,
    label: QUALITY_LABELS[value] ?? value,
  }));
  const requestedAspectRatio = model.parameterDefaults?.aspectRatio ?? '1:1';
  const requestedResolution = model.parameterDefaults?.resolution ?? '2K';
  const defaultAspectRatio = aspectRatios.some(({ value }) => value === requestedAspectRatio)
    ? requestedAspectRatio
    : aspectRatios[0]?.value ?? '';
  const defaultResolution = resolutions.some(({ value }) => value === requestedResolution)
    ? requestedResolution
    : resolutions[0]?.value ?? '';
  // An absent field means the catalog predates advanced parameters; an empty
  // array is the model declaring it has none. Either way the panel stays
  // closed, but only the former may fall back to a schema the model never sent.
  const extraParamsSchema = model.advancedParamsSchema?.map((definition) => ({
    ...definition,
  }));
  return {
    id: model.id,
    mediaType: 'image',
    displayName: model.label,
    providerId: model.providerId,
    description: model.useCase ?? '按模型能力自动匹配图片生成参数。',
    eta: '按上游响应',
    defaultAspectRatio,
    defaultResolution,
    aspectRatios,
    resolutions,
    qualityOptions,
    supportsCustomAspectRatio: model.supportsCustomAspectRatio === true,
    supportsCustomResolution: model.supportsCustomResolution === true,
    capabilitySource: model.capabilitySource ?? 'unknown',
    supportedModes,
    ...(extraParamsSchema && extraParamsSchema.length > 0 ? { extraParamsSchema } : {}),
    ...(model.advancedParamDefaults && Object.keys(model.advancedParamDefaults).length > 0
      ? { defaultExtraParams: model.advancedParamDefaults }
      : {}),
    resolveRequest: ({ referenceImageCount }) => {
      const supportsTextToImage = supportedModes.includes('text_to_image');
      const supportsImageToImage = supportedModes.includes('image_to_image');
      return {
        requestModel: model.apiModel,
        modeLabel:
          referenceImageCount > 0 && supportsImageToImage
            ? '图生图'
            : supportsTextToImage
              ? '文生图'
              : '图生图',
      };
    },
  };
}
