import { createRoot } from 'react-dom/client';
import '@/index.css';
import { ScriptRoughPreview } from '@/features/canvas/nodes/script/ScriptRoughPreview';

const rows = [{ shot_no: 1, duration: 1, visual_description: '第一段动态素材' },
  { shot_no: 2, duration: 1, visual_description: '中间镜头缺视频' },
  { shot_no: 3, duration: 1, visual_description: '第三段动态素材' }];
createRoot(document.getElementById('root')!).render(<main className="mx-auto w-full max-w-[900px] p-3">
  <ScriptRoughPreview rows={rows} videos={['./rough-preview-test.webm', null, './rough-preview-test.webm']}
    frames={rows.map(() => ({ url: null, label: '缺少分镜' }))} />
</main>);
