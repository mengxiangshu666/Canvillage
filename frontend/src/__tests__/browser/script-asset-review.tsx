import { createRoot } from 'react-dom/client';
import '@/index.css';
import { ScriptAssetView } from '@/features/canvas/nodes/script/ScriptAssetView';
import { collectScriptAssetLedger } from '@/features/canvas/nodes/script/scriptAssets';
import { useCanvasStore } from '@/stores/canvasStore';

const rows = [{ shot_no: 1, prop_tags: '滑板', prop_descriptions: { 滑板: '青色板面，橙色轮子' } }];
const asset = collectScriptAssetLedger(rows, new Map()).props[0];
const canvas = document.createElement('canvas'); canvas.width = 640; canvas.height = 360;
const ctx = canvas.getContext('2d')!; ctx.fillStyle = '#e8e8e8'; ctx.fillRect(0, 0, 640, 360);
ctx.fillStyle = '#168a82'; ctx.fillRect(90, 130, 460, 65);
ctx.fillStyle = '#e86a31'; for (const x of [150, 450]) { ctx.beginPath(); ctx.arc(x, 215, 30, 0, Math.PI * 2); ctx.fill(); }
useCanvasStore.getState().setCanvasData([{ id: 'asset', type: 'imageGenNode', position: { x: 0, y: 0 }, data: { imageUrl: canvas.toDataURL(), scriptAssetId: asset.id, scriptAssetRevision: asset.revision, scriptAssetContentHash: asset.contentHash, scriptAssetIdentityLocks: asset.identityLocks } }], []);
createRoot(document.getElementById('root')!).render(<main className="mx-auto h-[820px] w-full max-w-[640px] bg-background p-3 text-text-primary"><ScriptAssetView rows={rows} /></main>);
