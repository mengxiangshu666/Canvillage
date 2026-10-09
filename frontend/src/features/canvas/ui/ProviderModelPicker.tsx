// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Box, Check, ChevronDown } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { useFreezoneImageModels } from '@/features/canvas/hooks/useFreezoneImageModels';
import { useFreezoneVideoModels } from '@/features/canvas/hooks/useFreezoneVideoModels';
import type {
  FreezoneAdvancedParamSchema,
  FreezoneVideoCapabilityOpaqueField,
  FreezoneVideoCapabilityParameter,
} from '@/api/ops';
import { selectLiveModel } from '@/features/canvas/domain/videoModelSelection';
import { wireContractChip } from '@/features/canvas/ui/modelContractChip';
import {
  NODE_FLOATING_PANEL_SURFACE_CLASS,
  NODE_TEXT_CONTROL_ICON_CLASS,
  NODE_TEXT_CONTROL_TRIGGER_CLASS,
} from '@/features/canvas/ui/nodeControlStyles';

const MODEL_PICKER_POPOVER_WIDTH = 260;
const MODEL_PICKER_POPOVER_CLASS =
  `nodrag nowheel fixed z-[10000] max-h-[280px] w-[260px] overflow-y-auto p-1 ${NODE_FLOATING_PANEL_SURFACE_CLASS}`;
const MODEL_PICKER_OPTION_BASE_CLASS =
  'inline-flex min-h-10 w-full items-start gap-2 rounded-[6px] px-3 py-1.5 text-left text-xs font-medium transition-colors';

export type ProviderId =
  | 'huimeng'
  | 'openrouter'
  | 'openai'
  | 'seedance'
  | 'newapi'
  | 'minimax'
  | 'eleven'
  | 'mureka'
  | 'direct';

export interface ProviderOption {
  id: ProviderId;
  label: string;
}

export interface ModelOption {
  id: string;
  providerId: ProviderId;
  apiModel: string;
  /** Optional real upstream id when apiModel is a secure local catalog id. */
  upstreamModel?: string;
  label: string;
  parameters?: FreezoneVideoCapabilityParameter[];
  providerMapping?: Record<string, string>;
  provider_mapping?: Record<string, string>;
  mapping?: Record<string, string>;
  mediaInputs?: Array<Record<string, unknown>>;
  media_inputs?: Array<Record<string, unknown>>;
  audioInputSemantics?: Array<'driving_audio' | 'voice_profile' | 'soundtrack' | 'audio_prompt'>;
  audio_input_semantics?: Array<'driving_audio' | 'voice_profile' | 'soundtrack' | 'audio_prompt'>;
  /** AutoDL/ComfyUI workflow evidence retained for node-level capability mapping. */
  workflowId?: string;
  workflowName?: string;
  workflowInputRules?: Array<Record<string, unknown>>;
  resolutionMappings?: Array<Record<string, unknown>>;
  workflowDiscovery?: Record<string, unknown>;
  opaque?: FreezoneVideoCapabilityOpaqueField[];
  capabilityEnvelopeVersion?: number;
  capabilityRevision?: string;
  resolutionOptions?: string[];
  advertisedResolutionOptions?: string[];
  runtimeResolutionOptions?: string[];
  runtimeRejectedResolutionOptions?: string[];
  runtimeCapabilityStatus?: "verified" | "degraded" | "unknown";
  runtimeCapabilityNote?: string;
  /** Fixed pixel size slots (e.g. Gemini/Veo "992x432") when the model uses literal sizes. */
  sizeOptions?: string[];
  aspectRatioOptions?: string[];
  qualityOptions?: string[];
  /**
   * Advanced parameters declared by this model's capability contract (for
   * example Midjourney `stylize` / `weird` / `chaos` / `personalisation`).
   * Absent means the catalog predates advanced parameters; an empty array is an
   * explicit declaration that the model has none.
   */
  advancedParamsSchema?: FreezoneAdvancedParamSchema[];
  /** Contract defaults for `advancedParamsSchema`, keyed by parameter. */
  advancedParamDefaults?: Record<string, unknown>;
  supportsCustomAspectRatio?: boolean;
  supportsCustomResolution?: boolean;
  capabilitySource?: 'profile' | 'upstream' | 'runtime' | 'unknown';
  /** 出线合同来源：渠道自带（随渠道增删）/ 源码种子（未验证）/ 通用兜底。 */
  wireContractSource?: 'channel' | 'seed' | 'default';
  wireContractSourceLabel?: string;
  wireContractProfileId?: string;
  wireContractEvidence?: string;
  wireContractVerified?: boolean;
  wireContractConflictsWithSeed?: string[];
  wireContractError?: string;
  nativeAudio?: "unsupported" | "optional" | "required";
  minDuration?: number | null;
  maxDuration?: number | null;
  durationOptions?: number[];
  fpsOptions?: number[];
  inputSlots?: string[];
  returnLastFrame?: boolean;
  supportsCustomDuration?: boolean;
  durationParameterEnabled?: boolean;
  sceneOptimizeOptions?: Array<'anime' | 'realistic'>;
  defaultSceneOptimize?: 'anime' | 'realistic' | null;
  parameterDefaults?: {
    resolution?: string;
    durationSeconds?: number;
    aspectRatio?: string;
    generateAudio?: boolean;
    strategy?: 'balanced';
  };
  /** Model-center default used by newly created nodes. */
  isDefault?: boolean;
  family?: 'happyhorse' | 'firefly-seedance2' | 'grok-video-channel' | 'seedance-1x' | 'seedance-2' | 'seedance-2-value' | 'wokey-jimeng' | 'kacang-933' | 'kacang-mini-h3' | 'kacang-kling-v2v' | 'prompt-hubs-sd' | 'kling-3.0' | 'prompt-hubs-flex' | 'direct' | 'generic';
  supportedModes?: string[];
  referenceLimits?: Record<
    string,
    { image: number; video: number; audio: number; total?: number }
  >;
  supportsHumanReview?: boolean;
  channelLabel?: string;
  useCase?: string;
  priceHint?: string;
  recommendation?: 'default' | 'recommended' | 'specialist' | 'premium' | 'available';
  sortRank?: number;
  /** When false/undefined-with-disabled, picker greys the option out. */
  enabled?: boolean;
  disabled?: boolean;
  disabledReason?: string | null;
}

/** Offline catalog used only when /freezone/video/models is unreachable. */
export const VIDEO_CHANNEL_OFFLINE_FALLBACK_REASON =
  '视频渠道状态未知（模型列表拉取失败）。请确认后端已启动，并在设置中配置直连视频模型。';

export const SHARED_PROVIDERS: ProviderOption[] = [
  { id: 'direct', label: '模型中心' },
];

export const SHARED_MODELS: ModelOption[] = [];

export const DEFAULT_SHARED_MODEL_ID = '';

// Video generation models. `id` is the raw backend model id sent to
// /freezone/video/gen so we don't need a separate apiModel mapping.
export const VIDEO_PROVIDERS: ProviderOption[] = [
  { id: 'direct', label: '直连视频 API' },
];

/** Direct endpoints are configured locally; stale NewAPI fallbacks are never shown. */
export const VIDEO_MODELS: ModelOption[] = [];

// Matches the backend `FreezoneVideoGenRequest.model` default. The picker
// hydrates the live list via /freezone/video/models, but this id is what the
// canvas store uses on first node creation before that fetch resolves (and
// when no previously-picked model has been remembered).
export const DEFAULT_VIDEO_MODEL_ID = '';

export type ProviderModelDomain = 'image' | 'video';

interface ProviderModelPickerProps {
  selectedModelId: string;
  onChange: (modelId: string) => void;
  providers?: ProviderOption[];
  models?: ModelOption[];
  /**
   * Selects which freezone models endpoint backs the picker when no explicit
   * `models` prop is provided (`image` → /freezone/image/models, `video` →
   * /freezone/video/models). Defaults to `image` so existing image-node call
   * sites are unaffected.
   */
  domain?: ProviderModelDomain;
  className?: string;
  popoverPlacement?: 'top' | 'bottom';
  /**
   * Returns a disabled reason for a given model option, or null when the model
   * is selectable. When non-null, that option is rendered greyed-out and not
   * clickable, with the reason shown as a hover tooltip. Used by the video node
   * to block Seedance 1.0 models while reference media is attached.
   */
  getOptionDisabledReason?: (model: ModelOption) => string | null;
}

export function ProviderModelPicker({
  selectedModelId,
  onChange,
  providers: _providers = SHARED_PROVIDERS,
  models,
  domain = 'image',
  className,
  popoverPlacement = 'top',
  getOptionDisabledReason,
}: ProviderModelPickerProps) {
  const { t } = useTranslation();
  // When the caller supplies an explicit `models` prop we don't fire any API
  // request — pass `null` to both hooks so they no-op. Otherwise the active
  // hook is picked by `domain`, and the inactive one is fed `null` to stay
  // dormant. (React still calls both hooks unconditionally so the call order
  // is stable across renders.)
  const skipFetch = models ? null : undefined;
  const imageHook = useFreezoneImageModels(domain === 'image' ? skipFetch : null);
  const videoHook = useFreezoneVideoModels(domain === 'video' ? skipFetch : null);
  const apiModels = domain === 'video' ? videoHook.models : imageHook.models;
  const effectiveModels = models ?? apiModels;
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const [isOpen, setIsOpen] = useState(false);
  const [popoverPosition, setPopoverPosition] = useState<{
    left: number;
    top: number;
  } | null>(null);
  // 禁用项的 hover 提示。自渲染成一个 z 高于弹窗(z-[10001] > z-[10000])的浮层,
  // 锚定到当前项的右下角并 portal 到 body,避免被弹窗遮挡 / 被列表 overflow 裁剪。
  const [disabledTooltip, setDisabledTooltip] = useState<{
    reason: string;
    left: number;
    top: number;
  } | null>(null);
  const selectedModel = selectLiveModel(effectiveModels, selectedModelId || null);
  const displayedModels = domain === 'video'
    ? [...effectiveModels].sort((left, right) => (right.sortRank ?? 0) - (left.sortRank ?? 0))
    : effectiveModels;

  useEffect(() => {
    // A stale/disabled id resolves to no selected model and is therefore left
    // untouched.  Empty and legacy aliases may still canonicalize to the live
    // catalog id.
    if (selectedModel && selectedModel.id !== selectedModelId) {
      onChange(selectedModel.id);
    }
  }, [onChange, selectedModel, selectedModelId]);

  const syncPopoverPosition = () => {
    const trigger = triggerRef.current;
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    const margin = 8;
    const left = Math.min(
      Math.max(margin, rect.left),
      window.innerWidth - MODEL_PICKER_POPOVER_WIDTH - margin,
    );
    const top = popoverPlacement === 'top'
      ? rect.top - 8
      : rect.bottom + 8;
    setPopoverPosition({ left, top });
  };

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
  }, [isOpen, popoverPlacement]);

  return (
    <div className={`relative ${className ?? ''}`}>
      <button
        ref={triggerRef}
        type="button"
        onClick={(event) => {
          event.stopPropagation();
          setIsOpen((prev) => !prev);
        }}
        className={NODE_TEXT_CONTROL_TRIGGER_CLASS}
      >
        <Box className={NODE_TEXT_CONTROL_ICON_CLASS} />
        <span className="font-medium">
          {(selectedModel?.label ?? (selectedModelId ? `已失效 · ${selectedModelId}` : '未配置模型'))}
        </span>
        <ChevronDown className="h-3 w-3 text-text-muted/90" />
      </button>
      {isOpen && popoverPosition && createPortal(
        <div
          ref={popoverRef}
          className={MODEL_PICKER_POPOVER_CLASS}
          style={{
            left: popoverPosition.left,
            top: popoverPosition.top,
            transform: popoverPlacement === 'top' ? 'translateY(-100%)' : undefined,
          }}
          onPointerDown={(event) => event.stopPropagation()}
          onClick={(event) => event.stopPropagation()}
        >
          <div className="flex flex-col gap-0.5">
            {displayedModels.map((model) => {
              const isActive = selectedModel?.id === model.id;
              const channelDisabled =
                model.disabled === true || model.enabled === false
                  ? (model.disabledReason?.trim() || '当前模型不可用')
                  : null;
              const capabilityDisabled = getOptionDisabledReason?.(model) ?? null;
              const disabledReason = channelDisabled ?? capabilityDisabled;
              const isDisabled = disabledReason != null && !isActive;
              const optionInner = (
                <>
                  {isActive ? (
                    <Check className="h-3.5 w-3.5 shrink-0" />
                  ) : (
                    <span className="inline-block h-3.5 w-3.5 shrink-0" />
                  )}
                  <span className="min-w-0 flex-1">
                    <span className="block truncate">{model.label}</span>
                    {(() => {
                      const contractChip = wireContractChip(model);
                      const details = [model.channelLabel, model.useCase, model.priceHint, contractChip]
                        .filter(Boolean)
                        .join(' · ');
                      if (!details) return null;
                      return (
                        <span className="block truncate text-[10px] font-normal leading-4 text-text-muted/75">
                          {details}
                        </span>
                      );
                    })()}
                  </span>
                </>
              );
              const optionClass = `${MODEL_PICKER_OPTION_BASE_CLASS} ${
                isActive
                  ? 'bg-white/[0.13] text-text-dark ring-1 ring-white/24'
                  : isDisabled
                    ? 'cursor-not-allowed text-text-muted/40'
                    : 'text-text-muted/95 hover:bg-white/[0.11] hover:text-text-dark'
              }`;
              if (isDisabled) {
                return (
                  <button
                    key={model.id}
                    type="button"
                    aria-disabled
                    onClick={(event) => event.stopPropagation()}
                    onMouseEnter={(event) => {
                      const rect = event.currentTarget.getBoundingClientRect();
                      setDisabledTooltip({
                        reason: disabledReason,
                        // 锚定到当前项的右下角:水平从图标右侧起,垂直略压住项底边。
                        left: rect.left + 36,
                        top: rect.bottom - 6,
                      });
                    }}
                    onMouseLeave={() => setDisabledTooltip(null)}
                    className={optionClass}
                  >
                    {optionInner}
                  </button>
                );
              }
              return (
                <button
                  key={model.id}
                  type="button"
                  onClick={() => {
                    onChange(model.id);
                    setIsOpen(false);
                  }}
                  className={optionClass}
                >
                  {optionInner}
                </button>
              );
            })}
            {effectiveModels.length === 0 && (
              <span className="px-3 py-2 text-xs text-text-muted">
                {t('modelPicker.empty')}
              </span>
            )}
          </div>
        </div>,
        document.body,
      )}
      {isOpen && disabledTooltip && createPortal(
        <div
          className="pointer-events-none fixed z-[10001] max-w-[240px] rounded-lg bg-neutral-800/95 px-3 py-2 text-xs leading-5 text-white shadow-lg"
          style={{ left: disabledTooltip.left, top: disabledTooltip.top }}
        >
          {disabledTooltip.reason}
        </div>,
        document.body,
      )}
    </div>
  );
}
