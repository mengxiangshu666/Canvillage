import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ComposeAudioPreview } from '@/features/canvas/compose/VideoComposeModal';
import type { ComposeClip } from '@/features/canvas/compose/timelineModel';

function tone(frequency: number) {
  const rate = 8000, count = rate * 4;
  const bytes = new ArrayBuffer(44 + count * 2), view = new DataView(bytes);
  const text = (offset: number, value: string) => [...value].forEach((char, i) => view.setUint8(offset + i, char.charCodeAt(0)));
  text(0, 'RIFF'); view.setUint32(4, 36 + count * 2, true); text(8, 'WAVE'); text(12, 'fmt ');
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  text(36, 'data'); view.setUint32(40, count * 2, true);
  for (let i = 0; i < count; i++) view.setInt16(44 + i * 2, Math.sin(i * frequency * Math.PI * 2 / rate) * 4000, true);
  return URL.createObjectURL(new Blob([bytes], { type: 'audio/wav' }));
}
const sources = [tone(220), tone(440), tone(880)];
const clips: ComposeClip[] = sources.map((sourceUrl, i) => ({ id: `sound-${i}`, nodeId: null, kind: 'audio', sourceUrl, displayName: null, thumbUrl: null, durationMs: 4000, timelineStartMs: i === 1 ? 1000 : 0, trimStartMs: 0, trimEndMs: 4000, volume: 0.5, muted: false, speed: 1 }));
function Fixture() {
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(1500);
  const [quiet, setQuiet] = useState(false);
  const changed = clips.map((clip, i) => i === 0 && quiet ? { ...clip, volume: 0.2, muted: true, speed: 1.5 } : clip);
  return <main>
    <button onClick={() => setPlaying(true)}>播放重叠声音</button>
    <button onClick={() => setQuiet(true)}>修改首段声音</button>
    <button onClick={() => { setPlaying(false); setTime(6000); }}>移到片尾</button>
    <ComposeAudioPreview tracks={[{ id: 'voice', kind: 'audio', clips: changed.slice(0, 2) }, { id: 'effect', kind: 'audio', clips: changed.slice(2) }]} playheadMs={time} isPlaying={playing} />
  </main>;
}
createRoot(document.getElementById('root')!).render(<Fixture />);
