// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useTranslation } from 'react-i18next';

import type { ExtraParamDefinition } from '@/features/canvas/models/types';
import { UiCheckbox, UiInput, UiSelect } from '@/components/ui';

/**
 * 模型能力合同声明的高级参数（MJ 系的 stylize / chaos / weird / personalisation …）。
 *
 * 键与可选值完全由 `selectedModel.extraParamsSchema`（后端 `advanced_params_schema`）
 * 决定，调用方只负责把值存进节点的 `extraParams`、提交时拼成 `advanced_settings`。
 * 这个组件是唯一的一份渲染实现：`ModelParamsControls` 的「参数」面板与
 * `ImageGenNode` 的参数浮层都渲染它，避免两处各写一套之后行为漂移。
 */

export const DEFAULT_ADVANCED_PARAMS_GROUP_CLASS_NAME =
  'space-y-2 rounded-xl border border-[rgba(255,255,255,0.1)] bg-bg-dark/65 p-3';
export const DEFAULT_ADVANCED_PARAM_ITEM_CLASS_NAME =
  'space-y-2 rounded-lg border border-[rgba(255,255,255,0.08)] bg-black/10 p-2';
export const DEFAULT_ADVANCED_PARAM_LABEL_CLASS_NAME = 'text-xs font-medium text-text-dark';
export const DEFAULT_ADVANCED_PARAM_FIELD_CLASS_NAME = 'h-9 text-sm';

/** `t(key)` 未命中时（i18next 原样返回 key）退回到 schema 自带的字面量。 */
export function resolveTranslatedText(
  t: (key: string) => string,
  key: string | undefined,
  fallback: string | undefined
): string {
  if (!key) {
    return fallback ?? '';
  }

  const translated = t(key);
  return translated === key ? (fallback ?? key) : translated;
}

/**
 * 取值优先级：节点上已存的值 > 模型合同声明的默认值 > schema 里的 defaultValue。
 * 节点值可能是 0 / false / ''，所以必须按类型判存在，不能用真值判断。
 */
export function resolveExtraParamValue(
  key: string,
  extraParams: Record<string, unknown> | undefined,
  defaultExtraParams: Record<string, unknown> | undefined,
  schemaDefault: boolean | number | string | undefined
): boolean | number | string | undefined {
  const currentValue = extraParams?.[key];
  if (typeof currentValue === 'boolean' || typeof currentValue === 'number' || typeof currentValue === 'string') {
    return currentValue;
  }

  const modelDefaultValue = defaultExtraParams?.[key];
  if (
    typeof modelDefaultValue === 'boolean' ||
    typeof modelDefaultValue === 'number' ||
    typeof modelDefaultValue === 'string'
  ) {
    return modelDefaultValue;
  }

  return schemaDefault;
}

export interface ImageModelAdvancedParamFieldsProps {
  /** 已剔除行内渲染项（如 thinking_level）之后的 schema。空数组 ⇒ 调用方不渲染。 */
  schema: ExtraParamDefinition[];
  extraParams?: Record<string, unknown>;
  defaultExtraParams?: Record<string, unknown>;
  onExtraParamChange?: (key: string, value: boolean | number | string) => void;
  showHeading?: boolean;
  showDescription?: boolean;
  heading?: string;
  groupClassName?: string;
  itemClassName?: string;
  labelClassName?: string;
  fieldClassName?: string;
}

export function ImageModelAdvancedParamFields({
  schema,
  extraParams,
  defaultExtraParams,
  onExtraParamChange,
  showHeading = false,
  showDescription = false,
  heading,
  groupClassName = DEFAULT_ADVANCED_PARAMS_GROUP_CLASS_NAME,
  itemClassName = DEFAULT_ADVANCED_PARAM_ITEM_CLASS_NAME,
  labelClassName = DEFAULT_ADVANCED_PARAM_LABEL_CLASS_NAME,
  fieldClassName = DEFAULT_ADVANCED_PARAM_FIELD_CLASS_NAME,
}: ImageModelAdvancedParamFieldsProps) {
  const { t } = useTranslation();

  if (schema.length === 0) return null;

  return (
    <>
      {showHeading && (
        <div className="mb-2 text-xs text-text-muted">{heading ?? t('modelParams.extraOptions')}</div>
      )}
      <div className={groupClassName}>
        {schema.map((definition) => {
        const translatedLabel = resolveTranslatedText(
          t,
          definition.labelKey,
          definition.label
        );
        const translatedDescription = definition.description || definition.descriptionKey
          ? resolveTranslatedText(t, definition.descriptionKey, definition.description)
          : '';
        const resolvedValue = resolveExtraParamValue(
          definition.key,
          extraParams,
          defaultExtraParams,
          definition.defaultValue
        );

        return (
          <div key={definition.key} className={itemClassName}>
            <div>
              <div className={labelClassName}>{translatedLabel}</div>
              {showDescription && translatedDescription && (
                <div className="mt-0.5 text-[11px] leading-4 text-text-muted">
                  {translatedDescription}
                </div>
              )}
            </div>

            {definition.type === 'enum' && definition.options && (
              <UiSelect
                value={String(resolvedValue ?? '')}
                onChange={(event) =>
                  onExtraParamChange?.(definition.key, event.target.value)
                }
                className={fieldClassName}
              >
                {definition.options.map((option) => (
                  <option key={option.value} value={option.value}>
                    {resolveTranslatedText(t, option.labelKey, option.label)}
                  </option>
                ))}
              </UiSelect>
            )}

            {definition.type === 'boolean' && (
              <label className="flex cursor-pointer items-center gap-2 text-sm text-text-dark">
                <UiCheckbox
                  checked={Boolean(resolvedValue)}
                  onCheckedChange={(checked) =>
                    onExtraParamChange?.(definition.key, checked)
                  }
                />
                <span>{translatedLabel}</span>
              </label>
            )}

            {definition.type === 'number' && (
              <UiInput
                type="number"
                min={definition.min}
                max={definition.max}
                step={definition.step}
                value={typeof resolvedValue === 'number' ? String(resolvedValue) : ''}
                onChange={(event) =>
                  onExtraParamChange?.(definition.key, Number(event.target.value))
                }
                className={fieldClassName}
              />
            )}

            {definition.type === 'string' && (
              <UiInput
                value={typeof resolvedValue === 'string' ? resolvedValue : ''}
                onChange={(event) =>
                  onExtraParamChange?.(definition.key, event.target.value)
                }
                className={fieldClassName}
              />
            )}
          </div>
        );
      })}
      </div>
    </>
  );
}
