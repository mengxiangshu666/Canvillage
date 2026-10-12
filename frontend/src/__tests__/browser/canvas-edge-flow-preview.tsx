// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { createRoot } from 'react-dom/client';
import { ReactFlowProvider, Position, type EdgeProps } from '@xyflow/react';
import '@/index.css';
import { DisconnectableEdge } from '@/features/canvas/edges/DisconnectableEdge';
import { CanvasSettingsPanel } from '@/features/canvas/ui/CanvasSettingsPanel';
import { setCanvasGestureActive } from '@/features/canvas/application/canvasLod';
import { useCanvasStore } from '@/stores/canvasStore';

const edges = [
  { id: 'short', source: 'near', target: 'target' },
  { id: 'long', source: 'far', target: 'target' },
];
useCanvasStore.setState({ nodes: [], edges, selectedNodeId: 'target' });
const common: Omit<EdgeProps, 'id' | 'source' | 'target' | 'sourceX' | 'sourceY' | 'targetY'> = {
  targetX: 550, sourcePosition: Position.Right, targetPosition: Position.Left,
  selected: false, selectable: true, deletable: true,
};

function Fixture() {
  return (
    <ReactFlowProvider>
      <main className="flex min-h-screen flex-col gap-3 bg-[#121214] p-3 text-white md:flex-row">
        <div className="min-w-0 flex-1">
          <svg viewBox="0 0 620 320" className="village-canvas-stage block w-full" aria-label="流光预览">
            <DisconnectableEdge {...common} {...edges[0]} sourceX={350} sourceY={80} targetY={80} />
            <DisconnectableEdge {...common} {...edges[1]} sourceX={50} sourceY={240} targetY={240} />
            <text x="350" y="58" fill="#ddd" fontSize="14">近距离</text>
            <text x="50" y="218" fill="#ddd" fontSize="14">远距离</text>
          </svg>
          <div className="flex gap-3">
            <button type="button" onClick={() => setCanvasGestureActive(true)}>暂停交互流光</button>
            <button type="button" onClick={() => setCanvasGestureActive(false)}>恢复交互流光</button>
          </div>
        </div>
        <div className="shrink-0"><CanvasSettingsPanel onClose={() => {}} /></div>
      </main>
    </ReactFlowProvider>
  );
}

createRoot(document.getElementById('root')!).render(<Fixture />);
