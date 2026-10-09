// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { NodeToolbar as ReactFlowNodeToolbar, Position } from '@xyflow/react';
import {
  ArrowUp,
  Check,
  ChevronDown,
  Image as ImageIcon,
  RectangleHorizontal,
  RectangleVertical,
  Sparkles,
  Square,
  X,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';

import {
  CANVAS_NODE_TYPES,
  DEFAULT_ASPECT_RATIO,
  DEFAULT_NODE_WIDTH,
  EXPORT_RESULT_NODE_DEFAULT_WIDTH,
  EXPORT_RESULT_NODE_LAYOUT_HEIGHT,
  type CanvasNode,
} from '@/features/canvas/domain/canvasNodes';
import { useCanvasStore } from '@/stores/canvasStore';
import {
  fetchFreezoneJobResult,
  submitFreezoneOutpaint,
  type FreezoneOutpaintAspectRatio,
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
  modelAspectRatioOptions,
  modelResolutionOptions,
  persistedCanvasModelId,
  resolveModelResolution,
  selectLiveImageEditModel,
} from '@/features/canvas/domain/videoModelSelection';
import { inheritMainlineFields } from '@/features/canvas/domain/inheritMainlineFields';
import { CreditCostPill } from '@/components/credits/credit-visual';
import { useGenerationCreditCost } from '@/lib/queries/generation-credit-cost';
import { NODE_TOOLBAR_CLASS } from './nodeToolbarConfig';
import { CANVAS_NODE_TOOLBAR_PILL_CLASS } from './nodeFrameStyles';
import {
  NODE_CREDIT_PILL_FLAT_CLASS,
  NODE_GENERATE_BUTTON_BASE_CLASS,
  NODE_GENERATE_BUTTON_ENABLED_CLASS,
  NODE_GENERATE_BUTTON_DISABLED_CLASS,
} from './nodeControlStyles';
import { useAnchoredNodePopoverPosition } from './anchoredPopover';

const OUTPAINT_NUM_IMAGES = [1, 2, 3, 4] as const;
type OutpaintNumImages = (typeof OUTPAINT_NUM_IMAGES)[number];

// 数量 > 1 时多个结果节点纵向错开摆放的间距。
const RESULT_STACK_GAP = 24;

const OUTPAINT_ASPECT_OPTIONS: {
  value: FreezoneOutpaintAspectRatio;
  ratio: number | null; // null = preserve original
  i18nKey: string;
  Icon: typeof RectangleHorizontal;
}[] = [
  { value: 'original', ratio: null, i18nKey: 'outpaintEditor.aspect.original', Icon: ImageIcon },
  { value: '1:1', ratio: 1, i18nKey: 'outpaintEditor.aspect.s1_1', Icon: Square },
  { value: '4:3', ratio: 4 / 3, i18nKey: 'outpaintEditor.aspect.s4_3', Icon: RectangleHorizontal },
  { value: '3:4', ratio: 3 / 4, i18nKey: 'outpaintEditor.aspect.s3_4', Icon: RectangleVertical },
  { value: '16:9', ratio: 16 / 9, i18nKey: 'outpaintEditor.aspect.s16_9', Icon: RectangleHorizontal },
  { value: '9:16', ratio: 9 / 16, i18nKey: 'outpaintEditor.aspect.s9_16', Icon: RectangleVertical },
];

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

interface OutpaintEditorOverlayProps {
  node: CanvasNode;
  imageSource: string;
  onClose: () => void;
}

export const OutpaintEditorOverlay = memo(
  ({ node, imageSource, onClose }: OutpaintEditorOverlayProps) => {
    const { t } = useTranslation();
    const addNode = useCanvasStore((state) => state.addNode);
    const addEdge = useCanvasStore((state) => state.addEdge);
    const setSelectedNode = useCanvasStore((state) => state.setSelectedNode);
    const findNodePosition = useCanvasStore((state) => state.findNodePosition);
    const updateNodeData = useCanvasStore((state) => state.updateNodeData);

    const [aspectRatio, setAspectRatio] =
      useState<FreezoneOutpaintAspectRatio>('original');
    const [imageSize, setImageSize] = useState<string | undefined>(undefined);
    const [numImages, setNumImages] = useState<OutpaintNumImages>(1);
    const sourceModelId = persistedCanvasModelId(node.data as Record<string, unknown>);
    const [modelId, setModelId] = useState<string>(() => sourceModelId ?? '');
    const {
      models: availableModels,
      isLoading: imageModelsLoading,
      isFallback: imageModelsFallback,
    } = useFreezoneImageModels();
    const [isSubmitting, setIsSubmitting] = useState(false);
    const selectedModel = selectLiveImageEditModel(
      availableModels,
      modelId || sourceModelId,
    );
    const aspectRatioOptions = useMemo(
      () => OUTPAINT_ASPECT_OPTIONS.filter((option) => (
        option.value === 'original'
        || modelAspectRatioOptions(selectedModel).includes(option.value)
      )),
      [selectedModel],
    );
    const imageSizeOptions = useMemo(
      () => modelResolutionOptions(selectedModel),
      [selectedModel],
    );
    useEffect(() => {
      if (selectedModel && modelId !== selectedModel.id) {
        setModelId(selectedModel.id);
      }
    }, [modelId, selectedModel]);
    useEffect(() => {
      setImageSize((current) => resolveModelResolution(selectedModel, current));
    }, [selectedModel]);
    useEffect(() => {
      if (aspectRatioOptions.length > 0 && !aspectRatioOptions.some((option) => option.value === aspectRatio)) {
        setAspectRatio(aspectRatioOptions[0].value);
      }
    }, [aspectRatio, aspectRatioOptions]);
    const creditCost = useGenerationCreditCost(
      'image_selection',
      selectedModel?.apiModel ?? null,
      {
        surface: 'canvas',
        params: {
          ...(imageSize ? { size: imageSize } : {}),
          ...(imageModelSupportsQuality(selectedModel?.apiModel)
            ? { quality: 'medium' }
            : {}),
        },
        quantity: Math.min(Math.max(numImages, 1), 4),
      },
    );

    const nodeWidth =
      typeof node.measured?.width === 'number'
        ? node.measured.width
        : typeof node.width === 'number'
          ? node.width
          : DEFAULT_NODE_WIDTH;
    const nodeHeight =
      typeof node.measured?.height === 'number'
        ? node.measured.height
        : typeof node.height === 'number'
          ? node.height
          : nodeWidth;

    const frame = useMemo(() => {
      const option = OUTPAINT_ASPECT_OPTIONS.find((o) => o.value === aspectRatio);
      const targetRatio = option?.ratio ?? null;
      if (targetRatio === null) {
        return { width: nodeWidth, height: nodeHeight };
      }
      const nodeRatio = nodeWidth / nodeHeight;
      // Keep the original image at its native size; extend only the dimension
      // that needs to grow so the frame still contains it.
      if (targetRatio >= nodeRatio) {
        // Target is wider → grow horizontally.
        return { width: nodeHeight * targetRatio, height: nodeHeight };
      }
      // Target is taller → grow vertically.
      return { width: nodeWidth, height: nodeWidth / targetRatio };
    }, [aspectRatio, nodeHeight, nodeWidth]);

    const verticalExtension = Math.max(0, (frame.height - nodeHeight) / 2);
    const horizontalExtension = Math.max(0, (frame.width - nodeWidth) / 2);
    const bottomToolbarOffset = verticalExtension + 12;

    // 建一个 loading 结果节点并连边，立即返回节点 id（同步，不等待生成）。
    const createOutpaintNode = useCallback(
      (sourceAspectRatio: string, position: { x: number; y: number }) => {
        const generationStartedAt = Date.now();
        // 1→1 outpaint: inherit source's mainline fields so the new node still
        // resolves to the same canonical slot at Push time. user_spawned: true
        // is stamped by inheritMainlineFields; preset_managed is never set.
        const initialData = inheritMainlineFields(
          { data: node.data as Record<string, unknown> },
          {
            displayName: t('outpaintEditor.title'),
            imageUrl: null,
            previewImageUrl: null,
            aspectRatio: aspectRatio === 'original' ? sourceAspectRatio : aspectRatio,
            resultKind: 'generic',
            isGenerating: true,
            generationStartedAt,
            generationModel: selectedModel?.apiModel,
            generationModelId: selectedModel?.id,
            generationProviderId: selectedModel?.providerId,
            outpaintModelId: selectedModel?.id,
            freezoneOutpaintRequest: {
              sourceUrl: imageSource.split('?')[0],
              targetAspectRatio: aspectRatio,
              ...(imageSize ? { imageSize } : {}),
              model: selectedModel?.apiModel,
            },
          },
        );
        const nextNodeId = addNode(
          CANVAS_NODE_TYPES.exportImage,
          position,
          initialData as unknown as Parameters<typeof addNode>[2],
        );
        addEdge(node.id, nextNodeId);
        return nextNodeId;
      },
      [addEdge, addNode, aspectRatio, imageSize, imageSource, node, selectedModel, t],
    );

    // 针对已建好的节点提交单图扩图（num_images=1）→ 轮询 → 回填。
    const runOutpaintGeneration = useCallback(
      async (project: string, nodeId: string, apiModel: string) => {
        updateNodeData(nodeId, {
          freezoneOutpaintRequest: {
            sourceUrl: imageSource.split('?')[0],
            targetAspectRatio: aspectRatio,
            ...(imageSize ? { imageSize } : {}),
            model: apiModel,
          },
          generationModel: apiModel,
          generationModelId: selectedModel?.id ?? apiModel,
          generationProviderId: selectedModel?.providerId ?? 'direct',
        });
        try {
          const ref = await submitFreezoneOutpaint(project, {
            sourceUrl: imageSource.split('?')[0],
            targetAspectRatio: aspectRatio,
            numImages: 1,
            ...(imageSize ? { imageSize } : {}),
            model: apiModel,
            canvasId: readUrl().canvas ?? 'default',
            nodeId,
          });
          updateNodeData(nodeId, generationTaskDescriptor(ref));
          const completed = await awaitTaskCompletion(ref.task_key, project);
          const directUrl = completed.result?.['output_url'] as string | undefined;
          let url = directUrl;
          if (!url) {
            const fallback = await fetchFreezoneJobResult(project, ref.task_type, ref.job_id);
            url = fallback.url;
          }
          updateNodeData(nodeId, {
            imageUrl: url,
            previewImageUrl: url,
            isGenerating: false,
            generationStartedAt: null,
            generationError: null,
          });
        } catch (err) {
          const message = err instanceof Error ? err.message : String(err);
          console.error('[outpaint] generation failed', err);
          updateNodeData(nodeId, {
            isGenerating: false,
            generationStartedAt: null,
            generationError: message,
          });
        }
      },
      [aspectRatio, imageSize, imageSource, selectedModel, updateNodeData],
    );

    const modelUnavailableReason = selectedModel && !imageModelsLoading && !imageModelsFallback
      ? null
      : imageModelsLoading
        ? '正在读取并验证生图模型...'
        : imageModelsFallback
          ? '模型目录未验证，暂不提交扩图任务'
          : '尚未配置可用的图生图模型，请先到模型中心添加并检测。';

    const handleSubmit = useCallback(async () => {
      if (isSubmitting || !selectedModel) return;
      const project = readUrl().project;
      if (!project) {
        console.error('[outpaint] no project in URL — cannot submit');
        return;
      }

      const sourceAspectRatio =
        typeof (node.data as { aspectRatio?: unknown }).aspectRatio === 'string'
          ? ((node.data as { aspectRatio?: string }).aspectRatio ?? DEFAULT_ASPECT_RATIO)
          : DEFAULT_ASPECT_RATIO;
      const base = findNodePosition(
        node.id,
        EXPORT_RESULT_NODE_DEFAULT_WIDTH,
        EXPORT_RESULT_NODE_LAYOUT_HEIGHT,
      );
      const apiModel = selectedModel.apiModel;

      setIsSubmitting(true);
      try {
        // 后端 outpaint 单次仅出 1 张：选了 N 张就建 N 个 loading 节点（纵向错开）
        // 并发起 N 次单图请求，每个节点各自独立轮询/回填/报错。
        const count = Math.max(1, numImages);
        const nodeIds = Array.from({ length: count }, (_unused, i) =>
          createOutpaintNode(sourceAspectRatio, {
            x: base.x,
            y: base.y + i * (EXPORT_RESULT_NODE_LAYOUT_HEIGHT + RESULT_STACK_GAP),
          }),
        );
        setSelectedNode(nodeIds[0]);
        onClose();
        nodeIds.forEach((id) => void runOutpaintGeneration(project, id, apiModel));
      } finally {
        setIsSubmitting(false);
      }
    }, [
      createOutpaintNode,
      findNodePosition,
      isSubmitting,
      modelId,
      node,
      numImages,
      onClose,
      runOutpaintGeneration,
      selectedModel,
      setSelectedNode,
    ]);

    return (
      <>
        {/* Frame overlay anchored at the node's top edge; the inner div uses
            absolute positioning to draw a rectangle centered on the node. */}
        <ReactFlowNodeToolbar
          nodeId={node.id}
          isVisible
          position={Position.Top}
          align="center"
          offset={0}
          className={`${NODE_TOOLBAR_CLASS} pointer-events-none`}
        >
          <div className="relative" style={{ width: 0, height: 0 }}>
            <div
              className="pointer-events-none absolute overflow-hidden rounded-lg border border-cyan-200/30 bg-cyan-200/[0.025] shadow-[0_0_0_1px_rgba(34,211,238,0.08)]"
              style={{
                width: frame.width,
                height: frame.height,
                left: '50%',
                top: nodeHeight / 2,
                transform: 'translate(-50%, -50%)',
              }}
            >
              {verticalExtension > 0 && (
                <>
                  <div
                    className="absolute inset-x-0 top-0 bg-cyan-200/[0.055]"
                    style={{ height: verticalExtension }}
                  />
                  <div
                    className="absolute inset-x-0 bottom-0 bg-cyan-200/[0.055]"
                    style={{ height: verticalExtension }}
                  />
                </>
              )}
              {horizontalExtension > 0 && (
                <>
                  <div
                    className="absolute inset-y-0 left-0 bg-cyan-200/[0.055]"
                    style={{ width: horizontalExtension }}
                  />
                  <div
                    className="absolute inset-y-0 right-0 bg-cyan-200/[0.055]"
                    style={{ width: horizontalExtension }}
                  />
                </>
              )}
            </div>
          </div>
        </ReactFlowNodeToolbar>

        <ReactFlowNodeToolbar
          nodeId={node.id}
          isVisible
          position={Position.Bottom}
          align="center"
          offset={bottomToolbarOffset}
          className={NODE_TOOLBAR_CLASS}
        >
          <div
            className={`flex items-center gap-1 ${CANVAS_NODE_TOOLBAR_PILL_CLASS}`}
            onClick={(event) => event.stopPropagation()}
          >
            <button
              type="button"
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-text-muted transition-colors hover:bg-white/[0.06] hover:text-text-dark"
              onClick={onClose}
              title={t('outpaintEditor.exit')}
            >
              <X className="h-4 w-4" />
            </button>

            <ProviderModelPicker
              selectedModelId={selectedModel?.id ?? modelId}
              onChange={setModelId}
              getOptionDisabledReason={imageEditModelDisabledReason}
            />
            <AspectRatioPicker
              value={aspectRatio}
              options={aspectRatioOptions}
              onChange={setAspectRatio}
            />
            {imageSizeOptions.length > 0 && imageSize && (
              <SimpleSegmentedDropdown<string>
                value={imageSize}
                options={imageSizeOptions}
                onChange={setImageSize}
                renderLabel={(v) => v}
                titleI18nKey="outpaintEditor.qualityLabel"
              />
            )}
            <SimpleSegmentedDropdown<OutpaintNumImages>
              value={numImages}
              options={OUTPAINT_NUM_IMAGES}
              onChange={setNumImages}
              renderLabel={(v) => t('outpaintEditor.numImages', { count: v })}
              titleI18nKey="outpaintEditor.numImagesLabel"
            />
            <CreditCostPill
              display={creditCost.data?.data.display}
              className={NODE_CREDIT_PILL_FLAT_CLASS}
            />

            <button
              type="button"
              onClick={handleSubmit}
              disabled={isSubmitting || Boolean(modelUnavailableReason)}
              className={`shrink-0 ${NODE_GENERATE_BUTTON_BASE_CLASS} ${
                isSubmitting
                  ? NODE_GENERATE_BUTTON_DISABLED_CLASS
                  : NODE_GENERATE_BUTTON_ENABLED_CLASS
              }`}
              title={modelUnavailableReason ?? t('outpaintEditor.submit')}
            >
              <ArrowUp className="h-4 w-4" />
            </button>
          </div>
        </ReactFlowNodeToolbar>
      </>
    );
  },
);

OutpaintEditorOverlay.displayName = 'OutpaintEditorOverlay';

interface AspectRatioPickerProps {
  value: FreezoneOutpaintAspectRatio;
  options: ReadonlyArray<(typeof OUTPAINT_ASPECT_OPTIONS)[number]>;
  onChange: (next: FreezoneOutpaintAspectRatio) => void;
}

function AspectRatioPicker({ value, options, onChange }: AspectRatioPickerProps) {
  const { t } = useTranslation();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const [isOpen, setIsOpen] = useState(false);
  const popoverPosition = useAnchoredNodePopoverPosition(isOpen, triggerRef, {
    width: 180,
    height: 252,
    align: 'center',
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

  const selected = options.find((o) => o.value === value)
    ?? options[0]
    ?? OUTPAINT_ASPECT_OPTIONS[0];
  const SelectedIcon = selected.Icon;

  return (
    <div className="relative">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setIsOpen((prev) => !prev)}
        className="inline-flex h-8 items-center gap-1 rounded-full px-2.5 text-xs text-text-dark transition-colors hover:bg-white/[0.06]"
      >
        <SelectedIcon className="h-3.5 w-3.5 text-text-muted" />
        <span className="font-medium">{t(selected.i18nKey)}</span>
        <ChevronDown className="h-3 w-3 text-text-muted" />
      </button>
      {isOpen && popoverPosition && typeof document !== 'undefined' && createPortal(
        <div
          ref={popoverRef}
          data-canvas-floating-popover="true"
          className="fixed z-[10000] w-[180px] rounded-xl border border-white/10 bg-surface-dark/95 p-2 shadow-2xl backdrop-blur-md"
          style={{
            left: popoverPosition.left,
            top: popoverPosition.top,
          }}
          onPointerDown={(event) => event.stopPropagation()}
          onClick={(event) => event.stopPropagation()}
        >
          <div className="mb-1 px-2 py-1 text-[11px] uppercase tracking-wide text-text-muted">
            {t('outpaintEditor.aspectLabel')}
          </div>
          <div className="flex flex-col">
            {options.map((option) => {
              const Icon = option.Icon;
              const isActive = option.value === value;
              return (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => {
                    onChange(option.value);
                    setIsOpen(false);
                  }}
                  className={`flex items-center gap-2 rounded-md px-2 py-1.5 text-xs font-medium transition-colors ${
                    isActive
                      ? 'bg-white/[0.12] text-text-dark'
                      : 'text-text-muted hover:bg-white/[0.08] hover:text-text-dark'
                  }`}
                >
                  <Icon className="h-3.5 w-3.5" />
                  <span>{t(option.i18nKey)}</span>
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

interface SimpleSegmentedDropdownProps<T extends string | number> {
  value: T;
  options: readonly T[];
  onChange: (next: T) => void;
  renderLabel: (value: T) => string;
  titleI18nKey: string;
}

function SimpleSegmentedDropdown<T extends string | number>({
  value,
  options,
  onChange,
  renderLabel,
  titleI18nKey,
}: SimpleSegmentedDropdownProps<T>) {
  const { t } = useTranslation();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const [isOpen, setIsOpen] = useState(false);
  const popoverPosition = useAnchoredNodePopoverPosition(isOpen, triggerRef, {
    width: 160,
    height: 180,
    align: 'center',
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

  return (
    <div className="relative">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setIsOpen((prev) => !prev)}
        className="inline-flex h-8 items-center gap-1 rounded-full px-2.5 text-xs text-text-dark transition-colors hover:bg-white/[0.06]"
      >
        <Sparkles className="h-3.5 w-3.5 text-text-muted" />
        <span className="font-medium">{renderLabel(value)}</span>
        <ChevronDown className="h-3 w-3 text-text-muted" />
      </button>
      {isOpen && popoverPosition && typeof document !== 'undefined' && createPortal(
        <div
          ref={popoverRef}
          data-canvas-floating-popover="true"
          className="fixed z-[10000] w-[160px] rounded-xl border border-white/10 bg-surface-dark/95 p-2 shadow-2xl backdrop-blur-md"
          style={{
            left: popoverPosition.left,
            top: popoverPosition.top,
          }}
          onPointerDown={(event) => event.stopPropagation()}
          onClick={(event) => event.stopPropagation()}
        >
          <div className="mb-1 px-2 py-1 text-[11px] uppercase tracking-wide text-text-muted">
            {t(titleI18nKey)}
          </div>
          <div className="flex flex-col">
            {options.map((option) => {
              const isActive = option === value;
              return (
                <button
                  key={String(option)}
                  type="button"
                  onClick={() => {
                    onChange(option);
                    setIsOpen(false);
                  }}
                  className={`flex items-center justify-between rounded-md px-2 py-1.5 text-xs font-medium transition-colors ${
                    isActive
                      ? 'bg-white/[0.12] text-text-dark'
                      : 'text-text-muted hover:bg-white/[0.08] hover:text-text-dark'
                  }`}
                >
                  <span>{renderLabel(option)}</span>
                  {isActive && <Check className="h-3 w-3" />}
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
