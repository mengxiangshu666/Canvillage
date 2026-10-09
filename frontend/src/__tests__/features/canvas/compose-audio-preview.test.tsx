import { fireEvent, render } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ComposeAudioPreview } from '@/features/canvas/compose/VideoComposeModal';
import type { ComposeClip, ComposeTrack } from '@/features/canvas/compose/timelineModel';

afterEach(() => vi.restoreAllMocks());
const clip = (id: string, start = 0): ComposeClip => ({ id, nodeId: null, kind: 'audio', sourceUrl: `/${id}.wav`, displayName: id, thumbUrl: null, durationMs: 4000, timelineStartMs: start, trimStartMs: 0, trimEndMs: 4000, volume: 0.7, muted: false, speed: 1 });
it('plays overlapping clips on both the same and separate tracks and applies live settings', () => {
  const play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue();
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
  vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {});
  const tracks: ComposeTrack[] = [{ id: 'voice', kind: 'audio', clips: [clip('voice'), clip('room', 1000)] }, { id: 'effects', kind: 'audio', clips: [clip('effect')] }];
  const { container, rerender } = render(<ComposeAudioPreview tracks={tracks} playheadMs={1500} isPlaying />);
  const media = Array.from(container.querySelectorAll('audio'));
  expect(media).toHaveLength(3);
  media.forEach(el => { fireEvent.loadedMetadata(el); expect(el.currentTime).toBe(el.dataset.composeAudio === 'room' ? 0.5 : 1.5); });
  expect(play).toHaveBeenCalledTimes(6);
  const changed = [{ ...tracks[0], clips: [{ ...tracks[0].clips[0], volume: 0.2, muted: true, speed: 1.5 }, tracks[0].clips[1]] }, tracks[1]];
  rerender(<ComposeAudioPreview tracks={changed} playheadMs={1500} isPlaying />);
  expect(media[0].volume).toBe(0.2);
  expect(media[0].muted).toBe(true);
  expect(media[0].playbackRate).toBe(1.5);
  rerender(<ComposeAudioPreview tracks={changed} playheadMs={6000} isPlaying={false} />);
  media.forEach(el => expect(el.hasAttribute('src')).toBe(false));
});
