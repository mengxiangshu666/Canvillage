// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { memo, useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { NodeToolbar as ReactFlowNodeToolbar, Position } from '@xyflow/react';
import { ArrowUp, Check, ChevronDown, Sparkles } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { type CanvasNode } from '@/features/canvas/domain/canvasNodes';
import { useCanvasStore } from '@/stores/canvasStore';
import {
  fetchFreezoneJobResult,
  submitFreezoneUpscale,
  type FreezoneUpscaleScaleFactor,
} from '@/api/ops';
import { awaitTaskCompletion } from '@/api/tasks';
import { generationTaskDescriptor } from '@/features/canvas/application/resumeGeneration';
import { readUrl } from '@/lib/url-params';
import {
  ProviderModelPicker,
} from '@/features/canvas/ui/ProviderModelPicker';
import { useFreezoneImageModels } from '@/features/canvas/hooks/useFreezoneImageModels';
import {
  imageEditModelDisabledReason,
  modelResolutionOptions,
  resolveModelResolution,
  selectLiveImageEditModel,
} from '@/features/canvas/domain/videoModelSelection';
import { CreditCostPill } from '@/components/credits/credit-visual';
import { useGenerationCreditCost } from '@/lib/queries/generation-credit-cost';
import { NODE_TOOLBAR_CLASS } from './nodeToolbarConfig';
import { CANVAS_NODE_TOOLBAR_CARD_CLASS } from './nodeFrameStyles';
import { NODE_CREDIT_PILL_FLAT_CLASS } from './nodeControlStyles';
import { ZoomScaledToolbar } from './ZoomScaledToolbar';
import { useAnchoredNodePopoverPosition } from './anchoredPopover';

const SCALE_FACTORS: FreezoneUpscaleScaleFactor[] = [2, 4, 6];
const DEFAULT_UPSCALE_SCALE_FACTOR: FreezoneUpscaleScaleFactor = 2;

function imageModelSupportsQuality(apiModel: string | null | undefined): boolean {
  if (!apiModel) return false;
  const normalized = apiModel.toLowerCase();
  return (
    normalized === 'gpt-image-2'
    || normalized === 'image-2'
    || normalized === 'image-2-official'
    || normalized.includes('gpt-image')
  );
}

interface UpscalePersistedFields {
  upscaleSourceUrl?: string;
  upscaleModelId?: string;
  upscaleImageSize?: string;
  upscaleScaleFactor?: FreezoneUpscaleScaleFactor;
}

interface UpscaleEditorOverlayProps {
  /**
   * The upscale-result ExportImage node. The panel is always anchored beneath it
   * while the node is selected — settings are persisted on `node.data` so they
   * survive re-selection.
   */
  node: CanvasNode;
}

export const UpscaleEditorOverlay = memo(({ node }: UpscaleEditorOverlayProps) => {
  const { t } = useTranslation();
  const updateNodeData = useCanvasStore((state) => state.updateNodeData);
  const deleteNode = useCanvasStore((state) => state.deleteNode);
  const setSelectedNode = useCanvasStore((state) => state.setSelectedNode);

  const persisted = node.data as UpscalePersistedFields;
  const sourceUrl = persisted.upscaleSourceUrl ?? '';
  const persistedModelId =
    typeof persisted.upscaleModelId === 'string' ? persisted.upscaleModelId : '';
  const {
    models: availableModels,
    isLoading: modelsLoading,
    isFallback: modelsFallback,
  } = useFreezoneImageModels();
  const selectedModel = selectLiveImageEditModel(availableModels, persistedModelId);
  const imageSizeOptions = modelResolutionOptions(selectedModel);
  const persistedImageSize = resolveModelResolution(selectedModel, persisted.upscaleImageSize);
  const persistedScaleFactor: FreezoneUpscaleScaleFactor =
    persisted.upscaleScaleFactor === 4 || persisted.upscaleScaleFactor === 6
      ? persisted.upscaleScaleFactor
      : DEFAULT_UPSCALE_SCALE_FACTOR;

  const [isSubmitting, setIsSubmitting] = useState(false);
  useEffect(() => {
    if (selectedModel && persistedModelId !== selectedModel.id) {
      updateNodeData(node.id, { upscaleModelId: selectedModel.id });
    }
    const current = typeof persisted.upscaleImageSize === 'string'
      ? persisted.upscaleImageSize
      : undefined;
    const resolved = resolveModelResolution(selectedModel, current);
    if (resolved !== current) {
      updateNodeData(node.id, { upscaleImageSize: resolved });
    }
  }, [node.id, persisted.upscaleImageSize, persistedModelId, selectedModel, updateNodeData]);
  const modelUnavailableReason = selectedModel && !modelsLoading && !modelsFallback
    ? null
    : modelsLoading
      ? '正在读取并验证生图模型...'
      : modelsFallback
        ? '模型目录未验证，暂不提交放大任务'
        : '尚未配置可用的图生图模型，请先到模型中心添加并检测。';
  const creditCost = useGenerationCreditCost(
    'image_selection',
    selectedModel?.apiModel ?? null,
    {
      surface: 'canvas',
      params: {
        ...(persistedImageSize ? { size: persistedImageSize } : {}),
        ...(imageModelSupportsQuality(selectedModel?.apiModel)
          ? { quality: 'medium' }
          : {}),
      },
    },
  );

  const handleModelChange = useCallback(
    (modelId: string) => {
      updateNodeData(node.id, { upscaleModelId: modelId });
    },
    [node.id, updateNodeData],
  );

  const handleImageSizeChange = useCallback(
    (size: string) => {
      updateNodeData(node.id, { upscaleImageSize: size });
    },
    [node.id, updateNodeData],
  );

  const handleScaleFactorChange = useCallback(
    (factor: FreezoneUpscaleScaleFactor) => {
      updateNodeData(node.id, { upscaleScaleFactor: factor });
    },
    [node.id, updateNodeData],
  );

  const handleCancel = useCallback(() => {
    deleteNode(node.id);
    setSelectedNode(null);
  }, [deleteNode, node.id, setSelectedNode]);

  const handleSubmit = useCallback(async () => {
    if (isSubmitting || !selectedModel) return;
    if (!sourceUrl) {
      console.error('[upscale] missing upscaleSourceUrl on node.data — cannot submit');
      return;
    }
    const project = readUrl().project;
    if (!project) {
      console.error('[upscale] no project in URL — cannot submit');
      return;
    }

    const apiModel = selectedModel.apiModel;

    setIsSubmitting(true);
    const generationStartedAt = Date.now();
    updateNodeData(node.id, {
      isGenerating: true,
      generationStartedAt,
      generationError: null,
      generationModel: apiModel,
      generationModelId: selectedModel.id,
      generationProviderId: selectedModel.providerId,
      upscaleModelId: selectedModel.id,
      freezoneUpscaleRequest: {
        sourceUrl: sourceUrl.split('?')[0],
        ...(persistedImageSize ? { imageSize: persistedImageSize } : {}),
        scaleFactor: persistedScaleFactor,
        model: apiModel,
      },
    });

    try {
      const ref = await submitFreezoneUpscale(project, {
        sourceUrl: sourceUrl.split('?')[0],
        scaleFactor: persistedScaleFactor,
        ...(persistedImageSize ? { imageSize: persistedImageSize } : {}),
        model: apiModel,
        canvasId: readUrl().canvas ?? 'default',
        nodeId: node.id,
      });
      updateNodeData(node.id, generationTaskDescriptor(ref));
      const completed = await awaitTaskCompletion(ref.task_key, project);
      const directUrl = completed.result?.['output_url'] as string | undefined;
      let url = directUrl;
      if (!url) {
        const fallback = await fetchFreezoneJobResult(project, ref.task_type, ref.job_id);
        url = fallback.url;
      }
      updateNodeData(node.id, {
        imageUrl: url,
        previewImageUrl: url,
        isGenerating: false,
        generationStartedAt: null,
        generationError: null,
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      console.error('[upscale] generation failed', err);
      updateNodeData(node.id, {
        isGenerating: false,
        generationStartedAt: null,
        generationError: message,
      });
    } finally {
      setIsSubmitting(false);
    }
  }, [
    isSubmitting,
    node.id,
    persistedImageSize,
    persistedModelId,
    persistedScaleFactor,
    selectedModel,
    sourceUrl,
    updateNodeData,
  ]);

  return (
    <ReactFlowNodeToolbar
      nodeId={node.id}
      isVisible
      position={Position.Bottom}
      align="center"
      offset={12}
      className={NODE_TOOLBAR_CLASS}
    >
      {/* 操作区按画布缩放同步缩放：面板挂在节点下方，锚点取顶边（贴着节点底边），
          画布缩小时面板朝节点收缩、视觉上与节点同比变小。 */}
      <ZoomScaledToolbar origin="top center">
        <div
          className={`w-[400px] p-4 ${CANVAS_NODE_TOOLBAR_CARD_CLASS}`}
          onClick={(event) => event.stopPropagation()}
        >
        <div className="mb-3 flex items-center justify-between border-b border-white/10 pb-2.5">
          <div className="text-sm font-semibold text-text-dark">
            {t('upscaleEditor.title')}
          </div>
          <button
            type="button"
            className="text-xs text-text-muted transition-colors hover:text-text-dark"
            onClick={handleCancel}
            title={t('upscaleEditor.cancel')}
          >
            {t('common.cancel')}
          </button>
        </div>

        <div className="space-y-3">
          <PanelRow label={t('upscaleEditor.providerLabel')}>
            <ProviderModelPicker
              selectedModelId={selectedModel?.id ?? persistedModelId}
              onChange={handleModelChange}
              getOptionDisabledReason={imageEditModelDisabledReason}
            />
          </PanelRow>

          {imageSizeOptions.length > 0 && persistedImageSize && (
            <PanelRow label={t('upscaleEditor.qualityLabel')}>
              <QualityPicker
                value={persistedImageSize}
                options={imageSizeOptions}
                onChange={handleImageSizeChange}
              />
            </PanelRow>
          )}

          <PanelRow label={t('upscaleEditor.scaleLabel')}>
            <ScaleFactorPicker value={persistedScaleFactor} onChange={handleScaleFactorChange} />
          </PanelRow>
        </div>

        <div className="mt-4 flex items-center justify-end gap-3 border-t border-white/10 pt-3">
          <CreditCostPill
            display={creditCost.data?.data.display}
            className={NODE_CREDIT_PILL_FLAT_CLASS}
          />
          <button
            type="button"
            onClick={handleSubmit}
            disabled={isSubmitting || Boolean(modelUnavailableReason)}
            className="flex h-8 w-8 items-center justify-center rounded-full bg-white text-bg-dark transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
            title={modelUnavailableReason ?? t('upscaleEditor.submit')}
          >
            <ArrowUp className="h-4 w-4" />
          </button>
        </div>
        </div>
      </ZoomScaledToolbar>
    </ReactFlowNodeToolbar>
  );
});

UpscaleEditorOverlay.displayName = 'UpscaleEditorOverlay';

function PanelRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-xs text-text-muted">{label}</span>
      {children}
    </div>
  );
}

interface QualityPickerProps {
  value: string;
  options: readonly string[];
  onChange: (value: string) => void;
}

function QualityPicker({ value, options, onChange }: QualityPickerProps) {
  const { t } = useTranslation();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const [isOpen, setIsOpen] = useState(false);
  const popoverPosition = useAnchoredNodePopoverPosition(isOpen, triggerRef, {
    width: 240,
    height: 94,
    align: 'end',
  });

  useEffect(() => {
    if (!isOpen) return;
    const onPointerDown = (event: MouseEvent) => {
      if (
        triggerRef.current?.contains(event.target as Node) ||
        popoverRef.current?.contains(event.target as Node)
      ) {
        return;
      }
      setIsOpen(false);
    };
    document.addEventListener('mousedown', onPointerDown, true);
    return () => document.removeEventListener('mousedown', onPointerDown, true);
  }, [isOpen]);

  const title = t('upscaleEditor.qualityPicker.title');

  return (
    <div className="relative">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setIsOpen((prev) => !prev)}
        className="inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-white/[0.04] px-2.5 py-1 text-xs text-text-dark transition-colors hover:bg-white/[0.08]"
      >
        <Sparkles className="h-3.5 w-3.5 text-text-muted" />
        <span className="font-medium">{title}</span>
        <span className="text-text-muted">·</span>
        <span className="text-text-muted">{value}</span>
        <ChevronDown className="h-3 w-3 text-text-muted" />
      </button>
      {isOpen && popoverPosition && typeof document !== 'undefined' && createPortal(
        <div
          ref={popoverRef}
          className="fixed z-[10000] w-[240px] rounded-xl border border-white/10 bg-surface-dark/95 p-3 shadow-2xl backdrop-blur-md"
          style={{
            left: popoverPosition.left,
            top: popoverPosition.top,
          }}
          onPointerDown={(event) => event.stopPropagation()}
          onClick={(event) => event.stopPropagation()}
        >
          <div className="mb-1 text-[11px] uppercase tracking-wide text-text-muted">{title}</div>
          <div className="flex gap-1.5">
            {options.map((size) => {
              const isActive = value === size;
              return (
                <button
                  key={size}
                  type="button"
                  onClick={() => {
                    onChange(size);
                    setIsOpen(false);
                  }}
                  className={`inline-flex h-8 flex-1 items-center justify-center gap-1 rounded-full px-3 text-xs font-medium transition-colors ${
                    isActive
                      ? 'bg-[rgb(var(--accent-rgb))] text-white'
                      : 'bg-white/[0.06] text-text-dark hover:bg-white/[0.12]'
                  }`}
                >
                  {isActive && <Check className="h-3 w-3" />}
                  {size}
                </button>
              );
            })}
          </div>
        </div>,
        document.body,
      )}
    </div>
  );
}

interface ScaleFactorPickerProps {
  value: FreezoneUpscaleScaleFactor;
  onChange: (next: FreezoneUpscaleScaleFactor) => void;
}

function ScaleFactorPicker({ value, onChange }: ScaleFactorPickerProps) {
  return (
    <div className="inline-flex items-center gap-1 rounded-lg border border-white/10 bg-white/[0.04] p-0.5">
      {SCALE_FACTORS.map((factor) => {
        const isActive = value === factor;
        return (
          <button
            key={factor}
            type="button"
            onClick={() => onChange(factor)}
            className={`flex h-7 w-12 items-center justify-center rounded-md text-xs font-medium transition-colors ${
              isActive
                ? 'bg-white text-bg-dark'
                : 'text-text-muted hover:bg-white/[0.06] hover:text-text-dark'
            }`}
          >
            {factor}
          </button>
        );
      })}
    </div>
  );
}
