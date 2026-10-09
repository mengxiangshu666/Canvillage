// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { type ChangeEvent, useEffect } from 'react';

import { UiSelect } from '@/components/ui';
import {
  resolveDirectCanvasModelSelection,
  useDirectModelCatalog,
} from '@/features/canvas/hooks/useDirectModelCatalog';
import type { DirectModelKind } from '@/lib/queries/model-gateway';

const KIND_LABEL: Record<DirectModelKind, string> = {
  chat: '对话',
  agent: 'Agent',
  text: '文字',
  vision: '视觉',
  image: '生图',
  embedding: '向量',
};

type DirectModelPickerProps = {
  kind: DirectModelKind;
  value?: string | null;
  onChange: (modelId: string) => void;
  disabled?: boolean;
  className?: string;
  ariaLabel?: string;
  requiredMode?: string;
};

/** Compact node-level selector backed by the shared direct model registry. */
export function DirectModelPicker({
  kind,
  value,
  onChange,
  disabled = false,
  className = '',
  ariaLabel,
  requiredMode,
}: DirectModelPickerProps) {
  const { models, defaultModel, isLoading } = useDirectModelCatalog(kind, requiredMode);
  const selection = resolveDirectCanvasModelSelection(value, models);
  const selected = selection.modelId;
  useEffect(() => {
    if (!isLoading && selection.status === 'default' && selected && selected !== value) {
      onChange(selected);
    }
  }, [isLoading, onChange, selected, selection.status, value]);

  if (!isLoading && models.length === 0) {
    return (
      <span className="max-w-[132px] truncate text-[11px] text-text-muted/70">
        未配置{KIND_LABEL[kind]}模型
      </span>
    );
  }
  const handleChange = (event: ChangeEvent<HTMLSelectElement>) => onChange(event.target.value);
  return (
    <UiSelect
      value={selected}
      onChange={handleChange}
      disabled={disabled || isLoading}
      aria-label={ariaLabel ?? `${KIND_LABEL[kind]}模型`}
      className={`!h-8 !max-w-[176px] !rounded-lg !border-white/[0.1] !bg-white/[0.04] !px-2.5 !text-[12px] !text-text-dark ${className}`}
      menuClassName="!z-[320] !min-w-[220px] !border-white/10 !bg-[#202024] !text-text-dark"
    >
      {!selected ? (
        <option value="">
          {selection.status === 'stale'
            ? `已失效 · ${selection.requestedId}`
            : defaultModel
              ? `默认 · ${defaultModel.label}`
              : `加载${KIND_LABEL[kind]}模型…`}
        </option>
      ) : null}
      {models.map((model) => (
        <option key={model.catalogId} value={model.catalogId}>
          {model.label} · {model.modelId}
        </option>
      ))}
    </UiSelect>
  );
}
