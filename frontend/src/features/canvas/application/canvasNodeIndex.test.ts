import { describe, expect, it } from 'vitest';

import {
  selectCanvasNodeById,
  selectIsCanvasBoxSelecting,
  selectSelectedCanvasNodes,
} from './canvasNodeIndex';

describe('canvasNodeIndex', () => {
  it('returns stable node references by id without changing the source array', () => {
    const nodes = [
      { id: 'a', type: 'textAnnotationNode', position: { x: 0, y: 0 }, data: {} },
      { id: 'b', type: 'imageNode', position: { x: 10, y: 10 }, data: {} },
    ] as never[];

    const first = selectCanvasNodeById(nodes, 'a');
    const second = selectCanvasNodeById(nodes, 'a');

    expect(first).toBe(nodes[0]);
    expect(second).toBe(first);
    expect(selectCanvasNodeById(nodes, 'missing')).toBeUndefined();
    expect(selectCanvasNodeById(nodes, null)).toBeUndefined();
  });

  it('returns a stable selected-node slice for the same array identity', () => {
    const nodes = [
      { id: 'a', selected: true, position: { x: 0, y: 0 }, data: {} },
      { id: 'b', selected: false, position: { x: 10, y: 10 }, data: {} },
    ] as never[];

    const first = selectSelectedCanvasNodes(nodes);
    const second = selectSelectedCanvasNodes(nodes);

    expect(first).toEqual([nodes[0]]);
    expect(second).toBe(first);
  });

  it('caches the box-selection boolean per node-array identity', () => {
    const nodes = [
      { id: 'a', selected: true, position: { x: 0, y: 0 }, data: {} },
      { id: 'b', selected: true, position: { x: 10, y: 10 }, data: {} },
    ] as never[];

    expect(selectIsCanvasBoxSelecting(nodes)).toBe(true);
    expect(selectIsCanvasBoxSelecting(nodes)).toBe(true);
    expect(selectIsCanvasBoxSelecting([nodes[0]] as never[])).toBe(false);
  });
});
