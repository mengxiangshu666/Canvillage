import { describe, expect, it } from 'vitest';

import {
  inferVideoReferenceRole,
  referenceRoleFromContinuityEdge,
  referenceRoleFromVideoEdge,
  renderVideoReferenceRoleLegend,
} from './videoReferenceRoles';

describe('video reference roles', () => {
  it('reads shot-specific frame and asset edge roles', () => {
    expect(referenceRoleFromVideoEdge('scriptShotVideo', '首帧')).toBe('first_frame');
    expect(referenceRoleFromVideoEdge('scriptShotAssetReference', '角色 阿波')).toBe('identity');
    expect(referenceRoleFromVideoEdge('scriptShotAssetReference', '场景 高架桥')).toBe('scene');
    expect(referenceRoleFromVideoEdge('scriptShotAssetReference', '道具 滑板')).toBe('prop');
  });
  it('prefers explicit output roles from existing asset nodes', () => {
    expect(inferVideoReferenceRole({ output_role: 'character_identity' })).toBe('identity');
    expect(inferVideoReferenceRole({ output_role: 'scene_director_pano_360' })).toBe('scene');
    expect(inferVideoReferenceRole({ output_role: 'prop_reference' })).toBe('prop');
  });

  it('reads legacy preset metadata without guessing unlabeled nodes', () => {
    expect(inferVideoReferenceRole({ __freezone_source: { role: 'scene_master' } })).toBe('scene');
    expect(inferVideoReferenceRole({ displayName: '主角身份图' })).toBe('identity');
    expect(inferVideoReferenceRole({ displayName: '普通图片' })).toBeUndefined();
  });

  it('infers role from nodeRole and assetKind metadata used by canvas nodes', () => {
    expect(inferVideoReferenceRole({ nodeRole: 'character' })).toBe('identity');
    expect(inferVideoReferenceRole({ assetKind: 'scene_reference' })).toBe('scene');
  });

  it('renders a compact role legend and keyframe slots', () => {
    const legend = renderVideoReferenceRoleLegend(
      [
        { kind: 'image', role: 'identity', displayName: '主角' },
        { kind: 'image', role: 'scene', displayName: '屋顶' },
      ],
      'imageReference',
    );
    expect(legend).toContain('图片1 是角色身份锚点（主角）');
    expect(legend).toContain('图片2 是场景空间锚点（屋顶）');

    const keyframes = renderVideoReferenceRoleLegend(
      [{ kind: 'image' }, { kind: 'image' }],
      'firstLastFrame',
    );
    expect(keyframes).toContain('图片1 是首帧约束');
    expect(keyframes).toContain('图片2 是尾帧约束');
  });

  it('delivers the legend as prose instead of an internal ledger block', () => {
    const legend = renderVideoReferenceRoleLegend(
      [{ kind: 'image', role: 'identity', displayName: '主角' }],
      'imageReference',
    );

    // 交付文本里不能再出现内部规则标记：方括号标题、等号记账、内部角色 ID 前缀。
    expect(legend).not.toContain('[参考素材角色]');
    expect(legend).not.toContain('=');
    expect(legend).toContain('参考素材中，');
  });

  it('types the previous-shot edge as continuity and leaves other edges alone', () => {
    expect(referenceRoleFromContinuityEdge('scriptShotContinuity')).toBe('continuity');
    expect(referenceRoleFromContinuityEdge('', '上一镜承接')).toBe('continuity');
    // 首帧边不在这里升级语义：它的角色仍由节点自己声明。
    expect(referenceRoleFromContinuityEdge('scriptShotVideo')).toBeUndefined();
    expect(referenceRoleFromContinuityEdge('scriptShotVideo', '首帧')).toBeUndefined();
    expect(referenceRoleFromContinuityEdge(undefined, undefined)).toBeUndefined();
  });

  it('tells the model what the second image is when a shot carries over', () => {
    const legend = renderVideoReferenceRoleLegend(
      [
        { kind: 'image', displayName: '分镜 #1' },
        { kind: 'image', role: 'continuity', displayName: '分镜 #2' },
      ],
      'allReference',
    );

    expect(legend).toContain('图片2 是上一镜画面（分镜 #2）');
    // 没有声明角色的那张不该被编造出一个角色。
    expect(legend).not.toContain('图片1 是');
  });
});
