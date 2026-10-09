import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ScriptRoughPreview } from '@/features/canvas/nodes/script/ScriptRoughPreview';

afterEach(() => vi.useRealTimers());

it('uses decoded clip time, trims at the planned cut, pauses on seek and media errors', async () => {
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue();
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
  const view = render(<ScriptRoughPreview rows={[{ shot_no: 1, duration: 2 }, { shot_no: 2, duration: 3, visual_description: '静帧替代' }]} videos={['/clip.mp4', null]} />);
  const video = view.container.querySelector('video')!;
  Object.defineProperty(video, 'duration', { value: 4 });
  fireEvent.loadedMetadata(video);
  fireEvent.click(screen.getByRole('button', { name: '开启素材声音' }));
  expect(video.muted).toBe(false);
  fireEvent.click(screen.getByRole('button', { name: '播放预演' }));
  video.currentTime = 1;
  fireEvent.timeUpdate(video);
  expect(screen.getByRole('slider')).toHaveValue('1');
  fireEvent.change(screen.getByRole('slider'), { target: { value: '0.5' } });
  expect(video.currentTime).toBe(0.5);
  expect(screen.getByRole('button', { name: '播放预演' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '播放预演' }));
  video.currentTime = 2.2;
  fireEvent.timeUpdate(video);
  expect(view.container.querySelector('video')).toBeNull();
  expect(screen.getByText('静帧替代')).toBeInTheDocument();
  fireEvent.change(screen.getByRole('slider'), { target: { value: '0.5' } });
  fireEvent.error(view.container.querySelector('video')!);
  expect(screen.getByRole('alert')).toHaveTextContent('视频加载失败');
  expect(screen.getByRole('button', { name: '播放预演' })).toBeInTheDocument();
  vi.restoreAllMocks();
});

it('does not pretend a shorter video covers the planned shot', () => {
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
  render(<ScriptRoughPreview rows={[{ duration: 4 }]} videos={['/short.mp4']} />);
  const video = document.querySelector('video')!;
  Object.defineProperty(video, 'duration', { value: 2 });
  fireEvent.loadedMetadata(video);
  expect(screen.getByRole('alert')).toHaveTextContent('视频短于剧本镜头时长');
  vi.restoreAllMocks();
});

it('previews cut boundaries, pauses for scrubbing and stops at the end', () => {
  vi.useFakeTimers();
  render(<ScriptRoughPreview rows={[
    { shot_no: 1, duration: 2, visual_description: '空房', cut_reason: '切向声源' },
    { shot_no: 2, duration: 3, visual_description: '街道', shot_purpose: '揭示来人' },
  ]} />);
  expect(screen.getByText('空房')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '播放预演' }));
  act(() => vi.advanceTimersByTime(2100));
  expect(screen.getByText('街道')).toBeInTheDocument();
  fireEvent.change(screen.getByRole('slider', { name: '预演位置' }), { target: { value: '1' } });
  expect(screen.getByRole('button', { name: '播放预演' })).toBeInTheDocument();
  expect(screen.getByText('空房')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '播放预演' }));
  act(() => vi.advanceTimersByTime(1100));
  act(() => vi.advanceTimersByTime(4000));
  expect(screen.getByRole('button', { name: '播放预演' })).toBeInTheDocument();
  expect(screen.getByRole('slider')).toHaveValue('5');
});

it('blocks playback when a shot has no known duration', () => {
  render(<ScriptRoughPreview rows={[{ shot_no: 1, visual_description: '空房' }]} />);
  expect(screen.getByRole('button', { name: '播放预演' })).toBeDisabled();
});

it('updates rendered storyboard without resetting the playhead or falling back to an input', () => {
  const rows = [{ shot_no: 1, duration: 3, reference: '/portrait.png', visual_description: '空房' }];
  const { rerender } = render(<ScriptRoughPreview rows={rows} frames={[{ url: '/shot.png', label: '已生成分镜' }]} />);
  fireEvent.change(screen.getByRole('slider'), { target: { value: '1' } });
  rerender(<ScriptRoughPreview rows={rows} frames={[{ url: '/new.png', label: '已生成分镜' }]} />);
  expect(screen.getByRole('img')).toHaveAttribute('src', expect.stringContaining('/new.png'));
  expect(screen.getByRole('slider')).toHaveValue('1');
  rerender(<ScriptRoughPreview rows={rows} frames={[{ url: null, label: '分镜已过期' }]} />);
  expect(screen.queryByRole('img')).toBeNull();
  expect(screen.getByText('分镜已过期')).toBeInTheDocument();
});
