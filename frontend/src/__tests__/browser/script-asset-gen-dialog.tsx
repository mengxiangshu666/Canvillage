import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import '@/index.css';
import type { ScriptAssetViewMode } from '@/features/canvas/domain/canvasNodes';
import { ScriptAssetGenDialog } from '@/features/canvas/nodes/script/ScriptAssetGenDialog';
import { collectScriptAssetLedger } from '@/features/canvas/nodes/script/scriptAssets';

const ledger = collectScriptAssetLedger([{
  character_1: '阿波', character_description_1: '绿色毛发，红色夹克',
  scene_tags: '屋顶', scene_descriptions: { 屋顶: '左侧楼梯口，右侧栏杆与排气管' },
  prop_tags: '滑板', prop_descriptions: { 滑板: '青色板面，橙色轮子' },
}], new Map());
const noop = () => {};

function Preview() {
  const [viewMode, setViewMode] = useState<ScriptAssetViewMode>('multi_view');
  return <ScriptAssetGenDialog open ledger={ledger} model="" modelReady={false}
    aspectKey="auto" viewMode={viewMode} onViewModeChange={setViewMode}
    onModelChange={noop} onAspectChange={noop} onCancel={noop}
    onConfirm={noop} onCreateOnly={noop} />;
}

document.documentElement.classList.add('dark');
createRoot(document.getElementById('root')!).render(
  <QueryClientProvider client={new QueryClient()}><Preview /></QueryClientProvider>,
);
