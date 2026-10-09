import { mediaNeedsCrossOrigin } from '@/features/canvas/application/imageData';

type CaptureOptions = { preload?: 'auto' | 'metadata'; timeoutMs?: number };

async function withVideoFrame<T>(
  src: string,
  seekSec: number,
  render: (video: HTMLVideoElement) => T | Promise<T>,
  options?: CaptureOptions,
): Promise<T> {
  const video = document.createElement('video');
  video.muted = true;
  video.playsInline = true;
  video.preload = options?.preload ?? 'auto';
  if (mediaNeedsCrossOrigin(src)) video.crossOrigin = 'anonymous';
  try {
    return await new Promise<T>((resolve, reject) => {
      let settled = false;
      const timer = options?.timeoutMs === undefined ? null : window.setTimeout(() => finish(() => reject(new Error('video frame capture timeout'))), options.timeoutMs);
      const finish = (run: () => void) => {
        if (settled) return;
        settled = true;
        if (timer !== null) window.clearTimeout(timer);
        run();
      };
      video.addEventListener('error', () => finish(() => reject(new Error('video element error'))), { once: true });
      video.addEventListener('loadeddata', () => {
        const duration = video.duration;
        if (!Number.isFinite(duration) || duration <= 0) return finish(() => reject(new Error('invalid video duration')));
        video.addEventListener('seeked', () => {
          Promise.resolve(render(video)).then((value) => finish(() => resolve(value)), (error) => finish(() => reject(error)));
        }, { once: true });
        video.currentTime = Math.max(0, Math.min(seekSec, Math.max(0, duration - 0.05)));
      }, { once: true });
      video.src = src;
      video.load();
    });
  } finally {
    video.removeAttribute('src');
    video.load();
  }
}

export async function captureVideoFrameBlob(src: string, seekSec: number): Promise<Blob> {
  return withVideoFrame(src, seekSec, (video) => new Promise<Blob>((resolve, reject) => {
    const canvas = document.createElement('canvas');
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const context = canvas.getContext('2d');
    if (!context) return reject(new Error('canvas context unavailable'));
    context.drawImage(video, 0, 0);
    canvas.toBlob((blob) => blob ? resolve(blob) : reject(new Error('canvas.toBlob returned null')), 'image/png');
  }));
}

const CACHE_LIMIT = 256;
const MAX_CONCURRENT = 2;
const stills = new Map<string, string | null>();
const inflight = new Set<string>();
const queue: string[] = [];
const listeners = new Set<() => void>();
let active = 0;

function notify(): void { for (const listener of listeners) listener(); }

async function captureStill(src: string): Promise<void> {
  let result: string | null = null;
  try {
    result = await withVideoFrame(src, 0.1, (video) => {
      const width = video.videoWidth || 320;
      const height = video.videoHeight || 180;
      const scale = Math.min(1, 320 / Math.max(width, height));
      const canvas = document.createElement('canvas');
      canvas.width = Math.max(1, Math.round(width * scale));
      canvas.height = Math.max(1, Math.round(height * scale));
      const context = canvas.getContext('2d');
      if (!context) return null;
      context.drawImage(video, 0, 0, canvas.width, canvas.height);
      return canvas.toDataURL('image/jpeg', 0.72);
    }, { preload: 'metadata', timeoutMs: 15000 });
  } catch {
    result = null;
  }
  if (stills.size >= CACHE_LIMIT && !stills.has(src)) {
    const oldest = stills.keys().next().value;
    if (oldest) stills.delete(oldest);
  }
  stills.set(src, result);
  notify();
}

function pump(): void {
  while (active < MAX_CONCURRENT && queue.length > 0) {
    const src = queue.shift();
    if (!src) continue;
    active += 1;
    const run = () => captureStill(src).finally(() => { active -= 1; inflight.delete(src); pump(); });
    if (typeof window.requestIdleCallback === 'function') window.requestIdleCallback(run, { timeout: 2000 });
    else window.setTimeout(run, 250);
  }
}

export function getLodStill(src: string | null | undefined): string | null {
  return src ? stills.get(src) ?? null : null;
}

export function requestLodStill(src: string | null | undefined): void {
  if (!src || stills.has(src) || inflight.has(src)) return;
  inflight.add(src);
  queue.push(src);
  pump();
}

export function subscribeLodStills(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
