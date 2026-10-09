// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from 'vitest';

import { findOrphanNodes } from './orphanNodes';

const node = (id: string, type: string, data: Record<string, unknown> = {}) => ({
  id,
  type,
  position: { x: 0, y: 0 },
  data,
});

describe('findOrphanNodes', () => {
  it('空画布返回空数组', () => {
    expect(findOrphanNodes([], [])).toEqual([]);
  });

  it('识别既非 source 亦非 target 的节点', () => {
    const nodes = [
      node('a', 'imageGenNode'),
      node('b', 'videoNode'),
      node('c', 'textAnnotationNode'),
    ];
    const edges = [{ source: 'a', target: 'b' }];
    const orphans = findOrphanNodes(nodes, edges);
    expect(orphans.map((o) => o.id)).toEqual(['c']);
  });

  it('只有出边或只有入边的节点不算孤儿', () => {
    const nodes = [
      node('src', 'uploadNode'),
      node('sink', 'videoComposeNode'),
      node('lonely', 'audioNode'),
    ];
    const edges = [{ source: 'src', target: 'sink' }];
    expect(findOrphanNodes(nodes, edges).map((o) => o.id)).toEqual(['lonely']);
  });

  it('分组容器不计入孤儿', () => {
    const nodes = [node('g', 'groupNode'), node('x', 'imageGenNode')];
    expect(findOrphanNodes(nodes, []).map((o) => o.id)).toEqual(['x']);
  });

  it('摘要优先取 displayName，回退 prompt', () => {
    const nodes = [
      node('a', 'imageGenNode', { displayName: '主角定妆' }),
      node('b', 'textAnnotationNode', { prompt: '凌晨的便利店门口' }),
      node('c', 'audioNode', {}),
    ];
    const byId = Object.fromEntries(
      findOrphanNodes(nodes, []).map((o) => [o.id, o]),
    );
    expect(byId.a.summary).toBe('主角定妆');
    expect(byId.b.summary).toBe('凌晨的便利店门口');
    expect(byId.c.summary).toBe('');
  });

  it('给出中文类型名，未知类型回退为原始 type', () => {
    const nodes = [node('a', 'imageGenNode'), node('b', 'mysteryNode')];
    const byId = Object.fromEntries(
      findOrphanNodes(nodes, []).map((o) => [o.id, o]),
    );
    expect(byId.a.typeLabel).toBe('图片节点');
    expect(byId.b.typeLabel).toBe('mysteryNode');
  });

  it('摘要超长时截断到 48 字以内', () => {
    const long = '这是一段特别长的提示词'.repeat(20);
    const [orphan] = findOrphanNodes(
      [node('a', 'imageGenNode', { prompt: long })],
      [],
    );
    expect(orphan.summary.length).toBeLessThanOrEqual(48);
  });
});
