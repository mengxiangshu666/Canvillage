// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Camera, ChevronDown, Palette, SlidersHorizontal } from 'lucide-react';
import {
  type ImageGenCameraSelection,
  type ImageGenCount,
  type ImageGenNodeData,
  type ImageQuality,
  type ImageSize,
  IMAGE_SIZES,
} from '@/features/canvas/domain/canvasNodes';
import {
  parseAspectRatio,
  pickClosestAspectRatio,
} from '@/features/canvas/application/imageData';
import {
  GENERATION_CONCURRENCY_DEFAULT,
  generationQueueUnitHint,
} from '@/features/canvas/application/generationConcurrency';
import type { ExtraParamDefinition } from '@/features/canvas/models/types';
import { ImageModelAdvancedParamFields } from '@/features/canvas/ui/ImageModelAdvancedParams';
import {
  CAMERA_PICKER_POPOVER_WIDTH,
  CameraPickerPopover,
} from '@/features/canvas/nodes/CameraPickerPopover';
import { StylePickerPopover } from '@/features/canvas/nodes/StylePickerPopover';
import {
  NODE_FLOATING_PANEL_SURFACE_CLASS,
  NODE_TEXT_CONTROL_ICON_CLASS,
  NODE_TEXT_CONTROL_TRIGGER_CLASS,
} from '@/features/canvas/ui/nodeControlStyles';
import { useAnchoredNodePopoverPosition } from '@/features/canvas/ui/anchoredPopover';

const COUNT_OPTIONS: ReadonlyArray<ImageGenCount> = [1, 2, 4, 6, 8, 12];
const PARAM_COUNT_OPTIONS: ReadonlyArray<ImageGenCount> = [1, 2, 4];
type ImageQualityPreset = ImageQuality | 'ultra' | 'extreme';
const QUALITY_OPTIONS: ReadonlyArray<{ value: ImageQualityPreset; label: string }> = [
  { value: 'low', label: '低画质' },
  { value: 'medium', label: '标准画质' },
  { value: 'high', label: '高画质' },
  { value: 'ultra', label: '超高画质' },
  { value: 'extreme', label: '极致画质' },
];
const BACKGROUND_OPTIONS = [
  { value: 'auto', label: '自动' },
  { value: 'preserve', label: '保留背景' },
  { value: 'transparent', label: '透明背景' },
] as const;
const ASPECT_PRESETS = [
  '1:1', '1:2', '2:1', '9:16', '16:9',
  '3:4', '4:3', '3:2', '2:3', '5:4',
  '4:5', '21:9', '9:21',
] as const;
const SIZE_OPTIONS = IMAGE_SIZES.filter((value) => value !== '0.5K') as ReadonlyArray<ImageSize>;
const ASPECT_GLYPH_SIZES: Record<string, { width: number; height: number }> = {
  '1:1': { width: 12, height: 12 },
  '1:2': { width: 8, height: 16 },
  '2:1': { width: 16, height: 8 },
  '9:16': { width: 9, height: 16 },
  '16:9': { width: 16, height: 9 },
  '3:4': { width: 9, height: 12 },
  '4:3': { width: 12, height: 9 },
  '3:2': { width: 12, height: 8 },
  '2:3': { width: 9, height: 12 },
  '5:4': { width: 12, height: 10 },
  '4:5': { width: 10, height: 12 },
  '21:9': { width: 16, height: 7 },
  '9:21': { width: 7, height: 16 },
};
const IMAGE_PARAM_POPOVER_CLASS =
  'nodrag nowheel fixed z-[10000] max-h-[min(440px,calc(100vh-24px))] w-[352px] max-w-[calc(100vw-24px)] overflow-y-auto overscroll-contain rounded-2xl border border-white/[0.1] bg-[#252527]/96 p-3 shadow-[0_18px_46px_rgba(0,0,0,0.44)] backdrop-blur-2xl';
const IMAGE_COUNT_POPOVER_CLASS =
  'nodrag nowheel fixed z-[10000] w-[96px] overflow-hidden p-1 ' +
  NODE_FLOATING_PANEL_SURFACE_CLASS;
const IMAGE_PARAM_LABEL_CLASS = 'text-[12px] font-medium leading-4 text-text-muted/85';
const IMAGE_PARAM_BUTTON_BASE_CLASS =
  'inline-flex min-h-8 min-w-0 flex-1 items-center justify-center whitespace-normal rounded-lg border px-2 text-[12px] leading-none transition-colors disabled:cursor-not-allowed disabled:opacity-35';
const IMAGE_PARAM_ACTIVE_BUTTON_CLASS =
  'border-white/70 bg-white/[0.08] text-text-dark';
const IMAGE_PARAM_IDLE_BUTTON_CLASS =
  'border-white/[0.12] bg-transparent text-text-muted/85 hover:bg-white/[0.06] hover:text-text-dark';
const IMAGE_PARAM_SECTION_CLASS = 'flex flex-col gap-1.5';
const IMAGE_PARAM_GRID_CLASS = 'grid grid-cols-3 gap-1.5';
const IMAGE_PARAM_ASPECT_GRID_CLASS = 'grid grid-cols-5 gap-1.5';
const NODE_COUNT_OPTION_BASE_CLASS =
  'flex w-full items-center justify-center rounded-[6px] px-3 py-1.5 text-xs transition-colors';

function imageParamButtonClass(active: boolean, supported = true): string {
  return [
    IMAGE_PARAM_BUTTON_BASE_CLASS,
    active ? IMAGE_PARAM_ACTIVE_BUTTON_CLASS : IMAGE_PARAM_IDLE_BUTTON_CLASS,
    supported ? '' : 'cursor-not-allowed opacity-35',
  ].filter(Boolean).join(' ');
}

function AspectGlyph({ value }: { value: string }) {
  const glyph = ASPECT_GLYPH_SIZES[value] ?? { width: 12, height: 12 };
  return (
    <span className='flex size-[17px] items-center justify-center'>
      <span
        className='flex-none rounded-[2px] border-[1.5px] border-current'
        style={{ width: glyph.width, height: glyph.height }}
      />
    </span>
  );
}

// 图片按自然尺寸算出的比例常是约分形式（如 21:9 会被约成 7:3），不在模型能力列表里，
// 直接显示就会出现列表外的标签。这里退回到「数值最接近的当前模型比例」（复用
// imageData 的 pickClosestAspectRatio）——chip 标签与下拉里的高亮选项都基于它，保证两边一致。
function resolveNearestAspectOption(
  aspectRatio: string,
  aspectOptions: ReadonlyArray<{ value: string; label: string }>,
): { value: string; label: string } {
  const exact = aspectOptions.find((option) => option.value === aspectRatio);
  if (exact) return exact;
  const candidates = aspectOptions.filter((option) => option.value !== 'auto');
  const nearestValue = pickClosestAspectRatio(
    parseAspectRatio(aspectRatio),
    candidates.map((option) => option.value),
  );
  return (
    candidates.find((option) => option.value === nearestValue)
    ?? { value: aspectRatio, label: aspectRatio }
  );
}

interface AspectSizeChipProps {
  aspectRatio: string;
  aspectOptions: ReadonlyArray<{ value: string; label: string }>;
  size: ImageSize;
  supportedSizes: ReadonlyArray<string>;
  quality: ImageQuality;
  supportedQualities: ReadonlyArray<string>;
  count: ImageGenCount;
  onChange: (patch: Partial<ImageGenNodeData>) => void;
}

export function AspectSizeChip({
  aspectRatio,
  aspectOptions,
  size,
  supportedSizes,
  quality,
  supportedQualities,
  count,
  onChange,
}: AspectSizeChipProps) {
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const [isOpen, setIsOpen] = useState(false);
  const popoverPosition = useAnchoredNodePopoverPosition(isOpen, triggerRef, {
    width: 352,
    height: 440,
    align: 'start',
    popoverRef,
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

  const supportedAspects = new Set(aspectOptions.map((option) => option.value));
  const nearestAspect = resolveNearestAspectOption(aspectRatio, aspectOptions);
  const aspectLabel = nearestAspect.label;
  const qualityLabel = QUALITY_OPTIONS.find((option) => option.value === quality)?.label
    ?? quality;

  return (
    <div className="relative">
      <button
        ref={triggerRef}
        type="button"
        onClick={(event) => {
          event.stopPropagation();
          setIsOpen((prev) => !prev);
        }}
        className={NODE_TEXT_CONTROL_TRIGGER_CLASS}
      >
        <AspectGlyph value={nearestAspect.value} />
        <span>{aspectLabel}</span>
        <span className="text-text-muted/80">·</span>
        <span>{qualityLabel}</span>
        <span className="text-text-muted/80">·</span>
        <span>{size}</span>
        <span className="text-text-muted/80">·</span>
        <span>{count}张</span>
        <ChevronDown className="h-3 w-3 text-text-muted/90" />
      </button>
      {isOpen && popoverPosition && typeof document !== 'undefined' && createPortal(
        <div
          ref={popoverRef}
          className={IMAGE_PARAM_POPOVER_CLASS}
          style={{
            left: popoverPosition.left,
            top: popoverPosition.top,
          }}
          onPointerDown={(event) => event.stopPropagation()}
          onClick={(event) => event.stopPropagation()}
        >
          <div className="flex w-full flex-col gap-3">
            <div className={IMAGE_PARAM_SECTION_CLASS}>
              <div className={IMAGE_PARAM_LABEL_CLASS}>画质</div>
              <div className={IMAGE_PARAM_GRID_CLASS}>
                {QUALITY_OPTIONS.map((option) => {
                  const isActive = quality === option.value;
                  const isSupported = supportedQualities.includes(option.value);
                  return (
                    <button
                      key={option.value}
                      type="button"
                      disabled={!isSupported}
                      aria-pressed={isActive}
                      onClick={() => onChange({ quality: option.value as ImageQuality })}
                      className={imageParamButtonClass(isActive, isSupported)}
                    >
                      {option.label}
                    </button>
                  );
                })}
              </div>
            </div>

            <div className={IMAGE_PARAM_SECTION_CLASS}>
              <div className={IMAGE_PARAM_LABEL_CLASS}>清晰度</div>
              <div className={IMAGE_PARAM_GRID_CLASS}>
                {SIZE_OPTIONS.map((option) => {
                  const isActive = size === option;
                  const isSupported = supportedSizes.includes(option);
                  return (
                    <button
                      key={option}
                      type="button"
                      disabled={!isSupported}
                      aria-pressed={isActive}
                      onClick={() => onChange({ size: option })}
                      className={imageParamButtonClass(isActive, isSupported)}
                    >
                      {option}
                    </button>
                  );
                })}
              </div>
            </div>

            <div className={IMAGE_PARAM_SECTION_CLASS}>
              <div className={IMAGE_PARAM_LABEL_CLASS}>背景</div>
              <div className={IMAGE_PARAM_GRID_CLASS}>
                {BACKGROUND_OPTIONS.map((option) => {
                  const isSupported = option.value === 'auto';
                  return (
                    <span
                      key={option.value}
                      aria-current={isSupported ? 'true' : undefined}
                      className={imageParamButtonClass(isSupported, isSupported)}
                    >
                      {option.label}
                    </span>
                  );
                })}
              </div>
            </div>

            <div className={IMAGE_PARAM_SECTION_CLASS}>
              <div className={IMAGE_PARAM_LABEL_CLASS}>比例</div>
              <div className={IMAGE_PARAM_ASPECT_GRID_CLASS}>
                {ASPECT_PRESETS.map((option) => {
                  const isActive = nearestAspect.value === option;
                  const isSupported = supportedAspects.has(option);
                  return (
                    <button
                      key={option}
                      type="button"
                      disabled={!isSupported}
                      aria-pressed={isActive}
                      onClick={() => onChange({
                        aspectRatio: option,
                        requestAspectRatio: option,
                      })}
                      className={[
                        'flex flex-1 flex-col items-center justify-center gap-0.5 rounded-lg border px-1 py-2 transition-colors',
                        isActive
                          ? IMAGE_PARAM_ACTIVE_BUTTON_CLASS
                          : IMAGE_PARAM_IDLE_BUTTON_CLASS,
                        isSupported ? '' : 'cursor-not-allowed opacity-35',
                      ].filter(Boolean).join(' ')}
                    >
                      <AspectGlyph value={option} />
                      <span className="text-xs">{option}</span>
                    </button>
                  );
                })}
              </div>
            </div>

            <div className="shrink-0">
              <div className={IMAGE_PARAM_SECTION_CLASS}>
                <div className={IMAGE_PARAM_LABEL_CLASS}>生成数量</div>
                <div className={IMAGE_PARAM_GRID_CLASS}>
                  {PARAM_COUNT_OPTIONS.map((option) => {
                    const isActive = count === option;
                    return (
                      <button
                        key={option}
                        type="button"
                        aria-pressed={isActive}
                        onClick={() => onChange({ count: option })}
                        className={imageParamButtonClass(isActive)}
                      >
                        {option}张
                      </button>
                    );
                  })}
                </div>
              </div>
            </div>
          </div>
        </div>,
        document.body,
      )}
    </div>
  );
}

interface AdvancedParamsChipProps {
  /** 模型合同声明的高级参数；空数组时本组件不渲染任何东西。 */
  schema: ExtraParamDefinition[];
  extraParams?: Record<string, unknown>;
  defaultExtraParams?: Record<string, unknown>;
  onChange: (key: string, value: boolean | number | string) => void;
}

/**
 * 模型高级参数（MJ 系的 stylize / chaos / weird / personalisation …）的入口。
 * 只在模型合同真的声明了高级参数时出现 —— 大多数模型没有，节点上就不该多一个空壳按钮。
 */
export function AdvancedParamsChip({
  schema,
  extraParams,
  defaultExtraParams,
  onChange,
}: AdvancedParamsChipProps) {
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const [isOpen, setIsOpen] = useState(false);
  const popoverPosition = useAnchoredNodePopoverPosition(isOpen, triggerRef, {
    width: 300,
    height: Math.max(160, schema.length * 86 + 32),
    align: 'start',
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

  if (schema.length === 0) return null;

  const activeCount = schema.filter((definition) => {
    const value = extraParams?.[definition.key];
    return value !== undefined && value !== null && value !== '';
  }).length;

  return (
    <div className="relative">
      <button
        ref={triggerRef}
        type="button"
        title="高级参数"
        onClick={(event) => {
          event.stopPropagation();
          setIsOpen((prev) => !prev);
        }}
        className={NODE_TEXT_CONTROL_TRIGGER_CLASS}
      >
        <SlidersHorizontal className={`${NODE_TEXT_CONTROL_ICON_CLASS} shrink-0`} />
        <span className="whitespace-nowrap">高级参数</span>
        {activeCount > 0 && <span className="text-text-muted/80">{activeCount}</span>}
        <ChevronDown className="h-3 w-3 text-text-muted/90" />
      </button>
      {isOpen && popoverPosition && typeof document !== 'undefined' && createPortal(
        <div
          ref={popoverRef}
          className={IMAGE_PARAM_POPOVER_CLASS}
          style={{ left: popoverPosition.left, top: popoverPosition.top }}
          onPointerDown={(event) => event.stopPropagation()}
          onClick={(event) => event.stopPropagation()}
        >
          <ImageModelAdvancedParamFields
            schema={schema}
            extraParams={extraParams}
            defaultExtraParams={defaultExtraParams}
            onExtraParamChange={onChange}
            showDescription
          />
        </div>,
        document.body,
      )}
    </div>
  );
}

interface StyleChipProps {
  selectedId: string | null;
  selectedLabel: string | null;
  onChange: (nextId: string | null) => void;
  onOpenChange?: (open: boolean) => void;
}

export function StyleChip({ selectedId, selectedLabel, onChange, onOpenChange }: StyleChipProps) {
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const [isOpen, setIsOpen] = useState(false);
  const popoverPosition = useAnchoredNodePopoverPosition(isOpen, triggerRef, {
    width: 280,
    height: 420,
    align: 'start',
  });

  useEffect(() => {
    onOpenChange?.(isOpen);
  }, [isOpen, onOpenChange]);

  useEffect(() => {
    return () => onOpenChange?.(false);
  }, [onOpenChange]);

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

  const isActive = Boolean(selectedId);
  const label = isActive ? selectedLabel ?? '风格' : '风格';

  return (
    <div className="relative">
      <button
        ref={triggerRef}
        type="button"
        onClick={(event) => {
          event.stopPropagation();
          setIsOpen((prev) => !prev);
        }}
        title={isActive ? selectedLabel ?? undefined : '风格'}
        className={`${NODE_TEXT_CONTROL_TRIGGER_CLASS} max-w-[160px]`}
      >
        <Palette className={`${NODE_TEXT_CONTROL_ICON_CLASS} shrink-0`} />
        <span className="truncate">{label}</span>
      </button>
      {isOpen && popoverPosition && typeof document !== 'undefined' && createPortal(
        <div
          ref={popoverRef}
          className="nodrag nowheel fixed z-[10000]"
          style={{
            left: popoverPosition.left,
            top: popoverPosition.top,
          }}
          onPointerDown={(event) => event.stopPropagation()}
          onClick={(event) => event.stopPropagation()}
        >
          <StylePickerPopover
            selectedId={selectedId}
            onSelect={(nextId) => {
              onChange(nextId);
              setIsOpen(false);
            }}
            onClose={() => setIsOpen(false)}
          />
        </div>,
        document.body,
      )}
    </div>
  );
}

interface CameraChipProps {
  selection: ImageGenCameraSelection | null;
  summary: string | null;
  onChange: (next: ImageGenCameraSelection | null) => void;
}

export function CameraChip({ selection, summary, onChange }: CameraChipProps) {
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const [isOpen, setIsOpen] = useState(false);
  const [popoverPosition, setPopoverPosition] = useState<{
    left: number;
    top: number;
  } | null>(null);

  const syncPopoverPosition = useCallback(() => {
    const trigger = triggerRef.current;
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    const margin = 12;
    setPopoverPosition({
      left: Math.min(
        Math.max(margin, rect.left),
        window.innerWidth - CAMERA_PICKER_POPOVER_WIDTH - margin,
      ),
      top: Math.max(margin, rect.top - 8),
    });
  }, []);

  useEffect(() => {
    if (!isOpen) return;
    syncPopoverPosition();
    const onPointerDown = (event: MouseEvent) => {
      if (
        triggerRef.current?.contains(event.target as Node) ||
        popoverRef.current?.contains(event.target as Node)
      ) {
        return;
      }
      setIsOpen(false);
    };
    const onViewportChange = () => syncPopoverPosition();
    document.addEventListener('mousedown', onPointerDown, true);
    window.addEventListener('resize', onViewportChange);
    window.addEventListener('scroll', onViewportChange, true);
    return () => {
      document.removeEventListener('mousedown', onPointerDown, true);
      window.removeEventListener('resize', onViewportChange);
      window.removeEventListener('scroll', onViewportChange, true);
    };
  }, [isOpen, syncPopoverPosition]);

  const isActive = Boolean(selection) && summary != null;
  const label = isActive && summary ? summary : '摄像机';

  return (
    <div className="relative">
      <button
        ref={triggerRef}
        type="button"
        onClick={(event) => {
          event.stopPropagation();
          setIsOpen((prev) => !prev);
        }}
        title={isActive ? summary ?? undefined : '摄像机'}
        className={`${NODE_TEXT_CONTROL_TRIGGER_CLASS} max-w-[220px]`}
      >
        <Camera className={`${NODE_TEXT_CONTROL_ICON_CLASS} shrink-0`} />
        <span className="truncate">{label}</span>
      </button>
      {isOpen && popoverPosition && createPortal(
        <div
          ref={popoverRef}
          className="fixed z-[10000]"
          style={{
            left: popoverPosition.left,
            top: popoverPosition.top,
            transform: 'translateY(-100%)',
          }}
          onClick={(event) => event.stopPropagation()}
        >
          <CameraPickerPopover
            selection={selection}
            onConfirm={(next) => {
              onChange(next);
              setIsOpen(false);
            }}
            onClose={() => setIsOpen(false)}
          />
        </div>,
        document.body,
      )}
    </div>
  );
}

interface CountSelectProps {
  value: ImageGenCount;
  onChange: (value: ImageGenCount) => void;
}

export function CountSelect({ value, onChange }: CountSelectProps) {
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const [isOpen, setIsOpen] = useState(false);
  const popoverPosition = useAnchoredNodePopoverPosition(isOpen, triggerRef, {
    width: 96,
    height: 210,
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

  return (
    <div className="relative">
      <button
        ref={triggerRef}
        type="button"
        onClick={(event) => {
          event.stopPropagation();
          setIsOpen((prev) => !prev);
        }}
        className={NODE_TEXT_CONTROL_TRIGGER_CLASS}
      >
        <span>{value}张{value > GENERATION_CONCURRENCY_DEFAULT ? '·排队' : ''}</span>
        <ChevronDown className="h-3 w-3 text-text-muted/90" />
      </button>
      {isOpen && popoverPosition && typeof document !== 'undefined' && createPortal(
        <div
          ref={popoverRef}
          className={IMAGE_COUNT_POPOVER_CLASS}
          style={{
            left: popoverPosition.left,
            top: popoverPosition.top,
          }}
          onPointerDown={(event) => event.stopPropagation()}
          onClick={(event) => event.stopPropagation()}
        >
          {COUNT_OPTIONS.map((option) => {
            const isActive = option === value;
            return (
              <button
                key={option}
                type="button"
                onClick={() => {
                  onChange(option);
                  setIsOpen(false);
                }}
                className={`${NODE_COUNT_OPTION_BASE_CLASS} ${
                  isActive
                    ? IMAGE_PARAM_ACTIVE_BUTTON_CLASS
                    : 'text-text-muted/95 hover:bg-white/[0.11] hover:text-text-dark'
                }`}
              >
                {option}张{generationQueueUnitHint(option, '张')}
              </button>
            );
          })}
        </div>,
        document.body,
      )}
    </div>
  );
}
