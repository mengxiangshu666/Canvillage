// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from 'vitest';

import { extractUpstreamImages } from './graphImageResolver';
import { extractUpstreamContent } from './graphContentResolver';
import { referenceImageUrl } from '../hooks/useVideoReferences';
import {
  decideResultMirror,
  resolveResultOriginNodeId,
  RESULT_MIRRORED_AT_FIELD,
  RESULT_ORIGIN_FIELD,
} from './mirrorResultToOrigin';
import type { CanvasNode } from '../domain/canvasNodes';

const node = (
  id: string,
  type: string,
  data: Record<string, unknown>,
): CanvasNode => ({ id, type, position: { x: 0, y: 0 }, data }) as unknown as CanvasNode;

/**
 * 这一组测试钉住「改图 / 分镜生成的结果传不到下游」这个断点。
 *
 * 两条起步路线的边是 `refine(imageNode) -> video` 与 `storyboard(storyboardGenNode)
 * -> video`，而结果落在新建的 `exportImage` 子节点上；消费侧却全在读入口节点自己的
 * `data.imageUrl`，而那个字段从没被写过。只要回写机制失效，这两条线就重新变成
 * 「连了也没用」。
 */
describe('生成结果回写入口节点', () => {
  const RESULT_URL = 'http://x/result.png';

  const originTypes = [
    ['imageNode', '改图'],
    ['storyboardGenNode', '分镜生成'],
  ] as const;

  it('回写后下游解析器都能读到 —— 这是断点修复的真正验收点', () => {
    const mirroredImageEdit = node('origin', 'imageNode', {
      imageUrl: RESULT_URL,
      previewImageUrl: RESULT_URL,
    });
    expect(extractUpstreamImages(mirroredImageEdit), '改图 graphImageResolver').toEqual([
      RESULT_URL,
    ]);
    expect(
      extractUpstreamContent(mirroredImageEdit).imageUrl,
      '改图 graphContentResolver',
    ).toBe(RESULT_URL);
    expect(referenceImageUrl(mirroredImageEdit), '改图 useVideoReferences').toBe(RESULT_URL);

    // 分镜生成不走 graphImageResolver（那张表里只有 upload / imageEdit / exportImage），
    // 走的是 graphContentResolver + useVideoReferences —— 视频节点的 i2v 两个入口都覆盖。
    const mirroredStoryboard = node('origin', 'storyboardGenNode', {
      imageUrl: RESULT_URL,
      previewImageUrl: RESULT_URL,
    });
    expect(
      extractUpstreamContent(mirroredStoryboard).imageUrl,
      '分镜生成 graphContentResolver',
    ).toBe(RESULT_URL);
    expect(
      referenceImageUrl(mirroredStoryboard),
      '分镜生成 useVideoReferences',
    ).toBe(RESULT_URL);
  });

  it('视频节点的 i2v 首帧取值口径（imageUrl 优先）能拿到回写的结果', () => {
    for (const [type, label] of originTypes) {
      const mirrored = node('origin', type, { imageUrl: RESULT_URL });
      expect(
        (mirrored.data as { imageUrl?: string }).imageUrl,
        `${label} VideoNode.submittableImageUrl 读这个键`,
      ).toBe(RESULT_URL);
    }
  });

  it('结果子节点带显式 originNodeId 时按它回写', () => {
    const child = node('child', 'exportImageNode', {
      [RESULT_ORIGIN_FIELD]: 'origin',
      imageUrl: RESULT_URL,
      generationStartedAt: 100,
    });
    const origin = node('origin', 'imageNode', {});
    expect(resolveResultOriginNodeId(child, [child, origin], [])).toBe('origin');
    expect(decideResultMirror(child.data, origin)?.imageUrl).toBe(RESULT_URL);
  });

  it('没有显式标记时按「唯一入边且源类型在白名单」推断', () => {
    const child = node('child', 'exportImageNode', {
      imageUrl: RESULT_URL,
      generationStartedAt: 100,
    });
    const refine = node('refine', 'imageNode', {});
    const nodes = [child, refine];
    expect(
      resolveResultOriginNodeId(child, nodes, [{ source: 'refine', target: 'child' }]),
    ).toBe('refine');
  });

  it('多入边且无显式标记时不回写（不猜，避免写错节点）', () => {
    const child = node('child', 'exportImageNode', {
      imageUrl: RESULT_URL,
      generationStartedAt: 100,
    });
    const a = node('a', 'imageNode', {});
    const b = node('b', 'uploadNode', {});
    expect(
      resolveResultOriginNodeId(child, [child, a, b], [
        { source: 'a', target: 'child' },
        { source: 'b', target: 'child' },
      ]),
    ).toBeNull();
  });

  it('唯一入边源类型不在白名单时不回写（例如上传节点直连结果节点）', () => {
    const child = node('child', 'exportImageNode', {
      imageUrl: RESULT_URL,
      generationStartedAt: 100,
    });
    const up = node('up', 'uploadNode', {});
    expect(
      resolveResultOriginNodeId(child, [child, up], [{ source: 'up', target: 'child' }]),
    ).toBeNull();
  });

  it('非 exportImage 节点永不回写（只处理「结果落点」这一类）', () => {
    const notResult = node('n', 'imageGenNode', { imageUrl: RESULT_URL });
    expect(resolveResultOriginNodeId(notResult, [notResult], [])).toBeNull();
  });

  it('入口节点类型不在白名单时不回写', () => {
    const child = node('child', 'exportImageNode', {
      [RESULT_ORIGIN_FIELD]: 'origin',
      imageUrl: RESULT_URL,
    });
    expect(decideResultMirror(child.data, node('origin', 'videoNode', {}))).toBeNull();
    expect(decideResultMirror(child.data, node('origin', 'uploadNode', {}))).toBeNull();
  });

  it('入口节点已删除时不回写（找不到就不写）', () => {
    const child = node('child', 'exportImageNode', {
      [RESULT_ORIGIN_FIELD]: 'origin',
      imageUrl: RESULT_URL,
    });
    expect(decideResultMirror(child.data, undefined)).toBeNull();
  });

  it('结果还没落地（无 URL）时不回写', () => {
    const child = node('child', 'exportImageNode', {
      [RESULT_ORIGIN_FIELD]: 'origin',
      isGenerating: true,
    });
    expect(decideResultMirror(child.data, node('origin', 'imageNode', {}))).toBeNull();
  });

  it('水位线：先提交后返回的旧结果不会覆盖新图', () => {
    const older = node('child', 'exportImageNode', {
      [RESULT_ORIGIN_FIELD]: 'origin',
      imageUrl: 'http://x/old.png',
      generationStartedAt: 100,
    });
    const newer = node('child', 'exportImageNode', {
      [RESULT_ORIGIN_FIELD]: 'origin',
      imageUrl: 'http://x/new.png',
      generationStartedAt: 200,
    });
    const origin = node('origin', 'imageNode', { [RESULT_MIRRORED_AT_FIELD]: 200 });

    expect(decideResultMirror(older.data, origin)).toBeNull();
    expect(decideResultMirror(newer.data, origin)?.imageUrl).toBe('http://x/new.png');
  });

  it('回写只写图片字段 + 水位线，不带槽位语义（slot_target 仍只属于子节点）', () => {
    const child = node('child', 'exportImageNode', {
      [RESULT_ORIGIN_FIELD]: 'origin',
      imageUrl: RESULT_URL,
      generationStartedAt: 100,
    });
    const decision = decideResultMirror(child.data, node('origin', 'imageNode', {}));
    expect(Object.keys(decision ?? {}).sort()).toEqual([
      'imageUrl',
      'mirroredAt',
      'originNodeId',
      'previewImageUrl',
    ]);
  });

  it('previewImageUrl 缺失时回落到 imageUrl（消费侧两个键都读）', () => {
    const child = node('child', 'exportImageNode', {
      [RESULT_ORIGIN_FIELD]: 'origin',
      imageUrl: RESULT_URL,
      generationStartedAt: 100,
    });
    expect(
      decideResultMirror(child.data, node('origin', 'imageNode', {}))?.previewImageUrl,
    ).toBe(RESULT_URL);
  });
});
