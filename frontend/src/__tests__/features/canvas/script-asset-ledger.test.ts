// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeEach, describe, expect, it } from 'vitest';

import type { FreezoneStoryScriptRow } from '@/api/ops';
import { CANVAS_NODE_TYPES, isImageGenNode, type VideoCreativeHandoff } from '@/features/canvas/domain/canvasNodes';
import {
  assetContributesAsReference,
  collectScriptAssetLedger,
  describeMissingAssets,
  scriptAssetId,
  sha256Hex,
} from '@/features/canvas/nodes/script/scriptAssets';
import {
  buildScriptAssetAnchor,
  bakeScriptAssetAnchor,
  stripScriptAssetAnchor,
  promptIsScriptOwned,
} from '@/features/canvas/nodes/script/scriptAssetAnchor';
import { buildScriptShotRefEntries } from '@/features/canvas/nodes/script/scriptShotRefs';
import {
  generateScriptAssetImages,
  defaultScriptAssetGenerationSelection,
  planScriptAssetImages,
  scriptAssetImagePrompt,
  collectScriptVisualStyle,
  resolveScriptAssetViewMode,
  resolveScriptAssetAspect,
  ASSET_IMAGE_CELL_HEIGHT,
  ASSET_IMAGE_CELL_WIDTH,
} from '@/features/canvas/nodes/script/scriptAssetGen';
import {
  computeScriptAssetPreflight,
  computeScriptPreflight,
  SCRIPT_REFERENCE_IMAGE_CAP,
} from '@/features/canvas/nodes/script/scriptPreflight';
import { resetStoryboardSettle } from '@/features/canvas/nodes/script/storyboardSettle';
import { useCanvasStore } from '@/stores/canvasStore';

/**
 * 脚本资产台账 → 资产图 → 分镜参考图 这条链。
 *
 * 这一层要证明的是「用脚本生成人物 / 场景 / 物品牌，并且这些图真的进得了下游参考图」：
 * - 台账从分镜行的标签推导出三族资产，带稳定 id，并能**认回**画布上已生成的资产图；
 * - 资产图的出图提示词按族分口径（角色设定图 / 场景空景 / 道具特写），描述为空也不提交空串；
 * - 锚定块把「图片N 是谁」写进提示词，编号与真正上传的参考图数组严格同序；
 * - 资产缺口按**资产**报一次，不是按镜报 N 遍。
 */

const SCRIPT_NODE_ID = 'script-node';
const SCRIPT_SIZE = { width: 800, height: 400 };
const MODEL = 'direct/image-msfkiqdq-ed3hhy';

function rows(): FreezoneStoryScriptRow[] {
  return [
    {
      shot_no: '1',
      shot_prompt: '推近祠堂',
      character_1: '阿雀',
      character_description_1: '十七岁少女，短打束发',
      scene_tags: '祠堂、黄昏',
      prop_tags: '玉佩',
    },
    {
      shot_no: '2',
      shot_prompt: '收尾',
      character_1: '阿雀',
      scene_tags: '祠堂',
      prop_tags: '玉佩、青铜钥匙',
    },
  ];
}

describe('资产定义摘要 sha256Hex', () => {
  it('命中标准 SHA-256 已知向量（含跨块填充边界）', () => {
    // 手写同步实现必须与标准摘要逐字节一致，否则资产 revision 会误判。
    expect(sha256Hex('')).toBe(
      'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
    );
    expect(sha256Hex('abc')).toBe(
      'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
    );
    // 56 字节会跨第二个消息块，专门验证填充长度计算。
    expect(sha256Hex('abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq')).toBe(
      '248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1',
    );
  });
});

describe('脚本资产台账', () => {
  it('三族从行标签推导，id 是 role + 归一化名的纯函数', () => {
    const ledger = collectScriptAssetLedger(rows(), new Map());
    expect(ledger.characters.map((asset) => asset.name)).toEqual(['阿雀']);
    expect(ledger.scenes.map((asset) => asset.name)).toEqual(['祠堂', '黄昏']);
    expect(ledger.props.map((asset) => asset.name)).toEqual(['玉佩', '青铜钥匙']);
    expect(ledger.characters[0].id).toBe('character:阿雀');
    // 名字大小写/空格不同仍是同一个资产（认领靠 id 相等）。
    expect(scriptAssetId('character', '  阿雀 ')).toBe('character:阿雀');
    // 「出现镜次」跨镜聚合：阿雀在第 1、2 镜都出场。
    expect(ledger.characters[0].shotNumbers).toEqual(['1', '2']);
  });

  it('按镜头实际暴露出的角度计算资产视图缺口', () => {
    const ledger = collectScriptAssetLedger([
      {
        shot_no: '1',
        shot: '祠堂俯视全景',
        character_action: '侧面拍摄阿雀转身离开',
        prop_state_change: '玉佩背面细节',
        character_1: '阿雀',
        scene_tags: '祠堂',
        prop_tags: '玉佩',
      },
    ], new Map([
      ['character:阿雀', {
        nodeId: 'char', imageUrl: 'char.png', revision: 1, contentHash: 'h',
        identityLocks: ['face'], dependencies: [], isGenerating: false, hasError: false,
        plannedViews: ['front', 'side', 'full_body'],
      }],
    ]));
    expect(ledger.characters[0].requiredViews).toEqual(['front', 'full_body', 'side']);
    expect(ledger.characters[0].plannedViews).toEqual(['front', 'side', 'full_body']);
    expect(ledger.characters[0].missingViews).toEqual(['front', 'full_body', 'side']);
    expect(ledger.scenes[0].requiredViews).toEqual(['geometry', 'top', 'wide']);
    expect(ledger.props[0].requiredViews).toEqual(['back', 'hero', 'multi_view']);
  });

  it('显式 asset_id 跨改名保持身份；内容变化生成新 revision 并把旧图退成 stale', () => {
    const beforeRows: FreezoneStoryScriptRow[] = [
      {
        shot_no: '1',
        character_1: '阿雀',
        character_asset_id_1: 'char_aque',
        character_description_1: '十七岁少女，短打束发',
        character_image_1: 'a.png',
      },
    ];
    const before = collectScriptAssetLedger(beforeRows, new Map());
    const original = before.byId.get('char_aque');
    expect(original).toBeDefined();
    expect(original?.contentHash).toMatch(/^[0-9a-f]{64}$/);
    expect(original?.identityLocks).toEqual(['face', 'costume', 'age', 'temperament']);
    expect(original?.revision).toBe(1);

    const renamed = collectScriptAssetLedger(
      beforeRows.map((row) => ({ ...row, character_1: '阿雀（新名）' })),
      new Map([
        [
          'char_aque',
          {
            nodeId: 'asset-node',
            imageUrl: 'gen.png',
            revision: 3,
            contentHash: original?.contentHash ?? null,
            identityLocks: ['face', 'costume', 'age', 'temperament'],
            dependencies: [],
            isGenerating: false,
            hasError: false,
          },
        ],
      ]),
    );
    const next = renamed.byId.get('char_aque');
    expect(next?.id).toBe('char_aque');
    expect(next?.name).toBe('阿雀（新名）');
    expect(next?.contentHash).not.toBe(original?.contentHash);
    expect(next?.revision).toBe(4);
    expect(next?.generatedImageState).toBe('stale');
    expect(next?.referenceImageUrl).toBe('a.png');
    expect(renamed.pendingGeneration.map((asset) => asset.id)).toContain('char_aque');
  });

  it('身份锁变化也算资产定义变化，旧资产图不能继续当当前参考图', () => {
    const base: FreezoneStoryScriptRow[] = [
      {
        shot_no: '1',
        character_1: '阿雀',
        character_asset_id_1: 'char_aque',
        character_identity_locks_1: 'face、costume',
        character_image_1: 'a.png',
      },
    ];
    const original = collectScriptAssetLedger(base, new Map()).byId.get('char_aque');
    const changed = collectScriptAssetLedger(
      [{ ...base[0], character_identity_locks_1: 'face' }],
      new Map([
        [
          'char_aque',
          {
            nodeId: 'asset-node',
            imageUrl: 'gen.png',
            revision: 2,
            contentHash: original?.contentHash ?? null,
            identityLocks: ['face', 'costume'],
            dependencies: [],
            isGenerating: false,
            hasError: false,
          },
        ],
      ]),
    ).byId.get('char_aque');

    expect(changed?.identityLocks).toEqual(['face']);
    expect(changed?.revision).toBe(3);
    expect(changed?.generatedImageState).toBe('stale');
  });

  it('没有图的资产单列成缺口，场景借的参考帧不算「有资产图」', () => {
    const ledger = collectScriptAssetLedger(rows(), new Map());
    // 角色有脚本回填图才算有图；这里行里没有 character_image_1 ⇒ 也算缺口。
    expect(ledger.missing.map((asset) => asset.id)).toEqual([
      'character:阿雀',
      'scene:祠堂',
      'scene:黄昏',
      'prop:玉佩',
      'prop:青铜钥匙',
    ]);
    expect(describeMissingAssets(ledger.missing)).toBe('1 个角色 / 2 个场景 / 2 个道具');
    // 三族的「能不能进参考图」口径不同，不能一刀切。
    const character = ledger.characters[0];
    const scene = ledger.scenes[0];
    expect(assetContributesAsReference({ ...character, imageUrl: 'a.png', imageSource: 'row' })).toBe(true);
    expect(assetContributesAsReference({ ...scene, imageUrl: 'f.png', imageSource: 'row' })).toBe(false);
    // 仅有 URL 还不算通过资产验收，必须带完整身份快照。
    expect(assetContributesAsReference({ ...scene, imageUrl: 'g.png', imageSource: 'generated' })).toBe(false);
  });

  it('画布上带 scriptAssetId 的图被认回台账（等价 LibTV 的 linkedNodeId + thumbnailUrl）', () => {
    const ledger = collectScriptAssetLedger(
      rows(),
      new Map([['character:阿雀', 'gen.png']]),
    );
    const character = ledger.characters[0];
    expect(character.imageUrl).toBe('gen.png');
    expect(character.imageSource).toBe('generated');
    // 生成过就不在缺口里，也不在「待生成」里。
    expect(ledger.missing.some((asset) => asset.id === 'character:阿雀')).toBe(false);
    expect(ledger.pendingGeneration).not.toContainEqual(character);
  });

  it('角色槽里的「无」不再生成幽灵角色资产', () => {
    const placeholders: FreezoneStoryScriptRow[] = [
      {
        shot_no: '1',
        character_1: '无',
        character_description_1: '',
        character_2: '无',
        character_description_2: '—',
      },
      {
        shot_no: '2',
        character_1: '没有',
      },
    ];
    const ledger = collectScriptAssetLedger(placeholders, new Map());
    expect(ledger.characters).toEqual([]);
    expect(ledger.missing.filter((asset) => asset.role === 'character')).toEqual([]);
  });

  it('批量弹层默认不勾只出现一次的场景，避免逐镜环境细节膨胀成资产', () => {
    const recurring: FreezoneStoryScriptRow[] = [
      { shot_no: '1', character_1: '阿雀', scene_tags: '祠堂、黄昏', prop_tags: '玉佩' },
      { shot_no: '2', character_1: '阿雀', scene_tags: '祠堂' },
    ];
    const ledger = collectScriptAssetLedger(recurring, new Map());
    const selected = defaultScriptAssetGenerationSelection(ledger.pendingGeneration);

    expect(selected.map((asset) => asset.id)).toContain('character:阿雀');
    expect(selected.map((asset) => asset.id)).toContain('scene:祠堂');
    expect(selected.map((asset) => asset.id)).not.toContain('scene:黄昏');
    expect(selected.map((asset) => asset.id)).toContain('prop:玉佩');
  });
});

describe('资产图锚定块（名字 ↔ 图片编号）', () => {
  const entries = () => {
    const ledger = collectScriptAssetLedger(rows(), new Map());
    return buildScriptShotRefEntries(rows()[0], ledger);
  };

  it('编号就是最终参考图数组里的位置，角色在前、场景道具在后', () => {
    const withImages = collectScriptAssetLedger(
      rows(),
      new Map([
        ['character:阿雀', 'a.png'],
        ['scene:祠堂', 's.png'],
        ['prop:玉佩', 'p.png'],
      ]),
    );
    const list = buildScriptShotRefEntries(rows()[0], withImages);
    expect(list.map((entry) => entry.imageUrl)).toEqual(['a.png', 's.png', 'p.png']);
    expect(list.map((entry) => entry.assetId)).toEqual([
      'character:阿雀',
      'scene:祠堂',
      'prop:玉佩',
    ]);
    const anchor = buildScriptAssetAnchor(list);
    expect(anchor.block).toContain('资产图锚定：');
    expect(anchor.block).toContain('角色 阿雀 的参考图是 图片1；锁定人物身份');
    expect(anchor.block).toContain('场景 祠堂 的参考图是 图片2；锁定地标');
    expect(anchor.block).toContain('道具 玉佩 的参考图是 图片3；锁定结构');
    expect(anchor.block).toContain('不让参考图覆盖本镜状态');
    expect(anchor.block).toContain('设定板中的多个角度属于同一个对象');
    expect(anchor.block).toContain('人数、道具数量、姿态和摄影构图按镜头描述');
    const baked = bakeScriptAssetAnchor('两名角色并肩站立', anchor);
    expect(stripScriptAssetAnchor(baked)).toBe('两名角色并肩站立');
    expect(promptIsScriptOwned(baked, '两名角色并肩站立')).toBe(true);
  });

  it('没有身份的参考帧占槽位但不进锚定表（锚定表的每一行都要说得名字）', () => {
    const row: FreezoneStoryScriptRow = { shot_no: '1', shot_prompt: '推近', reference: 'frame.png' };
    const list = buildScriptShotRefEntries(row, undefined);
    expect(list.map((entry) => entry.imageUrl)).toEqual(['frame.png']);
    expect(list[0].assetId).toBeNull();
    expect(buildScriptAssetAnchor(list).block).toBe('');
  });

  it('剥掉锚定块能拿回原句，据此判「这句还是脚本给的吗」', () => {
    const baked = '资产图锚定：\n角色 阿雀 的参考图是 图片1\n\n推近';
    expect(stripScriptAssetAnchor(baked)).toBe('推近');
    expect(promptIsScriptOwned(baked, '推近')).toBe(true);
    // 用户手改过的那句不再归脚本所有 —— 重跑时不该被顶掉。
    expect(promptIsScriptOwned('用户自己写的', '推近')).toBe(false);
    // 没有锚定块时原样返回。
    expect(stripScriptAssetAnchor('推近')).toBe('推近');
  });

  it('没有角色图的行不建锚定块，行为与改动前逐字一致', () => {
    const list = entries();
    expect(list).toEqual([]);
    expect(buildScriptAssetAnchor(list).block).toBe('');
  });
});

describe('资产图出图（第三刀）', () => {
  beforeEach(() => {
    resetStoryboardSettle();
    useCanvasStore.getState().setCanvasData(
      [
        {
          id: SCRIPT_NODE_ID,
          type: CANVAS_NODE_TYPES.script,
          position: { x: 0, y: 0 },
          style: { width: SCRIPT_SIZE.width, height: SCRIPT_SIZE.height },
          data: {
            scriptResult: { title: '天空之跃', rows: rows() },
            scriptTitle: '天空之跃',
          },
        },
      ] as never,
      [],
    );
  });

  it('三族默认多视图设定图，保留设计与干净成像', () => {
    const ledger = collectScriptAssetLedger(rows(), new Map());
    const character = scriptAssetImagePrompt(ledger.characters[0]);
    expect(character).toContain('生成角色四视图设定表');
    expect(character).toContain('左侧 1/3');
    expect(character).toContain('正面、侧面、背面三个无头全身视图');
    expect(character).not.toContain('右侧 1/4');
    expect(character).toContain('必须避免：五官变形');
    expect(character).toContain('十七岁少女');
    expect(character).toContain('锁定人物身份、五官、体型和设计');
    expect(character).toContain('本图只呈现资产设计与基准，不表演单镜动作');
    // 场景：不许出现人。
    expect(scriptAssetImagePrompt(ledger.scenes[0])).toContain('生成场景多视图设定板');
    expect(scriptAssetImagePrompt(ledger.scenes[0])).toContain('左侧为空镜全景，右侧为正俯视拓扑图');
    expect(scriptAssetImagePrompt(ledger.scenes[0])).toContain('不出现角色');
    expect(scriptAssetImagePrompt(ledger.scenes[0])).toContain('锁定地标、出入口、尺度与可通行区域');
    // 道具：同一张图内的四格 + 白底。
    const prop = scriptAssetImagePrompt(ledger.props[0]);
    expect(prop).toContain('生成道具多视图四格设定板');
    expect(prop).toContain('2×2 四格');
    expect(prop).toContain('无角色，纯白或浅灰背景');
    expect(prop).toContain('锁定结构、材质与比例');
    // 描述为空（场景/道具没有描述字段）时也不能是空串 —— 空串会被图片节点拒绝提交。
    expect(scriptAssetImagePrompt({ ...ledger.props[0], description: '' }).trim().length).toBeGreaterThan(0);
  });

  it('未知配置回落多视图，旧单视图选择兼容，新统一选择优先', () => {
    const ledger = collectScriptAssetLedger(rows(), new Map());
    const character = scriptAssetImagePrompt(ledger.characters[0], {
      characterPromptTemplate: 'front_full_body',
      style: '电影感写实',
    });

    expect(character).toContain('生成单人完整全身立绘');
    expect(character).toContain('标准正面站姿');
    expect(character).toContain('纯色浅灰背景 #E8E8E8');
    expect(character).toContain('图片风格为：电影感写实。');
    expect(character).toContain('必须避免：五官变形');
    expect(character).not.toContain('右侧 2/3');

    expect(resolveScriptAssetViewMode({ characterPromptTemplate: 'front_full_body' })).toBe('single_view');
    expect(resolveScriptAssetViewMode({ characterPromptTemplate: 'unknown' })).toBe('multi_view');
    expect(resolveScriptAssetViewMode()).toBe('multi_view');
    expect(resolveScriptAssetViewMode({ viewMode: null })).toBe('multi_view');
    expect(resolveScriptAssetViewMode({ viewMode: 'unknown' })).toBe('multi_view');
    expect(resolveScriptAssetViewMode({ characterPromptTemplate: 'four_view' })).toBe('multi_view');
    expect(resolveScriptAssetViewMode({ viewMode: 'multi_view', characterPromptTemplate: 'front_full_body' })).toBe('multi_view');
    expect(resolveScriptAssetViewMode({ viewMode: 'single_view', characterPromptTemplate: 'four_view' })).toBe('single_view');
    expect(scriptAssetImagePrompt(ledger.characters[0], { characterPromptTemplate: 'four_view' })).toContain('左侧 1/3');
    expect(scriptAssetImagePrompt(ledger.scenes[0], { viewMode: 'single_view' })).toContain('生成单幅场景空景参考图');
    expect(scriptAssetImagePrompt(ledger.props[0], { viewMode: 'single_view' })).toContain('生成单幅道具主视图参考');
  });

  it('场景拓扑标注通路、人物与摄影机各自含义，实际生成带本场调度', () => {
    const source = rows().map((row, index) => ({ ...row, display_shot_no: `段落镜${index + 1}` }));
    const ledger = collectScriptAssetLedger(source, new Map());
    const scene = ledger.scenes[0];
    const base = scriptAssetImagePrompt(scene);
    expect(base).toContain('地标、出入口、障碍物标签');
    expect(base).toContain('可行走通路');
    expect(base).toContain('不是实际人物走向或摄影机运镜');
    expect(base).toContain('不编造人物路线或摄影机轨迹');
    const staging = '阿雀从祠堂入口走向香案；摄影机留在门口面向香案。';
    useCanvasStore.getState().updateNodeData(SCRIPT_NODE_ID, { scriptResult: { rows: source, director_plan: {
      visual_bible: { visual_style: '剪纸画风', color_progression: '最后满屏红色' },
      sequences: [{ sequence_id: 'own', shot_nos: [1, 2], staging_plan: staging, performance_plan: '听完后狂喜' },
        { sequence_id: 'foreign', shot_nos: [99], staging_plan: '无关屋顶追逐' }],
    } } });
    const result = generateScriptAssetImages({ scriptNodeId: SCRIPT_NODE_ID, assets: [scene], scriptSize: SCRIPT_SIZE, config: { model: MODEL } });
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    const prompt = useCanvasStore.getState().nodes.find(node => node.id === result.nodeIds[0])?.data.prompt;
    expect(prompt).toContain(staging);
    expect(prompt).not.toContain('无关屋顶追逐');
    expect(useCanvasStore.getState().nodes.find(node => node.id === result.nodeIds[0])?.data.scriptCreativeHandoff).toMatchObject({
      directorContext: { sequences: [{ sequenceId: 'own', stagingPlan: staging }] },
    });
    expect(prompt).toContain('人物名、镜号、起点→终点');
    expect(prompt).toContain('C起→C止');
    expect(String(prompt).split(staging)).toHaveLength(2);
    expect(prompt).toContain('剪纸画风');
    expect(prompt).not.toContain('最后满屏红色');
    const handoff = useCanvasStore.getState().nodes.find(node => node.id === result.nodeIds[0])?.data.scriptCreativeHandoff as VideoCreativeHandoff;
    for (const asset of [ledger.characters[0], ledger.props[0]]) {
      const assetPrompt = scriptAssetImagePrompt(asset, { directorContext: handoff.directorContext });
      expect(assetPrompt).toContain('剪纸画风');
      expect(assetPrompt).not.toContain(staging);
      expect(assetPrompt).not.toContain('听完后狂喜');
    }
    const single = scriptAssetImagePrompt(scene, { viewMode: 'single_view', directorContext: handoff.directorContext });
    expect(single).toContain(staging);
    expect(single).toContain('不表演路线动作');
    expect(single).toContain('只取属于当前场景');
    expect(prompt).toContain('不把同段其他场景搬进本图');
  });

  it('全片风格从 shot_prompt 第 7 段白拿，取不到就不编', () => {
    const styled: FreezoneStoryScriptRow[] = [
      {
        shot_no: '1',
        shot_prompt:
          '[画面构图：近景] + [角色卡/主体描述：阿雀] + [视觉风格/质感：都市悬疑写实电影感] + [技术参数：85mm镜头，f/1.8]',
      },
    ];
    expect(collectScriptVisualStyle(styled)).toBe('都市悬疑写实电影感');
    // 角色卡里的方括号不能被当成风格段吃掉后面半句。
    const ledger = collectScriptAssetLedger(rows(), new Map());
    expect(
      scriptAssetImagePrompt(ledger.scenes[0], { style: collectScriptVisualStyle(styled) }),
    ).toContain('图片风格为：都市悬疑写实电影感。');
    // 老结果 / 用户自写提示词：没有第 7 段就返回 null，提示词里不出现空壳的「图片风格为：」。
    expect(collectScriptVisualStyle(rows())).toBeNull();
    expect(scriptAssetImagePrompt(ledger.scenes[0])).not.toContain('图片风格为');
  });

  it('资产图比例默认自动：角色 4:3、场景 16:9、道具 1:1（oiioii 落盘参数）', () => {
    expect(resolveScriptAssetAspect('character', 'auto')).toBe('4:3');
    expect(resolveScriptAssetAspect('scene', undefined)).toBe('16:9');
    expect(resolveScriptAssetAspect('prop', '')).toBe('1:1');
    // 用户锁定了比例就一律听用户的。
    expect(resolveScriptAssetAspect('character', '9:16')).toBe('9:16');
  });

  it('落位贴脚本左侧、顶部对齐，不与脚本节点重叠', () => {
    const plan = planScriptAssetImages({
      script: { x: 1000, y: 200, width: 800, height: 400 },
      count: 5,
    });
    // 整块右边缘留在脚本左侧（负向排布）。
    plan.positions.forEach((position) => {
      expect(position.x + ASSET_IMAGE_CELL_WIDTH).toBeLessThanOrEqual(1000);
      expect(position.y).toBeGreaterThanOrEqual(200);
    });
    expect(ASSET_IMAGE_CELL_HEIGHT).toBeGreaterThan(0);
  });

  it('建资产图节点：带认领键与归属，重复调用复用原节点而不是堆第二个', () => {
    const ledger = collectScriptAssetLedger(rows(), new Map());
    const first = generateScriptAssetImages({
      scriptNodeId: SCRIPT_NODE_ID,
      assets: ledger.all,
      scriptSize: SCRIPT_SIZE,
      config: { model: MODEL, aspectRatio: '1:1' },
    });
    expect(first.ok).toBe(true);
    if (!first.ok) return;
    expect(first.created).toBe(5);
    const created = useCanvasStore.getState().nodes.filter(isImageGenNode);
    expect(created).toHaveLength(5);
    expect(created.map((node) => node.data.scriptAssetId).sort()).toEqual(
      ['character:阿雀', 'prop:玉佩', 'prop:青铜钥匙', 'scene:祠堂', 'scene:黄昏'].sort(),
    );
    // 归属 + 认领键都在，否则台账认不回这张图。
    created.forEach((node) => {
      expect(node.data.scriptAssetOwnerId).toBe(SCRIPT_NODE_ID);
      expect(node.data.count).toBe(1);
    });
    const characterNode = created.find((node) => node.data.scriptAssetId === 'character:阿雀');
    expect(characterNode?.data.prompt).toContain('生成角色四视图设定表');
    expect(characterNode?.data.scriptAssetPlannedViews).toEqual(['front', 'side', 'back', 'full_body']);
    expect(created.find(node => node.data.scriptAssetId === 'scene:祠堂')?.data.scriptAssetPlannedViews).toEqual(['wide', 'geometry']);
    expect(created.find(node => node.data.scriptAssetId === 'prop:玉佩')?.data.scriptAssetPlannedViews).toEqual(['hero', 'multi_view']);
    expect(characterNode?.data.assetId).toBe('character:阿雀');
    expect(characterNode?.data.scriptAssetRevision).toBe(ledger.characters[0].revision);
    expect(characterNode?.data.scriptAssetContentHash).toBe(ledger.characters[0].contentHash);
    expect(characterNode?.data.scriptAssetIdentityLocks).toEqual(ledger.characters[0].identityLocks);
    expect(characterNode?.data.scriptAssetDependencies).toEqual([]);
    // 定义哈希不是媒体产物摘要，不能冒充后端要校验的 sha256。
    expect(characterNode?.data.sha256).toBeUndefined();

    // 再点一次：不新建节点，复用原来那 5 个。
    const second = generateScriptAssetImages({
      scriptNodeId: SCRIPT_NODE_ID,
      assets: ledger.all,
      scriptSize: SCRIPT_SIZE,
      config: {
        model: MODEL,
        aspectRatio: '1:1',
        viewMode: 'single_view',
      },
    });
    expect(second.ok).toBe(true);
    if (!second.ok) return;
    expect(second.created).toBe(0);
    expect(second.reused).toBe(5);
    expect(useCanvasStore.getState().nodes.filter(isImageGenNode)).toHaveLength(5);
    const reusedCharacter = useCanvasStore
      .getState()
      .nodes.filter(isImageGenNode)
      .find((node) => node.data.scriptAssetId === 'character:阿雀');
    expect(reusedCharacter?.data.prompt).toContain('生成单人完整全身立绘');
    expect(reusedCharacter?.data.scriptAssetPlannedViews).toEqual(['front', 'full_body']);
    const reused = useCanvasStore.getState().nodes.filter(isImageGenNode);
    expect(reused.find(node => node.data.scriptAssetId === 'scene:祠堂')?.data.prompt).toContain('生成单幅场景空景参考图');
    expect(reused.find(node => node.data.scriptAssetId === 'prop:玉佩')?.data.prompt).toContain('生成单幅道具主视图参考');
    expect(reused.find(node => node.data.scriptAssetId === 'scene:祠堂')?.data.scriptAssetPlannedViews).toEqual(['wide']);
    expect(reused.find(node => node.data.scriptAssetId === 'prop:玉佩')?.data.scriptAssetPlannedViews).toEqual(['hero']);
  });

  it('过期模型绑定在建节点前被拒绝，不再写出绑死模型的资产图节点', () => {
    const ledger = collectScriptAssetLedger(rows(), new Map());
    const before = useCanvasStore.getState().nodes.length;
    const result = generateScriptAssetImages({
      scriptNodeId: SCRIPT_NODE_ID,
      assets: ledger.all,
      scriptSize: SCRIPT_SIZE,
      config: { model: MODEL, aspectRatio: '1:1' },
      availableModelIds: [],
      generateImages: true,
    });

    expect(result).toEqual({ ok: false, reason: '所选资产图模型已停用或不存在，请重新选择' });
    expect(useCanvasStore.getState().nodes).toHaveLength(before);
  });

  it('生成的资产图被台账认回，并成为分镜图的参考图 + 锚定块', () => {
    const ledger = collectScriptAssetLedger(rows(), new Map());
    generateScriptAssetImages({
      scriptNodeId: SCRIPT_NODE_ID,
      assets: ledger.all,
      scriptSize: SCRIPT_SIZE,
      config: { model: MODEL, aspectRatio: '1:1' },
    });
    // 模拟出图落定：把每个资产图节点写上 imageUrl。
    useCanvasStore
      .getState()
      .nodes.filter(isImageGenNode)
      .forEach((node) =>
        useCanvasStore
          .getState()
          .updateNodeData(node.id, { imageUrl: `${String(node.data.scriptAssetId)}.png` }),
      );

    // 台账自己不带认领表时会现读画布 —— 这里显式传认领表验「认回」这件事。
    const claims = new Map(
      useCanvasStore
        .getState()
        .nodes.filter(isImageGenNode)
        .map((node) => [String(node.data.scriptAssetId), String(node.data.imageUrl)]),
    );
    expect([...claims.keys()]).toHaveLength(5);
    const claimed = collectScriptAssetLedger(rows(), claims);
    expect(claimed.missing).toEqual([]);
    expect(claimed.characters[0].imageSource).toBe('generated');
    expect(claimed.scenes.map((asset) => asset.imageUrl)).toEqual(['scene:祠堂.png', 'scene:黄昏.png']);
    // 分镜参考图里现在有角色 + 场景（道具不在第 1 镜的标签里就自然不带）。
    const list = buildScriptShotRefEntries(rows()[0], claimed);
    expect(list.map((entry) => entry.imageUrl)).toEqual([
      'character:阿雀.png',
      'scene:祠堂.png',
      'scene:黄昏.png',
      'prop:玉佩.png',
    ]);
  });
});

describe('资产缺口与参考图闸门（第四 / 五刀）', () => {
  it('缺口按资产报一次，不是按镜报 N 遍', () => {
    const ledger = collectScriptAssetLedger(rows(), new Map());
    const preflight = computeScriptAssetPreflight(ledger);
    // 阿雀出现在两镜里，但缺口只算 1 个角色。
    expect(preflight.referenceMissing.filter((asset) => asset.role === 'character')).toHaveLength(1);
    expect(preflight.missingSummary).toBe('1 个角色 / 2 个场景 / 2 个道具');
    expect(preflight.hasGap).toBe(true);
  });

  it('场景只有整镜参考帧时仍报参考图缺口（预览图不能当资产参考图）', () => {
    const sceneOnly: FreezoneStoryScriptRow[] = [
      { shot_no: '1', shot_prompt: '推近祠堂', scene_tags: '祠堂', reference: 'frame.png' },
    ];
    const ledger = collectScriptAssetLedger(sceneOnly, new Map());

    // 台账认这张图是场景卡预览，因此不在“一张图都没有”的 missing 里……
    expect(ledger.scenes[0]).toMatchObject({ imageUrl: 'frame.png', imageSource: 'row' });
    expect(ledger.missing).toEqual([]);

    // ……但预检必须按能不能真的送进生成算：整镜参考帧不算，仍需补一张场景资产图。
    const preflight = computeScriptAssetPreflight(ledger);
    expect(preflight.referenceMissing.map((asset) => asset.id)).toEqual(['scene:祠堂']);
    expect(preflight.missingSummary).toBe('1 个场景');
    expect(preflight.contributionCount).toBe(0);
    expect(preflight.hasGap).toBe(true);
  });

  it('参考图超过后端上限 ⇒ 报出来（后端是静默截断，前台必须说话）', () => {
    // 2 个角色槽（上限就是 2）+ 8 个场景 = 10 张，越过后端的 9 张截断线。
    const sceneTags = ['s1', 's2', 's3', 's4', 's5', 's6', 's7', 's8'];
    const many: FreezoneStoryScriptRow[] = [
      {
        shot_no: '1',
        shot_prompt: '推近',
        character_1: 'A',
        character_image_1: 'a.png',
        character_2: 'B',
        character_image_2: 'b.png',
        scene_tags: sceneTags.join('、'),
      },
    ];
    const ledger = collectScriptAssetLedger(
      many,
      new Map(sceneTags.map((tag) => [`scene:${tag}`, `${tag}.png`])),
    );
    const list = buildScriptShotRefEntries(many[0], ledger);
    expect(list).toHaveLength(SCRIPT_REFERENCE_IMAGE_CAP + 1);
    const preflight = computeScriptPreflight({ members: [], rows: many, ledger });
    expect(preflight.overflowShotNumbers).toEqual(['1']);
  });

  it('提示词引用了这一镜没有的图片编号 ⇒ 报出来（视频侧有同款闸门，图片侧此前没有）', () => {
    const rowsWithToken: FreezoneStoryScriptRow[] = [
      { shot_no: '1', shot_prompt: '让 图片3 里的人抬头', character_1: '阿雀', character_image_1: 'a.png' },
    ];
    const preflight = computeScriptPreflight({ members: [], rows: rowsWithToken });
    expect(preflight.tokenMissingShotNumbers).toEqual(['1']);

    // 引用得到的那句不算缺项。
    const ok: FreezoneStoryScriptRow[] = [
      { shot_no: '1', shot_prompt: '让 图片1 里的人抬头', character_1: '阿雀', character_image_1: 'a.png' },
    ];
    expect(computeScriptPreflight({ members: [], rows: ok }).tokenMissingShotNumbers).toEqual([]);
  });

  it('角色已有资产图时不再报「角色没有角色图」', () => {
    const named: FreezoneStoryScriptRow[] = [{ shot_no: '1', shot_prompt: '推近', character_1: '阿雀' }];
    expect(computeScriptPreflight({ members: [], rows: named }).degradedShotNumbers).toEqual(['1']);
    const ledger = collectScriptAssetLedger(named, new Map([['character:阿雀', 'a.png']]));
    expect(
      computeScriptPreflight({ members: [], rows: named, ledger }).degradedShotNumbers,
    ).toEqual([]);
  });
});
