// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { ImageQuality, ImageSize } from '@/features/canvas/domain/canvasNodes';

export interface ScriptImageQualityOption {
  value: ImageQuality;
  label: string;
}

interface ScriptImageQualityControlsProps {
  size: ImageSize;
  sizeOptions: readonly ImageSize[];
  quality: ImageQuality | null;
  qualityOptions: readonly ScriptImageQualityOption[];
  disabled?: boolean;
  onSizeChange: (next: ImageSize) => void;
  onQualityChange: (next: ImageQuality) => void;
}

const OPTION_CLASS =
  'h-7 rounded-[8px] px-2 text-[12px] transition-colors disabled:cursor-not-allowed disabled:opacity-40';

function optionClass(active: boolean): string {
  return `${OPTION_CLASS} ${
    active ? 'bg-white/[0.14] text-text-dark' : 'text-text-muted hover:bg-white/[0.06] hover:text-text-dark'
  }`;
}

/**
 * 脚本出图弹层里的「画质 / 清晰度」两组档位。
 *
 * 与图片节点同一条能力口径：只展示当前模型声明支持的档位，切模型后旧值若不在
 * 新模型的能力里，由调用方回落到模型默认，而不是把不支持的参数发给上游。
 */
export function ScriptImageQualityControls({
  size,
  sizeOptions,
  quality,
  qualityOptions,
  disabled = false,
  onSizeChange,
  onQualityChange,
}: ScriptImageQualityControlsProps) {
  return (
    <>
      <div className="flex items-center justify-between gap-3">
        <span className="shrink-0 text-[12px] text-text-muted">画质</span>
        {qualityOptions.length > 0 ? (
          <div className="flex flex-wrap justify-end gap-1" role="group" aria-label="出图画质">
            {qualityOptions.map((option) => (
              <button
                key={option.value}
                type="button"
                aria-pressed={option.value === quality}
                disabled={disabled}
                onClick={() => onQualityChange(option.value)}
                className={optionClass(option.value === quality)}
              >
                {option.label}
              </button>
            ))}
          </div>
        ) : (
          <span className="text-[11px] text-text-muted/60">该模型未提供画质档位</span>
        )}
      </div>
      <div className="flex items-center justify-between gap-3">
        <span className="shrink-0 text-[12px] text-text-muted">清晰度</span>
        <div className="flex flex-wrap justify-end gap-1" role="group" aria-label="出图清晰度">
          {sizeOptions.map((option) => (
            <button
              key={option}
              type="button"
              aria-pressed={option === size}
              disabled={disabled}
              onClick={() => onSizeChange(option)}
              className={optionClass(option === size)}
            >
              {option}
            </button>
          ))}
        </div>
      </div>
    </>
  );
}
