// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useMemo, useRef, useState } from 'react';
import { Pause, Play, RotateCcw, Volume2, VolumeX } from 'lucide-react';
import type { FreezoneStoryScriptRow } from '@/api/scriptContract';
import { isRenderableImageSrc, resolveImageDisplayUrl } from '@/features/canvas/application/imageData';
import { parseDurationSeconds } from './scriptStats';
import { rowReferenceImageUrl, scriptRowShotNumber } from './scriptViews';
import type { ScriptPreviewFrame } from './scriptPreviewFrames';

export function ScriptRoughPreview({ rows, frames, videos }: { rows: FreezoneStoryScriptRow[]; frames?: ScriptPreviewFrame[]; videos?: Array<string | null> }) {
  const lengths = useMemo(() => rows.map(row => parseDurationSeconds(row.duration)), [rows]);
  const total = lengths.reduce<number>((sum, value) => sum + (value ?? 0), 0);
  const valid = rows.length > 0 && lengths.every(value => value != null);
  const [time, setTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(true);
  const [mediaError, setMediaError] = useState('');
  const player = useRef<HTMLVideoElement>(null);
  let cursor = 0;
  const index = lengths.findIndex(length => {
    cursor += length ?? 0;
    return time < cursor;
  });
  const rowIndex = index < 0 ? Math.max(0, rows.length - 1) : index;
  const shotStart = lengths.slice(0, rowIndex).reduce<number>((sum, length) => sum + (length ?? 0), 0);
  const videoUrl = videos?.[rowIndex] ?? null;
  useEffect(() => { setTime(0); setPlaying(false); }, [rows]);
  useEffect(() => { setMediaError(''); }, [videoUrl, rowIndex]);
  useEffect(() => {
    if (!playing || !valid || videoUrl) return;
    const start = performance.now() - time * 1000;
    const interval = window.setInterval(() => {
      const next = Math.min(shotStart + (lengths[rowIndex] ?? 0), total, (performance.now() - start) / 1000);
      setTime(next);
      if (next >= total) setPlaying(false);
    }, 50);
    return () => window.clearInterval(interval);
    // Playback uses a fixed origin; scrubbing pauses before changing time.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, valid, total, videoUrl, rowIndex]);
  useEffect(() => {
    const video = player.current;
    if (!video || !videoUrl) return;
    if (!playing) {
      video.pause();
      const local = Math.max(0, time - shotStart);
      if (Math.abs(video.currentTime - local) > 0.1) video.currentTime = local;
      return;
    }
    let active = true;
    void video.play().catch(() => { if (active) { setPlaying(false); setMediaError('视频播放失败，请检查素材后重试'); } });
    return () => { active = false; video.pause(); };
    // While playing, decoded media time drives the playhead instead of seeking every tick.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, videoUrl, rowIndex, playing ? null : time]);
  const row = rows[rowIndex];
  if (!row) return null;
  const reference = frames ? frames[rowIndex]?.url : rowReferenceImageUrl(row);
  const frameLabel = frames?.[rowIndex]?.label ?? '脚本参考帧 · 尚无生成分镜';
  const text = (value: unknown) => typeof value === 'string' ? value : '';
  return (
    <section className="nodrag nowheel mb-3 border-b border-white/10 pb-3" aria-label="粗剪预演">
      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-text-muted">
        <span>{videos?.some(Boolean) ? '素材粗剪 · 缺视频镜头使用静帧' : '静帧粗剪 · 运动与声音未验'}</span>
        <span>{videoUrl ? '当前镜头视频 · 按剧本时长取用' : frameLabel}</span>
        <button type="button" title={playing ? '暂停预演' : '播放预演'} aria-label={playing ? '暂停预演' : '播放预演'} disabled={!valid || Boolean(mediaError)} onClick={() => { if (time >= total) setTime(0); setPlaying(!playing); }} className="flex h-7 w-7 items-center justify-center disabled:opacity-40">
          {playing ? <Pause size={16} /> : <Play size={16} />}
        </button>
        <button type="button" title="回到开头" aria-label="回到开头" onClick={() => { setPlaying(false); setTime(0); }} className="flex h-7 w-7 items-center justify-center"><RotateCcw size={16} /></button>
        {videos?.some(Boolean) && <button type="button" title={muted ? '开启素材声音' : '关闭素材声音'} aria-label={muted ? '开启素材声音' : '关闭素材声音'} onClick={() => setMuted(!muted)} className="flex h-7 w-7 items-center justify-center">{muted ? <VolumeX size={16} /> : <Volume2 size={16} />}</button>}
        <span className="tabular-nums">{time.toFixed(1)} / {total.toFixed(1)}s</span>
        {!valid && <span className="text-amber-700 dark:text-amber-200">请补齐镜头时长</span>}
      </div>
      {mediaError && <p role="alert" className="mb-2 text-xs text-red-400">{mediaError}</p>}
      <div className="grid gap-3 md:grid-cols-2">
        <div className="flex aspect-video min-w-0 items-center justify-center overflow-hidden bg-black/30">
          {videoUrl ? <video key={`${rowIndex}:${videoUrl}`} ref={player} src={resolveImageDisplayUrl(videoUrl)} muted={muted} playsInline preload="metadata" aria-label={`预演第 ${scriptRowShotNumber(row, rowIndex)} 镜`} className="h-full w-full object-contain"
            onLoadedMetadata={event => {
              const video = event.currentTarget;
              if (!Number.isFinite(video.duration) || video.duration + 0.05 < (lengths[rowIndex] ?? 0)) { setPlaying(false); setMediaError('视频短于剧本镜头时长，请补齐素材'); return; }
              video.currentTime = Math.max(0, time - shotStart);
            }}
            onTimeUpdate={event => {
              if (!playing) return;
              const next = Math.min(shotStart + event.currentTarget.currentTime, shotStart + (lengths[rowIndex] ?? 0), total);
              setTime(next);
              if (next >= total) setPlaying(false);
            }}
            onEnded={() => { if (playing) { setTime(shotStart + (lengths[rowIndex] ?? 0)); if (rowIndex === rows.length - 1) setPlaying(false); } }}
            onError={() => { setPlaying(false); setMediaError('视频加载失败，请检查素材'); }}
          /> : reference && isRenderableImageSrc(reference) ? <img src={resolveImageDisplayUrl(reference)} alt={`第 ${scriptRowShotNumber(row, rowIndex)} 镜参考帧`} className="h-full w-full object-contain" /> : <p className="max-h-full overflow-auto p-3 text-xs text-text-muted">{text(row.visual_description) || '暂无参考帧'}</p>}
        </div>
        <div className="min-w-0 space-y-1 break-words text-xs text-text-muted">
          <p className="text-text-dark">镜 {scriptRowShotNumber(row, rowIndex)} · {text(row.shot)}</p>
          {(['shot_purpose', 'film_language', 'start_state', 'end_state', 'cut_reason', 'sound'] as const).map((key, i) => text(row[key]) && <p key={key}><span className="text-text-dark">{['观看目的', '拍法', '起点', '切点', '切镜理由', '声音计划'][i]}：</span>{text(row[key])}</p>)}
        </div>
      </div>
      <input aria-label="预演位置" type="range" min={0} max={total || 1} step={0.05} value={time} disabled={!valid} onChange={event => { setPlaying(false); setTime(Number(event.target.value)); }} className="mt-2 w-full" />
    </section>
  );
}
