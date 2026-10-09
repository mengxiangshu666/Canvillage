// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  fetchFreezoneJobResult,
  submitFreezoneMultiView,
  submitFreezoneOutpaint,
  submitFreezoneRedraw,
  submitFreezoneRelight,
  submitFreezoneScene360,
  submitFreezoneTemplateEdit,
  submitFreezoneUpscale,
  type FreezoneMultiViewPreset,
  type FreezoneMultiViewShotSize,
  type FreezoneOutpaintAspectRatio,
  type FreezoneRedrawAspectRatio,
  type FreezoneRelightKeyLightDirection,
  type FreezoneRelightScope,
  type FreezoneScene360AspectRatio,
  type FreezoneTemplateEditMode,
  type FreezoneUpscaleScaleFactor,
} from '@/api/ops';
import { CANVAS_NODE_TYPES } from '@/features/canvas/domain/canvasNodes';
import { awaitTaskCompletion } from '@/api/tasks';
import { readUrl } from '@/lib/url-params';
import { useCanvasStore } from '@/stores/canvasStore';
import { canvasAiGateway } from './canvasServices';
import { resolveErrorContent } from './errorDialog';
import { CURRENT_RUNTIME_SESSION_ID, extractRequestId } from './generationErrorReport';
import type { GenerateImagePayload } from './ports';
import { generationTaskDescriptor } from './resumeGeneration';

/**
 * Params persisted on an export node created by the 擦除 / 重绘 flow, so a failed
 * node can re-run its freezone `redraw` call without the overlay being mounted.
 */
interface FreezoneRedrawRequest {
  sourceUrl: string;
  maskUrl?: string | null;
  aspectRatio: string;
  imageSize?: string;
  prompt?: string;
  model?: string;
}

function readFreezoneRedrawRequest(
  data: Record<string, unknown>,
): FreezoneRedrawRequest | undefined {
  const req = data.freezoneRedrawRequest as Partial<FreezoneRedrawRequest> | undefined;
  if (!req || typeof req.sourceUrl !== 'string') {
    return undefined;
  }
  return {
    sourceUrl: req.sourceUrl,
    maskUrl: typeof req.maskUrl === 'string' ? req.maskUrl : null,
    aspectRatio: typeof req.aspectRatio === 'string' ? req.aspectRatio : 'original',
    imageSize: typeof req.imageSize === 'string' && req.imageSize.trim() ? req.imageSize : undefined,
    prompt: typeof req.prompt === 'string' ? req.prompt : '',
    model:
      typeof req.model === 'string'
        ? req.model
        : typeof data.generationModel === 'string'
          ? data.generationModel
          : undefined,
  };
}

interface FreezoneOutpaintRequest {
  sourceUrl: string;
  targetAspectRatio: FreezoneOutpaintAspectRatio;
  imageSize?: string;
  model?: string;
}

function readFreezoneOutpaintRequest(
  data: Record<string, unknown>,
): FreezoneOutpaintRequest | undefined {
  const req = data.freezoneOutpaintRequest as Partial<FreezoneOutpaintRequest> | undefined;
  if (!req || typeof req.sourceUrl !== 'string') return undefined;
  const ratios: readonly FreezoneOutpaintAspectRatio[] = [
    'original', '1:1', '4:3', '3:4', '16:9', '9:16',
  ];
  return {
    sourceUrl: req.sourceUrl,
    targetAspectRatio: ratios.includes(req.targetAspectRatio as FreezoneOutpaintAspectRatio)
      ? (req.targetAspectRatio as FreezoneOutpaintAspectRatio)
      : 'original',
    imageSize: typeof req.imageSize === 'string' && req.imageSize.trim() ? req.imageSize : undefined,
    model:
      typeof req.model === 'string'
        ? req.model
        : typeof data.generationModel === 'string'
          ? data.generationModel
          : undefined,
  };
}

interface FreezoneUpscaleRequest {
  sourceUrl: string;
  imageSize?: string;
  scaleFactor: FreezoneUpscaleScaleFactor;
  model?: string;
}

function readFreezoneUpscaleRequest(
  data: Record<string, unknown>,
): FreezoneUpscaleRequest | undefined {
  const req = data.freezoneUpscaleRequest as Partial<FreezoneUpscaleRequest> | undefined;
  if (!req || typeof req.sourceUrl !== 'string') return undefined;
  const scaleFactor = req.scaleFactor === 4 || req.scaleFactor === 6 ? req.scaleFactor : 2;
  return {
    sourceUrl: req.sourceUrl,
    imageSize: typeof req.imageSize === 'string' && req.imageSize.trim() ? req.imageSize : undefined,
    scaleFactor,
    model:
      typeof req.model === 'string'
        ? req.model
        : typeof data.generationModel === 'string'
          ? data.generationModel
          : undefined,
  };
}

interface FreezoneRelightRequest {
  sourceUrl: string;
  lightingReferenceUrl?: string | null;
  scope: FreezoneRelightScope;
  smartMode: boolean;
  brightness: number;
  colorHex: string;
  colorTemperatureKelvin?: number | null;
  keyLightDirection: FreezoneRelightKeyLightDirection;
  rimLight: boolean;
  prompt: string;
  imageSize?: string;
  model?: string;
}

function readFreezoneRelightRequest(
  data: Record<string, unknown>,
): FreezoneRelightRequest | undefined {
  const req = data.freezoneRelightRequest as Partial<FreezoneRelightRequest> | undefined;
  if (!req || typeof req.sourceUrl !== 'string') return undefined;
  const directions: readonly FreezoneRelightKeyLightDirection[] = [
    'left', 'top', 'right', 'front', 'bottom', 'back',
  ];
  return {
    sourceUrl: req.sourceUrl,
    lightingReferenceUrl: typeof req.lightingReferenceUrl === 'string' ? req.lightingReferenceUrl : null,
    scope: req.scope === 'local' ? 'local' : 'global',
    smartMode: req.smartMode === true,
    brightness: typeof req.brightness === 'number' ? req.brightness : 50,
    colorHex: typeof req.colorHex === 'string' ? req.colorHex : '#ffffff',
    colorTemperatureKelvin: typeof req.colorTemperatureKelvin === 'number' ? req.colorTemperatureKelvin : null,
    keyLightDirection: directions.includes(req.keyLightDirection as FreezoneRelightKeyLightDirection)
      ? (req.keyLightDirection as FreezoneRelightKeyLightDirection)
      : 'front',
    rimLight: req.rimLight === true,
    prompt: typeof req.prompt === 'string' ? req.prompt : '',
    imageSize: typeof req.imageSize === 'string' && req.imageSize.trim() ? req.imageSize : undefined,
    model:
      typeof req.model === 'string'
        ? req.model
        : typeof data.generationModel === 'string'
          ? data.generationModel
          : undefined,
  };
}

interface FreezoneMultiViewRequest {
  sourceUrl: string;
  preset: FreezoneMultiViewPreset;
  yawDegrees: number;
  pitchDegrees: number;
  shotSize: FreezoneMultiViewShotSize;
  prompt: string;
  imageSize?: string;
  model?: string;
}

function readFreezoneMultiViewRequest(
  data: Record<string, unknown>,
): FreezoneMultiViewRequest | undefined {
  const req = data.freezoneMultiViewRequest as Partial<FreezoneMultiViewRequest> | undefined;
  if (!req || typeof req.sourceUrl !== 'string') return undefined;
  const presets: readonly FreezoneMultiViewPreset[] = [
    'custom', 'fisheye', 'oblique', 'front', 'front_up', 'full_body', 'back',
  ];
  const shotSizes: readonly FreezoneMultiViewShotSize[] = [
    'extreme_close_up', 'close_up', 'medium_close', 'medium', 'full_body', 'wide', 'extreme_wide',
  ];
  return {
    sourceUrl: req.sourceUrl,
    preset: presets.includes(req.preset as FreezoneMultiViewPreset)
      ? (req.preset as FreezoneMultiViewPreset)
      : 'custom',
    yawDegrees: typeof req.yawDegrees === 'number' ? req.yawDegrees : 0,
    pitchDegrees: typeof req.pitchDegrees === 'number' ? req.pitchDegrees : 0,
    shotSize: shotSizes.includes(req.shotSize as FreezoneMultiViewShotSize)
      ? (req.shotSize as FreezoneMultiViewShotSize)
      : 'medium',
    prompt: typeof req.prompt === 'string' ? req.prompt : '',
    imageSize: typeof req.imageSize === 'string' && req.imageSize.trim() ? req.imageSize : undefined,
    model:
      typeof req.model === 'string'
        ? req.model
        : typeof data.generationModel === 'string'
          ? data.generationModel
          : undefined,
  };
}

interface FreezoneScene360Request {
  referenceUrl: string;
  aspectRatio: FreezoneScene360AspectRatio;
  imageSize?: string;
  model?: string;
}

function readFreezoneScene360Request(
  data: Record<string, unknown>,
  nodeId?: string,
): FreezoneScene360Request | undefined {
  const req = data.freezoneScene360Request as Partial<FreezoneScene360Request> | undefined;
  if (req && typeof req.referenceUrl === 'string' && req.referenceUrl.trim()) {
    return {
      referenceUrl: req.referenceUrl,
      aspectRatio: req.aspectRatio === '21:9' ? '21:9' : '2:1',
      imageSize: typeof req.imageSize === 'string' && req.imageSize.trim() ? req.imageSize : undefined,
      model:
        typeof req.model === 'string'
          ? req.model
          : typeof data.generationModel === 'string'
            ? data.generationModel
            : undefined,
    };
  }
  if (data.media_kind !== 'pano360' || !nodeId) {
    return undefined;
  }

  // Legacy panorama nodes predate request persistence; recover their source
  // from the incoming image edge so they remain retryable after an upgrade.
  const store = useCanvasStore.getState();
  const sourceId = store.edges.find((edge) => edge.target === nodeId)?.source;
  const sourceNode = sourceId ? store.nodes.find((candidate) => candidate.id === sourceId) : null;
  const sourceData = sourceNode?.data as Record<string, unknown> | undefined;
  const referenceUrl = [sourceData?.imageUrl, sourceData?.previewImageUrl, sourceData?.outputUrl].find(
    (value): value is string => typeof value === 'string' && value.trim().length > 0,
  );
  if (!referenceUrl) {
    return undefined;
  }
  return { referenceUrl: referenceUrl.split('?')[0], aspectRatio: '2:1' };
}

/** Retry a failed 擦除/重绘 export node by re-running its stored freezone redraw. */
async function regenerateFreezoneRedrawNode(
  nodeId: string,
  request: FreezoneRedrawRequest,
): Promise<void> {
  const store = useCanvasStore.getState();
  const project = readUrl().project;
  if (!project) {
    store.updateNodeData(nodeId, { generationError: '当前 URL 没有 project，无法重试' });
    return;
  }

  store.updateNodeData(nodeId, {
    isGenerating: true,
    generationStartedAt: Date.now(),
    generationError: null,
    generationErrorDetails: null,
    generationErrorRequestId: null,
    generationErrorStage: null,
    generationErrorSuggestedAction: null,
    generationErrorRetryable: null,
    generationRecoveryJobId: null,
    generationRecoveryTaskType: null,
  });

  try {
    const ref = await submitFreezoneRedraw(project, {
      sourceUrl: request.sourceUrl,
      maskUrl: request.maskUrl ?? null,
      prompt: request.prompt ?? '',
      aspectRatio: request.aspectRatio as FreezoneRedrawAspectRatio,
      numImages: 1,
      imageSize: request.imageSize,
      canvasId: readUrl().canvas ?? 'default',
      nodeId,
      ...(request.model ? { model: request.model } : {}),
    });
    useCanvasStore.getState().updateNodeData(nodeId, generationTaskDescriptor(ref));
    const completed = await awaitTaskCompletion(ref.task_key, project);
    const directUrl = completed.result?.['output_url'] as string | undefined;
    let url = directUrl;
    if (!url) {
      const fallback = await fetchFreezoneJobResult(project, ref.task_type, ref.job_id);
      url = fallback.url;
    }
    useCanvasStore.getState().updateNodeData(nodeId, {
      imageUrl: url,
      previewImageUrl: url,
      isGenerating: false,
      generationStartedAt: null,
      generationError: null,
      generationTaskKey: null,
      generationTaskType: null,
      generationTaskJobId: null,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.error('[regenerate] freezone redraw failed', error);
    useCanvasStore.getState().updateNodeData(nodeId, {
      isGenerating: false,
      generationStartedAt: null,
      generationError: message,
      generationTaskKey: null,
      generationTaskType: null,
      generationTaskJobId: null,
    });
  }
}

async function regenerateFreezoneOutpaintNode(
  nodeId: string,
  request: FreezoneOutpaintRequest,
): Promise<void> {
  const store = useCanvasStore.getState();
  const project = readUrl().project;
  if (!project) {
    store.updateNodeData(nodeId, { generationError: '当前 URL 没有 project，无法重试' });
    return;
  }
  store.updateNodeData(nodeId, {
    isGenerating: true,
    generationStartedAt: Date.now(),
    generationError: null,
    generationErrorDetails: null,
    generationErrorRequestId: null,
  });
  try {
    const ref = await submitFreezoneOutpaint(project, {
      sourceUrl: request.sourceUrl,
      targetAspectRatio: request.targetAspectRatio,
      numImages: 1,
      imageSize: request.imageSize,
      canvasId: readUrl().canvas ?? 'default',
      nodeId,
      ...(request.model ? { model: request.model } : {}),
    });
    store.updateNodeData(nodeId, generationTaskDescriptor(ref));
    const completed = await awaitTaskCompletion(ref.task_key, project);
    const url = (completed.result?.['output_url'] as string | undefined)
      || (await fetchFreezoneJobResult(project, ref.task_type, ref.job_id)).url;
    store.updateNodeData(nodeId, {
      imageUrl: url,
      previewImageUrl: url,
      isGenerating: false,
      generationStartedAt: null,
      generationError: null,
      generationTaskKey: null,
      generationTaskType: null,
      generationTaskJobId: null,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    store.updateNodeData(nodeId, {
      isGenerating: false,
      generationStartedAt: null,
      generationError: message,
      generationErrorDetails: null,
      generationErrorRequestId: null,
      generationTaskKey: null,
      generationTaskType: null,
      generationTaskJobId: null,
    });
  }
}

async function regenerateFreezoneUpscaleNode(
  nodeId: string,
  request: FreezoneUpscaleRequest,
): Promise<void> {
  const store = useCanvasStore.getState();
  const project = readUrl().project;
  if (!project) {
    store.updateNodeData(nodeId, { generationError: '当前 URL 没有 project，无法重试' });
    return;
  }
  store.updateNodeData(nodeId, {
    isGenerating: true,
    generationStartedAt: Date.now(),
    generationError: null,
    generationErrorDetails: null,
    generationErrorRequestId: null,
  });
  try {
    const ref = await submitFreezoneUpscale(project, {
      sourceUrl: request.sourceUrl,
      scaleFactor: request.scaleFactor,
      imageSize: request.imageSize,
      canvasId: readUrl().canvas ?? 'default',
      nodeId,
      ...(request.model ? { model: request.model } : {}),
    });
    store.updateNodeData(nodeId, generationTaskDescriptor(ref));
    const completed = await awaitTaskCompletion(ref.task_key, project);
    const url = (completed.result?.['output_url'] as string | undefined)
      || (await fetchFreezoneJobResult(project, ref.task_type, ref.job_id)).url;
    store.updateNodeData(nodeId, {
      imageUrl: url,
      previewImageUrl: url,
      isGenerating: false,
      generationStartedAt: null,
      generationError: null,
      generationTaskKey: null,
      generationTaskType: null,
      generationTaskJobId: null,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    store.updateNodeData(nodeId, {
      isGenerating: false,
      generationStartedAt: null,
      generationError: message,
      generationErrorDetails: null,
      generationErrorRequestId: null,
      generationTaskKey: null,
      generationTaskType: null,
      generationTaskJobId: null,
    });
  }
}

async function regenerateFreezoneRelightNode(
  nodeId: string,
  request: FreezoneRelightRequest,
): Promise<void> {
  const store = useCanvasStore.getState();
  const project = readUrl().project;
  if (!project) {
    store.updateNodeData(nodeId, { generationError: '当前 URL 没有 project，无法重试' });
    return;
  }
  store.updateNodeData(nodeId, {
    isGenerating: true,
    generationStartedAt: Date.now(),
    generationError: null,
    generationErrorDetails: null,
    generationErrorRequestId: null,
  });
  try {
    const ref = await submitFreezoneRelight(project, {
      sourceUrl: request.sourceUrl,
      lightingReferenceUrl: request.lightingReferenceUrl ?? null,
      scope: request.scope,
      smartMode: request.smartMode,
      brightness: request.brightness,
      colorHex: request.colorHex,
      colorTemperatureKelvin: request.colorTemperatureKelvin ?? null,
      keyLightDirection: request.keyLightDirection,
      rimLight: request.rimLight,
      prompt: request.prompt,
      imageSize: request.imageSize,
      canvasId: readUrl().canvas ?? 'default',
      nodeId,
      ...(request.model ? { model: request.model } : {}),
    });
    store.updateNodeData(nodeId, generationTaskDescriptor(ref));
    const completed = await awaitTaskCompletion(ref.task_key, project);
    const url = (completed.result?.['output_url'] as string | undefined)
      || (await fetchFreezoneJobResult(project, ref.task_type, ref.job_id)).url;
    store.updateNodeData(nodeId, {
      imageUrl: url,
      previewImageUrl: url,
      isGenerating: false,
      generationStartedAt: null,
      generationError: null,
      generationTaskKey: null,
      generationTaskType: null,
      generationTaskJobId: null,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    store.updateNodeData(nodeId, {
      isGenerating: false,
      generationStartedAt: null,
      generationError: message,
      generationErrorDetails: null,
      generationErrorRequestId: null,
      generationTaskKey: null,
      generationTaskType: null,
      generationTaskJobId: null,
    });
  }
}

async function regenerateFreezoneMultiViewNode(
  nodeId: string,
  request: FreezoneMultiViewRequest,
): Promise<void> {
  const store = useCanvasStore.getState();
  const project = readUrl().project;
  if (!project) {
    store.updateNodeData(nodeId, { generationError: '当前 URL 没有 project，无法重试' });
    return;
  }
  store.updateNodeData(nodeId, {
    isGenerating: true,
    generationStartedAt: Date.now(),
    generationError: null,
    generationErrorDetails: null,
    generationErrorRequestId: null,
  });
  try {
    const ref = await submitFreezoneMultiView(project, {
      sourceUrl: request.sourceUrl,
      preset: request.preset,
      yawDegrees: request.yawDegrees,
      pitchDegrees: request.pitchDegrees,
      shotSize: request.shotSize,
      prompt: request.prompt,
      imageSize: request.imageSize,
      canvasId: readUrl().canvas ?? 'default',
      nodeId,
      ...(request.model ? { model: request.model } : {}),
    });
    store.updateNodeData(nodeId, generationTaskDescriptor(ref));
    const completed = await awaitTaskCompletion(ref.task_key, project);
    const url = (completed.result?.['output_url'] as string | undefined)
      || (await fetchFreezoneJobResult(project, ref.task_type, ref.job_id)).url;
    store.updateNodeData(nodeId, {
      imageUrl: url,
      previewImageUrl: url,
      isGenerating: false,
      generationStartedAt: null,
      generationError: null,
      generationTaskKey: null,
      generationTaskType: null,
      generationTaskJobId: null,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    store.updateNodeData(nodeId, {
      isGenerating: false,
      generationStartedAt: null,
      generationError: message,
      generationErrorDetails: null,
      generationErrorRequestId: null,
      generationTaskKey: null,
      generationTaskType: null,
      generationTaskJobId: null,
    });
  }
}

function ensurePanoViewerNode(nodeId: string): void {
  const store = useCanvasStore.getState();
  const hasViewer = store.edges.some(
    (edge) =>
      edge.source === nodeId &&
      store.nodes.some(
        (candidate) =>
          candidate.id === edge.target && candidate.type === CANVAS_NODE_TYPES.pano360Viewer,
      ),
  );
  if (hasViewer) {
    return;
  }
  const position = store.findNodePosition(nodeId, 720, 420);
  const viewerNodeId = store.addNode(CANVAS_NODE_TYPES.pano360Viewer, position);
  store.addEdge(nodeId, viewerNodeId);
}

/** Retry a failed single-reference 360 panorama and restore its viewer. */
async function regenerateFreezoneScene360Node(
  nodeId: string,
  request: FreezoneScene360Request,
): Promise<void> {
  const store = useCanvasStore.getState();
  const project = readUrl().project;
  if (!project) {
    store.updateNodeData(nodeId, { generationError: '当前 URL 没有 project，无法重试' });
    return;
  }

  store.updateNodeData(nodeId, {
    isGenerating: true,
    generationStartedAt: Date.now(),
    generationError: null,
    generationTaskKey: null,
    generationTaskType: null,
    generationTaskJobId: null,
  });

  try {
    const ref = await submitFreezoneScene360(project, {
      referenceUrl: request.referenceUrl,
      aspectRatio: request.aspectRatio,
      mode: 'candidate',
      canvasId: readUrl().canvas ?? 'default',
      nodeId,
      ...(request.model ? { model: request.model } : {}),
    });
    store.updateNodeData(nodeId, generationTaskDescriptor(ref));
    const completed = await awaitTaskCompletion(ref.task_key, project);
    const directUrl = completed.result?.['output_url'] as string | undefined;
    const url = directUrl || (await fetchFreezoneJobResult(project, ref.task_type, ref.job_id)).url;
    useCanvasStore.getState().updateNodeData(nodeId, {
      imageUrl: url,
      previewImageUrl: url,
      output_role: 'scene_360_candidate',
      media_kind: 'pano360',
      isGenerating: false,
      generationStartedAt: null,
      generationError: null,
      generationTaskKey: null,
      generationTaskType: null,
      generationTaskJobId: null,
    });
    ensurePanoViewerNode(nodeId);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.error('[regenerate] scene-360 generation failed', error);
    useCanvasStore.getState().updateNodeData(nodeId, {
      isGenerating: false,
      generationStartedAt: null,
      generationError: message,
      generationErrorDetails: null,
      generationErrorRequestId: null,
      generationTaskKey: null,
      generationTaskType: null,
      generationTaskJobId: null,
    });
  }
}

interface FreezoneTemplateEditRequest {
  sourceUrl: string;
  mode: FreezoneTemplateEditMode;
  prompt: string;
  imageSize?: string;
  model?: string;
}

function readFreezoneTemplateEditRequest(
  data: Record<string, unknown>,
): FreezoneTemplateEditRequest | undefined {
  const req = data.freezoneTemplateEditRequest as Partial<FreezoneTemplateEditRequest> | undefined;
  if (!req || typeof req.sourceUrl !== 'string' || typeof req.mode !== 'string') {
    return undefined;
  }
  const validModes: readonly FreezoneTemplateEditMode[] = [
    'multi_camera_nine_grid',
    'story_pitch_four_grid',
    'character_face_three_view',
    'product_three_view',
    'storyboard_25_grid',
    'cinematic_light_correction',
    'character_three_view_generation',
    'image_projection_after_3s',
    'image_projection_before_5s',
  ];
  if (!validModes.includes(req.mode as FreezoneTemplateEditMode)) return undefined;
  return {
    sourceUrl: req.sourceUrl,
    mode: req.mode as FreezoneTemplateEditMode,
    prompt: typeof req.prompt === 'string' ? req.prompt : '',
    imageSize: typeof req.imageSize === 'string' && req.imageSize.trim() ? req.imageSize : undefined,
    model:
      typeof req.model === 'string'
        ? req.model
        : typeof data.generationModel === 'string'
          ? data.generationModel
          : undefined,
  };
}

/** Retry a failed template/grid action with the exact original model binding. */
async function regenerateFreezoneTemplateEditNode(
  nodeId: string,
  request: FreezoneTemplateEditRequest,
): Promise<void> {
  const store = useCanvasStore.getState();
  const project = readUrl().project;
  if (!project) {
    store.updateNodeData(nodeId, { generationError: '当前 URL 没有 project，无法重试' });
    return;
  }

  store.updateNodeData(nodeId, {
    isGenerating: true,
    generationStartedAt: Date.now(),
    generationError: null,
    generationTaskKey: null,
    generationTaskType: null,
    generationTaskJobId: null,
  });

  try {
    const ref = await submitFreezoneTemplateEdit(project, {
      sourceUrl: request.sourceUrl,
      mode: request.mode,
      prompt: request.prompt,
      imageSize: request.imageSize,
      canvasId: readUrl().canvas ?? 'default',
      nodeId,
      ...(request.model ? { model: request.model } : {}),
    });
    store.updateNodeData(nodeId, generationTaskDescriptor(ref));
    const completed = await awaitTaskCompletion(ref.task_key, project);
    const directUrl = completed.result?.['output_url'] as string | undefined;
    const url = directUrl || (await fetchFreezoneJobResult(project, ref.task_type, ref.job_id)).url;
    store.updateNodeData(nodeId, {
      imageUrl: url,
      previewImageUrl: url,
      isGenerating: false,
      generationStartedAt: null,
      generationError: null,
      generationTaskKey: null,
      generationTaskType: null,
      generationTaskJobId: null,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.error('[regenerate] freezone template edit failed', error);
    store.updateNodeData(nodeId, {
      isGenerating: false,
      generationStartedAt: null,
      generationError: message,
      generationTaskKey: null,
      generationTaskType: null,
      generationTaskJobId: null,
    });
  }
}

/**
 * Re-submit the generation that produced an export-result node, after it failed.
 *
 * Export nodes don't run their own submit loop — their parent (ImageEdit /
 * StoryboardGen) submits a job and stores the jobId, then Canvas.tsx polls it.
 * To retry without the parent being mounted/selected we persist the original
 * `generationRequestPayload` on the node at creation; here we re-submit it and
 * re-arm `generationJobId` so the existing Canvas polling effect picks it up.
 */
export async function regenerateExportImageNode(nodeId: string): Promise<void> {
  const store = useCanvasStore.getState();
  const node = store.nodes.find((n) => n.id === nodeId);
  if (!node) {
    return;
  }

  const data = node.data as Record<string, unknown>;
  if (data.isGenerating === true) {
    return;
  }

  const freezoneRequest = readFreezoneRedrawRequest(data);
  if (freezoneRequest) {
    await regenerateFreezoneRedrawNode(nodeId, freezoneRequest);
    return;
  }

  const outpaintRequest = readFreezoneOutpaintRequest(data);
  if (outpaintRequest) {
    await regenerateFreezoneOutpaintNode(nodeId, outpaintRequest);
    return;
  }

  const upscaleRequest = readFreezoneUpscaleRequest(data);
  if (upscaleRequest) {
    await regenerateFreezoneUpscaleNode(nodeId, upscaleRequest);
    return;
  }

  const relightRequest = readFreezoneRelightRequest(data);
  if (relightRequest) {
    await regenerateFreezoneRelightNode(nodeId, relightRequest);
    return;
  }

  const multiViewRequest = readFreezoneMultiViewRequest(data);
  if (multiViewRequest) {
    await regenerateFreezoneMultiViewNode(nodeId, multiViewRequest);
    return;
  }

  const scene360Request = readFreezoneScene360Request(data, nodeId);
  if (scene360Request) {
    await regenerateFreezoneScene360Node(nodeId, scene360Request);
    return;
  }

  const templateEditRequest = readFreezoneTemplateEditRequest(data);
  if (templateEditRequest) {
    await regenerateFreezoneTemplateEditNode(nodeId, templateEditRequest);
    return;
  }

  const payload = data.generationRequestPayload as GenerateImagePayload | undefined;
  if (!payload) {
    console.warn('[regenerate] export node has no stored payload, cannot retry', nodeId);
    return;
  }

  store.updateNodeData(nodeId, {
    isGenerating: true,
    generationStartedAt: Date.now(),
    generationJobId: null,
    generationError: null,
    generationErrorDetails: null,
    generationErrorRequestId: null,
    generationErrorStage: null,
    generationErrorSuggestedAction: null,
    generationErrorRetryable: null,
    generationRecoveryJobId: null,
    generationRecoveryTaskType: null,
  });

  try {
    const jobId = await canvasAiGateway.submitGenerateImageJob({ ...payload, nodeId });
    store.updateNodeData(nodeId, {
      generationJobId: jobId,
      generationClientSessionId: CURRENT_RUNTIME_SESSION_ID,
    });
  } catch (error) {
    const resolved = resolveErrorContent(error, '图像生成失败');
    store.updateNodeData(nodeId, {
      isGenerating: false,
      generationStartedAt: null,
      generationJobId: null,
      generationError: resolved.message,
      generationErrorDetails: resolved.details ?? null,
      generationErrorRequestId:
        extractRequestId(resolved.message) ?? extractRequestId(resolved.details),
    });
  }
}

/** Whether an export node has enough stored state to be regenerated. */
export function canRegenerateExportImageNode(data: Record<string, unknown>, nodeId?: string): boolean {
  return (
    Boolean(data.generationRequestPayload) ||
    Boolean(readFreezoneRedrawRequest(data)) ||
    Boolean(readFreezoneOutpaintRequest(data)) ||
    Boolean(readFreezoneUpscaleRequest(data)) ||
    Boolean(readFreezoneRelightRequest(data)) ||
    Boolean(readFreezoneMultiViewRequest(data)) ||
    Boolean(readFreezoneScene360Request(data, nodeId)) ||
    Boolean(readFreezoneTemplateEditRequest(data))
  );
}
