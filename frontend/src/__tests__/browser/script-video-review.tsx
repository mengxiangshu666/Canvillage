import { createRoot } from 'react-dom/client';
import { useState } from 'react';
import '@/index.css';
import { ScriptVideoReview } from '@/features/canvas/nodes/script/ScriptVideoReview';
import { ScriptShotRewriteDialog } from '@/features/canvas/nodes/script/ScriptShotRewriteDialog';
import { scriptRowFingerprint } from '@/features/canvas/nodes/script/scriptRowFingerprint';
import { SCRIPT_SHOT_VIDEO_SOURCE_FIELD, SCRIPT_SHOT_VIDEO_ROW_FINGERPRINT_FIELD } from '@/features/canvas/nodes/script/scriptShotVideos';
import { useCanvasStore } from '@/stores/canvasStore';
import type { CanvasNode } from '@/features/canvas/domain/canvasNodes';

async function start() {
  const canvas = document.createElement('canvas');
  canvas.width = 640; canvas.height = 360;
  const context = canvas.getContext('2d')!;
  const stream = canvas.captureStream(20);
  const recorder = new MediaRecorder(stream, { mimeType: 'video/webm' });
  const chunks: BlobPart[] = [];
  recorder.ondataavailable = event => chunks.push(event.data);
  const stopped = new Promise<void>(resolve => { recorder.onstop = () => resolve(); });
  recorder.start();
  let frame = 0;
  const timer = setInterval(() => {
    context.fillStyle = '#346e58'; context.fillRect(0, 0, 640, 360);
    context.fillStyle = '#ffdf70'; context.fillRect(50 + frame++ * 8, 110, 110, 110);
  }, 50);
  await new Promise(resolve => setTimeout(resolve, 1200));
  recorder.stop(); await stopped; clearInterval(timer); stream.getTracks().forEach(track => track.stop());
  const url = URL.createObjectURL(new Blob(chunks, { type: 'video/webm' }));
  const rows = [{ shot_id: 'a', shot_no: '1', duration: '4', visual_description: '沿屋顶向右滑行，持续观察落点' },
    { shot_id: 'b', shot_no: '2', duration: '8', visual_description: '进入下一平台后放慢速度并回望' }];
  const nodes: CanvasNode[] = rows.map((row, index) => ({ id: `v${index}`, type: 'videoNode', position: { x: 0, y: 0 },
    data: { scriptShotId: row.shot_id, [SCRIPT_SHOT_VIDEO_SOURCE_FIELD]: 'script',
      [SCRIPT_SHOT_VIDEO_ROW_FINGERPRINT_FIELD]: scriptRowFingerprint(row, index), videoUrl: url } }));
  useCanvasStore.setState({ nodes, edges: [] });
  function Review() {
    const [target, setTarget] = useState<number | null>(null);
    return <main className="mx-auto h-[700px] w-full max-w-[640px] bg-background p-3 text-text-primary">
      <ScriptVideoReview rows={rows} scriptNodeId="script" onRewrite={setTarget} />
      <ScriptShotRewriteDialog open={target !== null} rowIndex={target ?? 0} shotNo={rows[target ?? 0].shot_no} currentSummary={rows[target ?? 0].visual_description} frozenFacts={[]} untouchedCount={1} onCancel={() => setTarget(null)} onSubmit={() => {}} />
    </main>;
  }
  createRoot(document.getElementById('root')!).render(<Review />);
}
void start();
