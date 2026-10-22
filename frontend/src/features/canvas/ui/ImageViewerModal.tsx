// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useRef, useState, type ReactElement } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronLeft, ChevronRight, RotateCcw, X } from 'lucide-react';
import { UI_CONTENT_OVERLAY_INSET_CLASS } from '@/components/ui/motion';
import { useImageViewerTransform } from '../hooks/useImageViewerTransform';
import {
  MEDIA_VIEWER_CLOSE_BUTTON_CLASS,
  MEDIA_VIEWER_CLOSE_ICON_CLASS,
} from './closeButtonStyles';

export interface ImageViewerModalProps {
  open: boolean;
  imageUrl: string;
  imageList: string[];
  currentIndex: number;
  onClose: () => void;
  onNavigate: (direction: 'prev' | 'next') => void;
}

export function ImageViewerModal({
  open,
  imageUrl,
  imageList,
  currentIndex,
  onClose,
  onNavigate,
}: ImageViewerModalProps): ReactElement | null {
  const { t } = useTranslation();
  const viewerControlClass =
    'inline-flex h-8 items-center justify-center rounded-[8px] border border-white/10 bg-white/[0.055] px-3 text-xs tabular-nums text-white/82 transition-colors hover:bg-white/[0.09] hover:text-white';
  const [isVisible, setIsVisible] = useState(false);
  const [overlayOpacity, setOverlayOpacity] = useState(0);
  const [displayImageUrl, setDisplayImageUrl] = useState(imageUrl);
  const closeTimerRef = useRef<number | null>(null);

  const {
    containerRef,
    imageRef,
    scaleDisplayRef,
    viewerOpacity,
    resetView,
    handleImageMouseDown,
    handleContainerMouseMove,
    handleContainerMouseUp,
    handleImageMouseMove,
    handleImageLoad,
    isPointOnImageContent,
  } = useImageViewerTransform(open && isVisible);

  useEffect(() => {
    if (!isVisible) return;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = '';
    };
  }, [isVisible]);

  useEffect(() => {
    if (open) {
      setDisplayImageUrl(imageUrl);
      setIsVisible(true);
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
      setDisplayImageUrl('');
    }, 400);
    return () => {
      if (closeTimerRef.current) {
        clearTimeout(closeTimerRef.current);
        closeTimerRef.current = null;
      }
    };
  }, [open, isVisible]);

  useEffect(() => {
    if (!open || !imageUrl) {
      return;
    }
    setDisplayImageUrl(imageUrl);
  }, [open, imageUrl]);

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
    resetView();
  }, [open, imageUrl, resetView]);

  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'ArrowLeft') {
        onNavigate('prev');
      } else if (e.key === 'ArrowRight') {
        onNavigate('next');
      } else if (e.key === 'Escape') {
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [open, onNavigate, onClose]);

  if (!isVisible) return null;

  return (
    <div
      className={`fixed ${UI_CONTENT_OVERLAY_INSET_CLASS} z-[100] overflow-hidden bg-[#111214]/97 backdrop-blur-sm`}
      style={{
        opacity: overlayOpacity,
        transition: 'opacity 400ms ease',
        pointerEvents: open ? 'auto' : 'none',
      }}
    >
      <div
        ref={containerRef}
        className="absolute inset-0 flex items-center justify-center overflow-hidden p-4"
        style={{ overscrollBehavior: 'contain' }}
        onMouseMove={handleContainerMouseMove}
        onMouseUp={handleContainerMouseUp}
        onMouseLeave={handleContainerMouseUp}
        onClick={(e) => {
          if (e.target === e.currentTarget) onClose();
        }}
      >
        <button
          type="button"
          onClick={onClose}
          className={`absolute right-5 top-5 z-20 ${MEDIA_VIEWER_CLOSE_BUTTON_CLASS}`}
          title={t('common.close', '关闭')}
          aria-label={t('common.close', '关闭')}
        >
          <X className={MEDIA_VIEWER_CLOSE_ICON_CLASS} />
        </button>

        <div className="relative">
          <img
            ref={imageRef}
            src={displayImageUrl}
            alt={t('viewer.imageAlt', '图片')}
            className="select-none transition-opacity duration-300"
            style={{
              opacity: viewerOpacity * overlayOpacity,
              transformOrigin: 'center',
              width: '95vw',
              height: 'calc(100dvh - 96px)',
              objectFit: 'contain',
            }}
            onLoad={handleImageLoad}
            onMouseDown={handleImageMouseDown}
            onMouseMove={handleImageMouseMove}
            onClick={(e) => {
              if (isPointOnImageContent(e.clientX, e.clientY)) {
                e.stopPropagation();
              } else {
                onClose();
              }
            }}
            draggable={false}
          />
        </div>

        <div className="absolute bottom-5 left-1/2 flex -translate-x-1/2 items-center gap-1 rounded-[12px] border border-white/10 bg-[#1d1d20]/92 p-1 text-white shadow-[0_12px_32px_rgba(0,0,0,0.38),inset_0_1px_0_rgba(255,255,255,0.04)] backdrop-blur-2xl">
          {imageList.length > 1 && (
            <>
              <button
                type="button"
                onClick={() => onNavigate('prev')}
                disabled={currentIndex <= 0}
                className={`${viewerControlClass} w-8 px-0 disabled:cursor-not-allowed disabled:opacity-35`}
                title={t('viewer.prev', '上一张')}
                aria-label={t('viewer.prev', '上一张')}
              >
                <ChevronLeft className="h-4 w-4" />
              </button>
              <div className={`${viewerControlClass} min-w-[64px]`}>
                {currentIndex + 1} / {imageList.length}
              </div>
            </>
          )}
          <div
            ref={scaleDisplayRef}
            className={`${viewerControlClass} min-w-[64px] pointer-events-none`}
          >
            100%
          </div>
          <button
            type="button"
            onClick={resetView}
            className={viewerControlClass}
            title={t('viewer.reset', '重置视图')}
            aria-label={t('viewer.reset', '重置视图')}
          >
            <RotateCcw className="h-3.5 w-3.5" />
          </button>
          {imageList.length > 1 && (
            <button
              type="button"
              onClick={() => onNavigate('next')}
              disabled={currentIndex >= imageList.length - 1}
              className={`${viewerControlClass} w-8 px-0 disabled:cursor-not-allowed disabled:opacity-35`}
              title={t('viewer.next', '下一张')}
              aria-label={t('viewer.next', '下一张')}
            >
              <ChevronRight className="h-4 w-4" />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
