// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useRef, useState, type ReactElement } from 'react';
import { useTranslation } from 'react-i18next';
import { Maximize2, Pause, Play, Volume2, VolumeX, X } from 'lucide-react';
import {
  MEDIA_VIEWER_CLOSE_BUTTON_CLASS,
  MEDIA_VIEWER_CLOSE_ICON_CLASS,
} from './closeButtonStyles';

export interface VideoViewerModalProps {
  open: boolean;
  videoUrl: string;
  title?: string;
  onClose: () => void;
}

export function VideoViewerModal({
  open,
  videoUrl,
  title,
  onClose,
}: VideoViewerModalProps): ReactElement | null {
  const { t } = useTranslation();
  const [isVisible, setIsVisible] = useState(false);
  const [overlayOpacity, setOverlayOpacity] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [muted, setMuted] = useState(false);
  const closeTimerRef = useRef<number | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const viewerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!isVisible) return;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = '';
    };
  }, [isVisible]);

  useEffect(() => {
    if (open) {
      setIsVisible(true);
      setIsPlaying(false);
      setCurrentTime(0);
      setDuration(0);
      if (closeTimerRef.current) {
        clearTimeout(closeTimerRef.current);
        closeTimerRef.current = null;
      }
      setOverlayOpacity(0);
      requestAnimationFrame(() => {
        setOverlayOpacity(1);
      });
      return;
    }
    if (!isVisible) return;
    setOverlayOpacity(0);
    closeTimerRef.current = window.setTimeout(() => {
      setIsVisible(false);
      const el = videoRef.current;
      if (el) {
        el.pause();
      }
      setIsPlaying(false);
    }, 320);
    return () => {
      if (closeTimerRef.current) {
        clearTimeout(closeTimerRef.current);
        closeTimerRef.current = null;
      }
    };
  }, [open, isVisible]);

  useEffect(() => {
    return () => {
      if (closeTimerRef.current) {
        clearTimeout(closeTimerRef.current);
        closeTimerRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [open, onClose]);

  if (!isVisible) return null;

  const togglePlayback = () => {
    const el = videoRef.current;
    if (!el) return;
    if (el.paused) {
      void el.play();
    } else {
      el.pause();
    }
  };

  const toggleMuted = () => {
    const el = videoRef.current;
    if (!el) return;
    const nextMuted = !el.muted;
    el.muted = nextMuted;
    setMuted(nextMuted);
  };

  const handleSeek = (value: string) => {
    const el = videoRef.current;
    if (!el) return;
    const nextTime = Number(value);
    el.currentTime = Number.isFinite(nextTime) ? nextTime : 0;
    setCurrentTime(el.currentTime);
  };

  const enterFullscreen = () => {
    const el = viewerRef.current;
    if (!el || !el.requestFullscreen) return;
    void el.requestFullscreen();
  };

  const progress = duration > 0 ? (currentTime / duration) * 100 : 0;

  return (
    <div
      ref={viewerRef}
      className="fixed inset-0 z-[220] overflow-hidden bg-[#0b0c0e]/97 backdrop-blur-sm"
      style={{
        opacity: overlayOpacity,
        transition: 'opacity 320ms ease',
        pointerEvents: open ? 'auto' : 'none',
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="pointer-events-none absolute inset-x-0 top-0 z-20 flex items-center justify-between px-6 py-5 text-white">
        <div className="max-w-[min(60vw,560px)] truncate rounded-[9px] border border-white/10 bg-[#1d1d20]/76 px-3 py-1.5 text-xs font-medium text-white/82 shadow-[0_10px_24px_rgba(0,0,0,0.28)] backdrop-blur-xl">
          {title ?? t('viewer.videoTitleFallback', '视频')}
        </div>
        <button
          type="button"
          className={`pointer-events-auto ${MEDIA_VIEWER_CLOSE_BUTTON_CLASS}`}
          onClick={onClose}
          title={t('common.close', '关闭')}
          aria-label={t('common.close', '关闭')}
        >
          <X className={MEDIA_VIEWER_CLOSE_ICON_CLASS} />
        </button>
      </div>

      <div
        className="absolute inset-0 flex items-center justify-center px-8 py-24"
        onClick={(event) => {
          if (event.target === event.currentTarget) onClose();
        }}
      >
        <video
          ref={videoRef}
          src={videoUrl}
          className="max-h-[calc(100dvh-11rem)] max-w-[min(92vw,1280px)] rounded-[12px] border border-white/10 bg-black object-contain shadow-[0_24px_80px_rgba(0,0,0,0.55)]"
          style={{ width: 'auto', height: 'auto' }}
          autoPlay
          playsInline
          onClick={(event) => {
            event.stopPropagation();
            togglePlayback();
          }}
          onPlay={() => setIsPlaying(true)}
          onPause={() => setIsPlaying(false)}
          onLoadedMetadata={(event) => {
            setDuration(event.currentTarget.duration || 0);
            setMuted(event.currentTarget.muted);
          }}
          onDurationChange={(event) => setDuration(event.currentTarget.duration || 0)}
          onVolumeChange={(event) => setMuted(event.currentTarget.muted)}
          onTimeUpdate={(event) => setCurrentTime(event.currentTarget.currentTime)}
          onEnded={() => setIsPlaying(false)}
        />
      </div>

      <div className="absolute bottom-4 left-1/2 z-20 flex w-[min(92vw,1280px)] -translate-x-1/2 flex-wrap items-center gap-2 rounded-[12px] border border-white/10 bg-[#1d1d20]/88 px-2 py-2 text-white shadow-[0_16px_48px_rgba(0,0,0,0.4),inset_0_1px_0_rgba(255,255,255,0.04)] backdrop-blur-2xl sm:bottom-6 sm:flex-nowrap">
        <button
          type="button"
          onClick={togglePlayback}
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[8px] text-white/88 transition-colors hover:bg-white/10 hover:text-white"
          aria-label={isPlaying ? t('common.pause', '暂停') : t('common.play', '播放')}
        >
          {isPlaying ? <Pause className="h-[18px] w-[18px]" /> : <Play className="h-[18px] w-[18px]" />}
        </button>
        <span className="w-[72px] shrink-0 text-xs tabular-nums text-white/72">
          {formatVideoTime(currentTime)} / {formatVideoTime(duration)}
        </span>
        <input
          type="range"
          min={0}
          max={duration || 0}
          step={0.01}
          value={Math.min(currentTime, duration || currentTime)}
          onChange={(event) => handleSeek(event.target.value)}
          className="h-1 min-w-[160px] flex-1 cursor-pointer appearance-none rounded-full bg-white/18 [&::-moz-range-thumb]:h-3 [&::-moz-range-thumb]:w-3 [&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:border-0 [&::-moz-range-thumb]:bg-[#7de6ff] [&::-webkit-slider-thumb]:h-3 [&::-webkit-slider-thumb]:w-3 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-[#7de6ff]"
          style={{
            background: `linear-gradient(to right, rgba(125,230,255,0.96) 0%, rgba(125,230,255,0.96) ${progress}%, rgba(255,255,255,0.14) ${progress}%, rgba(255,255,255,0.14) 100%)`,
          }}
          aria-label={t('viewer.videoSeek', '视频进度')}
        />
        <button
          type="button"
          onClick={toggleMuted}
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[8px] text-white/78 transition-colors hover:bg-white/10 hover:text-white"
          aria-label={muted ? t('viewer.unmute', '取消静音') : t('viewer.mute', '静音')}
        >
          {muted ? <VolumeX className="h-[18px] w-[18px]" /> : <Volume2 className="h-[18px] w-[18px]" />}
        </button>
        <button
          type="button"
          onClick={enterFullscreen}
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[8px] text-white/78 transition-colors hover:bg-white/10 hover:text-white"
          aria-label={t('viewer.fullscreen', '全屏')}
        >
          <Maximize2 className="h-[18px] w-[18px]" />
        </button>
      </div>
    </div>
  );
}

function formatVideoTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) {
    return '0:00';
  }
  const totalSeconds = Math.floor(seconds);
  const minutes = Math.floor(totalSeconds / 60);
  const remainingSeconds = totalSeconds % 60;
  return `${minutes}:${String(remainingSeconds).padStart(2, '0')}`;
}
