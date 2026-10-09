// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useTranslation } from 'react-i18next';

export type VideoFrameCaptureMode = 'first' | 'last' | 'current';

/** 顺序与「节点能力」里的三个截帧能力一致：首帧、尾帧、当前帧。 */
export const VIDEO_FRAME_CAPTURE_MODES: readonly VideoFrameCaptureMode[] = [
  'first',
  'last',
  'current',
];

const CAPTURE_LABEL_KEYS: Record<VideoFrameCaptureMode, string> = {
  first: 'node.videoNode.frame.captureFirst',
  last: 'node.videoNode.frame.captureLast',
  // 相机按钮自己的 tooltip 是 captureCurrent（「点击截取当前帧」），菜单项用独立
  // 文案，避免把一句操作提示塞进菜单。
  current: 'node.videoNode.frame.captureCurrentItem',
};

interface VideoFrameCaptureMenuProps {
  onCapture: (mode: VideoFrameCaptureMode) => void;
  disabled?: boolean;
}

/** 视频播放器相机按钮的悬停菜单：首帧 / 尾帧 / 当前帧。 */
export function VideoFrameCaptureMenu({
  onCapture,
  disabled = false,
}: VideoFrameCaptureMenuProps) {
  const { t } = useTranslation();
  return (
    <div className="absolute bottom-full right-0 z-[10000] flex flex-col gap-1 rounded-lg border border-white/10 bg-surface-dark/95 p-1 text-xs shadow-2xl backdrop-blur-md">
      {VIDEO_FRAME_CAPTURE_MODES.map((mode) => (
        <button
          key={mode}
          type="button"
          disabled={disabled}
          onClick={() => onCapture(mode)}
          className="whitespace-nowrap rounded-md px-3 py-1.5 text-left text-text-dark transition-colors hover:bg-white/[0.08] disabled:cursor-not-allowed disabled:text-text-muted/60"
        >
          {t(CAPTURE_LABEL_KEYS[mode])}
        </button>
      ))}
    </div>
  );
}
