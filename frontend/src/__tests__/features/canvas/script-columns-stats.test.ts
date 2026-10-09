// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from 'vitest';

import type { FreezoneStoryScriptRow } from '@/api/ops';
import {
  CORE_ADD_BACK_ORDER,
  DETAIL_ADD_BACK_ORDER,
  FIT_ALWAYS_KEEP,
  SCRIPT_COLUMN_PRIORITY,
  fieldsTotalWidth,
  resolveScriptColumnMode,
  resolveVisibleScriptColumns,
  scriptColumnSummary,
  toggleScriptColumn,
  toggleScriptColumnPatch,
} from '@/features/canvas/nodes/script/scriptColumns';
import { scriptFieldSequence } from '@/features/canvas/nodes/script/scriptFields';
import {
  computeScriptShotRhythm,
  computeScriptStats,
  formatTotalDuration,
  parseDurationSeconds,
  summarizeScriptStats,
} from '@/features/canvas/nodes/script/scriptStats';

/** 节点默认宽 800px → 内容区 782px（见 ScriptNode 的 tableAvailableWidth）。 */
const NODE_CONTENT_WIDTH = 782;

function row(overrides: Partial<FreezoneStoryScriptRow> = {}): FreezoneStoryScriptRow {
  return { shot_no: '1', duration: '3', visual_description: '开场', ...overrides };
}

describe('分镜表列显隐', () => {
  it('未知模式一律回落默认，老画布无需迁移', () => {
    expect(resolveScriptColumnMode(undefined)).toBe('default');
    expect(resolveScriptColumnMode('卡片')).toBe('default');
    expect(resolveScriptColumnMode(null)).toBe('default');
    expect(resolveScriptColumnMode('all')).toBe('all');
    expect(resolveScriptColumnMode('manual')).toBe('manual');
  });

  it('默认档任何宽度都不放明细列，核心列与必留列一个不少', () => {
    // 此前这条读的是 `defaultHiddenColumnKeys(fields)` 这个只在测试里用的辅助函数，
    // 断言的是「辅助函数的输出」而不是表格真的显示什么。改成直接读生产路径：
    // 宽度给到无限，默认档仍然不把 detail 层带回来（那是「全部列」的事）。
    const detailKeys = [
      'reference',
      'lighting_mood',
      'sound',
      'prop_tags',
      'character_description_1',
      'character_image_1',
      'character_description_2',
      'character_image_2',
    ];
    const wide = resolveVisibleScriptColumns({
      mode: 'default',
      availableWidthPx: Number.POSITIVE_INFINITY,
    });
    const visibleKeys = wide.map((field) => field.key);
    for (const key of detailKeys) expect(visibleKeys).not.toContain(key);
    for (const key of FIT_ALWAYS_KEEP) expect(visibleKeys).toContain(key);
    // 反过来：切到「全部列」这些明细列必须都在 —— 否则上面那半段可能只是因为
    // 字段清单里根本没有它们而侥幸通过。
    const all = resolveVisibleScriptColumns({ mode: 'all' }).map((field) => field.key);
    for (const key of detailKeys) expect(all).toContain(key);
  });

  it('33 列合计 4850px，默认列在 800px 节点里放得下', () => {
    const all = scriptFieldSequence();
    expect(all).toHaveLength(33);
    expect(fieldsTotalWidth(all)).toBe(4850);
    const visible = resolveVisibleScriptColumns({
      mode: 'default',
      availableWidthPx: NODE_CONTENT_WIDTH,
    });
    expect(fieldsTotalWidth(visible)).toBeLessThanOrEqual(NODE_CONTENT_WIDTH);
    // 而且确实收掉了明细列，不是把 19 列原样塞进去。
    expect(visible.length).toBeLessThan(all.length);
  });

  it('all 恒为全列；manual 听黑名单；宽度给够时默认档带回全部核心列', () => {
    expect(resolveVisibleScriptColumns({ mode: 'all' })).toHaveLength(
      scriptFieldSequence().length,
    );

    // manual：显式黑名单说了算（这里只收「景别」）。
    const manual = resolveVisibleScriptColumns({ mode: 'manual', hidden: ['shot'] });
    expect(manual.map((field) => field.key)).not.toContain('shot');
    expect(manual.map((field) => field.key)).toContain('dialogue');

    // manual 但没勾过（黑名单缺席）→ 退回宽度自适应。
    const auto = resolveVisibleScriptColumns({
      mode: 'manual',
      availableWidthPx: NODE_CONTENT_WIDTH,
    });
    expect(auto.map((field) => field.key)).toEqual(
      resolveVisibleScriptColumns({ mode: 'default', availableWidthPx: NODE_CONTENT_WIDTH }).map(
        (field) => field.key,
      ),
    );

    // 宽度富余到能装下全部核心列。
    const roomy = resolveVisibleScriptColumns({ mode: 'default', availableWidthPx: 4000 });
    for (const key of FIT_ALWAYS_KEEP) expect(roomy.map((f) => f.key)).toContain(key);
    // 明细列仍不进来（那是「全部列」的事）。
    for (const key of DETAIL_ADD_BACK_ORDER) expect(roomy.map((f) => f.key)).not.toContain(key);
  });

  it('必留列 740px 压住 800px 节点：默认档在默认宽度下正好只剩必留列', () => {
    const visible = resolveVisibleScriptColumns({
      mode: 'default',
      availableWidthPx: NODE_CONTENT_WIDTH,
    });
    // 782 - 740 = 42px，任何一种核心列（最窄 120px）都塞不进来。
    expect(visible.map((field) => field.key)).toEqual(expect.arrayContaining([...FIT_ALWAYS_KEEP]));
    for (const key of CORE_ADD_BACK_ORDER) {
      expect(visible.map((field) => field.key)).not.toContain(key);
    }

    // 把节点拉宽 140px → 景别（120px）就回来了。
    const wider = resolveVisibleScriptColumns({
      mode: 'default',
      availableWidthPx: NODE_CONTENT_WIDTH + 140,
    });
    expect(wider.map((field) => field.key)).toContain('shot');
  });

  it('放不下的列只跳过不终止：窄列在宽列挤不进时仍能补位', () => {
    // 必留 740 + 景别 120 = 860 > 900？不，860 <= 900，所以景别进得来；
    // 再下一个是角色名 120 → 980 > 900，跳过；此处无窄列可补，用明细档验证。
    const withDetails = resolveVisibleScriptColumns({ mode: 'all' });
    expect(withDetails).toHaveLength(33);

    // 1000px：必留 740 + 景别 120 = 860；角色 120 → 980 ✓；角色动作 120 → 1100 ✗。
    const width1000 = resolveVisibleScriptColumns({
      mode: 'default',
      availableWidthPx: 1000,
    });
    const keys = width1000.map((field) => field.key);
    expect(keys).toContain('shot');
    expect(keys).toContain('character_1');
    expect(keys).not.toContain('character_action');
  });

  it('必留列在任何宽度下都不被收回，优先级表覆盖全部非必留列', () => {
    const fields = scriptFieldSequence();
    const keys = new Set(fields.map((field) => field.key));
    for (const key of FIT_ALWAYS_KEEP) expect(keys.has(key)).toBe(true);
    for (const key of SCRIPT_COLUMN_PRIORITY) {
      expect(keys.has(key)).toBe(true);
      expect(FIT_ALWAYS_KEEP).not.toContain(key);
    }
    // 优先级表 + 必留列 = 全部字段，一个不漏一个不重。
    expect(new Set([...FIT_ALWAYS_KEEP, ...SCRIPT_COLUMN_PRIORITY]).size).toBe(fields.length);
    // 角色 1 排在角色 2 前面。
    expect(CORE_ADD_BACK_ORDER.indexOf('character_1')).toBeLessThan(
      CORE_ADD_BACK_ORDER.indexOf('character_2'),
    );
  });

  it('逐列勾选落到 manual，且至少留一列', () => {
    const fields = scriptFieldSequence();
    const visibleKeys = resolveVisibleScriptColumns({ mode: 'default' }).map((f) => f.key);

    // 勾掉一列 → manual，且该列进了黑名单。
    const off = toggleScriptColumn({ fields, visibleKeys, key: 'dialogue' });
    expect(off.mode).toBe('manual');
    expect(off.hiddenColumns).toContain('dialogue');
    // 未被勾掉的核心列不应进黑名单。
    expect(off.hiddenColumns).not.toContain('shot_prompt');

    // 勾回来 → 该列离开黑名单。
    const back = toggleScriptColumn({
      fields,
      visibleKeys: visibleKeys.filter((key) => key !== 'dialogue'),
      key: 'dialogue',
    });
    expect(back.hiddenColumns).not.toContain('dialogue');

    // 只剩一列时不允许再关掉。
    const last = toggleScriptColumn({ fields, visibleKeys: ['shot_no'], key: 'shot_no' });
    expect(last.hiddenColumns).not.toContain('shot_no');
    expect(last.hiddenColumns).toHaveLength(fields.length - 1);
  });

  it('计数文案', () => {
    expect(scriptColumnSummary(12, 19)).toBe('12 / 19 列');
  });

  // 这条是回归钉：纯函数返回 `{ mode }`，节点数据读 `data.columnMode`。
  // 早先直接把纯函数结果交给 updateNodeData，档位被写进没人读的 `data.mode`，
  // 真机症状是「菜单勾选态变了、表格列数纹丝不动」；索引签名把拼写错误吞掉了。
  it('逐列勾选的补丁用 columnMode 而不是 mode', () => {
    const fields = scriptFieldSequence();
    const visibleKeys = resolveVisibleScriptColumns({ mode: 'all' }).map((f) => f.key);
    const patch = toggleScriptColumnPatch({ fields, visibleKeys, key: 'shot_no' });

    expect(Object.keys(patch).sort()).toEqual(['columnMode', 'hiddenColumns']);
    expect(patch).not.toHaveProperty('mode');
    expect(patch.columnMode).toBe('manual');
    expect(patch.hiddenColumns).toContain('shot_no');
    // 补丁喂回去要真的少一列（补丁是节点数据口径 `columnMode`/`hiddenColumns`，
    // 纯函数是 `mode`/`hidden` —— 这层改名在 ScriptNode 的调用点做），
    // 端到端闭合，而不是只对字段名。
    const visibleAfter = resolveVisibleScriptColumns({
      mode: patch.columnMode,
      hidden: patch.hiddenColumns,
    }).map((f) => f.key);
    expect(visibleAfter).not.toContain('shot_no');
    expect(visibleAfter).toHaveLength(fields.length - 1);
  });
});

describe('脚本读数', () => {
  it('时长解析：整数 / 小数 / 带单位都能读，读不出返回 null', () => {
    expect(parseDurationSeconds(5)).toBe(5);
    expect(parseDurationSeconds('5')).toBe(5);
    expect(parseDurationSeconds('3.5')).toBe(3.5);
    expect(parseDurationSeconds('4 秒')).toBe(4);
    expect(parseDurationSeconds(' .5s ')).toBe(0.5);
    for (const value of ['-2', '-2 秒', '3-5s', '3–5 秒', '约3秒', '1分30秒', '3.4.5', 'Infinity']) {
      expect(parseDurationSeconds(value)).toBeNull();
    }
    expect(parseDurationSeconds('')).toBeNull();
    expect(parseDurationSeconds('待定')).toBeNull();
    expect(parseDurationSeconds(0)).toBeNull();
    expect(parseDurationSeconds(-2)).toBeNull();
    expect(parseDurationSeconds(null)).toBeNull();
  });

  it('总时长只累计读得出来的行，并把读不出的单独计数', () => {
    const stats = computeScriptStats([
      row({ duration: '3' }),
      row({ shot_no: '2', duration: '2.5' }),
      row({ shot_no: '3', duration: '' }),
    ]);
    expect(stats.shotCount).toBe(3);
    expect(stats.totalDurationSec).toBe(5.5);
    expect(stats.unknownDurationCount).toBe(1);
  });

  it('景别按主景别计数（`近景 / 特写` 记近景），取前三 + 其它 + 未填', () => {
    const stats = computeScriptStats([
      row({ shot: '近景 / 特写' }),
      row({ shot_no: '2', shot: '近景' }),
      row({ shot_no: '3', shot: '远景' }),
      row({ shot_no: '4', shot: '中景' }),
      row({ shot_no: '5', shot: '全景' }),
      row({ shot_no: '6', shot: '中景' }),
      row({ shot_no: '7', shot: '' }),
    ]);
    expect(stats.shotSizeTop[0]).toEqual({ label: '近景', count: 2 });
    expect(stats.shotSizeTop).toHaveLength(3);
    // 计数 近景2 / 中景2 / 远景1 / 全景1：前三取「近景、中景、远景」，剩下全景计入其它。
    expect(stats.shotSizeTop.map((bucket) => bucket.label)).toEqual(['近景', '中景', '远景']);
    expect(stats.shotSizeOtherCount).toBe(1);
    expect(stats.shotSizeEmptyCount).toBe(1);
  });

  it('缺项三件：图片提示词 / 运动提示词 / 角色图', () => {
    const stats = computeScriptStats([
      row({
        shot_prompt: '推近',
        video_motion_prompt: '缓推',
        character_1: '阿雀',
        character_image_1: 'a.png',
      }),
      // 只有画面描述没有分镜提示词 → 不算缺（rowImagePrompt 会回落）。
      row({ shot_no: '2', video_motion_prompt: '横移' }),
      // 角色没图 + 两条提示词全空。
      row({ shot_no: '3', visual_description: '', shot_prompt: '', character_2: '老树' }),
    ]);
    expect(stats.missingImagePromptCount).toBe(1);
    expect(stats.missingMotionPromptCount).toBe(1);
    expect(stats.characterWithoutImageCount).toBe(1);
    expect(stats.missingCharacterNames).toEqual(['老树']);
    expect(stats.isReady).toBe(false);

    const clean = computeScriptStats([
      row({ shot_prompt: '推近', video_motion_prompt: '缓推' }),
    ]);
    expect(clean.isReady).toBe(true);
  });

  it('空表既不是 ready 也不报缺项', () => {
    const stats = computeScriptStats([]);
    expect(stats.shotCount).toBe(0);
    expect(stats.isReady).toBe(false);
    expect(stats.totalDurationSec).toBe(0);
  });

  it('超过单条上限的镜单独计数，且足以让 isReady 变假', () => {
    const stats = computeScriptStats([
      // 15s 是上限本身（libtv 硬规则 / 本仓 clampVideoDuration），不算超。
      row({ duration: '15', shot_prompt: '推近', video_motion_prompt: '缓推' }),
      row({ shot_no: '2', duration: '30', shot_prompt: '拉远', video_motion_prompt: '缓推' }),
    ]);
    expect(stats.overlongDurationCount).toBe(1);
    // 时长仍然照实累计（它是表里的真值，不是出片后的真值）。
    expect(stats.totalDurationSec).toBe(45);
    expect(stats.isReady).toBe(false);
    expect(summarizeScriptStats(stats, [])).toContain('1 镜超过 15s，需核对模型支持');
  });

  it('时长文案与摘要', () => {
    expect(formatTotalDuration(0)).toBe('—');
    expect(formatTotalDuration(12)).toBe('12s');
    expect(formatTotalDuration(12.5)).toBe('12.5s');
    const rows = [
      row({ duration: '3', shot_prompt: '推近' }),
      row({ shot_no: '2', duration: '2' }),
    ];
    const summary = summarizeScriptStats(computeScriptStats(rows), rows);
    expect(summary).toContain('共 2 个分镜');
    expect(summary).toContain('合 5s');
    expect(summary).toContain('镜号 1–2');
  });

  describe('景别节奏（相邻镜要换档，libtv §DISTILL/06_AGENT_BEHAVIOR_SPEC.md §4.1）', () => {
    it('同景别连着两镜才报，换档就合格，并指到具体是哪两镜', () => {
      const rhythm = computeScriptShotRhythm([
        row({ shot_no: '1', shot: '全景' }),
        // 全景 → 中景：libtv 自己举的例子（「跳两档」），合格。
        row({ shot_no: '2', shot: '中景' }),
        // 中景 → 近景：也合格 —— 语料的「两档」与五档阶梯不是同一刻度，
        // 按字面卡「必须跨两档」会把语料的标准答案判成错的，这里不设数字阈值。
        row({ shot_no: '3', shot: '近景' }),
        row({ shot_no: '4', shot: '特写' }),
        // 特写 → 特写：取景一样，切出来是卡带。
        row({ shot_no: '5', shot: '特写' }),
        // 特写 → 全景：跳得很开，合格。
        row({ shot_no: '6', shot: '全景' }),
      ]);
      expect(rhythm.issues.map((issue) => issue.shotNumber)).toEqual(['5']);
      expect(rhythm.issues[0]).toMatchObject({
        previousShotNumber: '4',
        previousShotSize: '特写',
        shotSize: '特写',
      });
      expect(rhythm.unknownCount).toBe(0);
    });

    it('主景别优先（`中景 / 仰视` 按中景判），认不出档位的行当作链条断点不误报', () => {
      const rhythm = computeScriptShotRhythm([
        row({ shot_no: '1', shot: '全景 / 俯视远景' }),
        row({ shot_no: '2', shot: '中景' }),
        // 自由文本：不在五档里 → 断点，不拿它与前后镜比（记一笔 unknown）。
        row({ shot_no: '3', shot: '侧面跟拍' }),
        row({ shot_no: '4', shot: '近景' }),
      ]);
      // 1→2 换档合格；3 是断点，所以 2→3 与 3→4 都不判。
      expect(rhythm.issues).toEqual([]);
      expect(rhythm.unknownCount).toBe(1);

      // 断点两侧是同一个景别也不报 —— 认不出档位时不猜。
      const acrossUnknown = computeScriptShotRhythm([
        row({ shot_no: '1', shot: '中景' }),
        row({ shot_no: '2', shot: '侧面跟拍' }),
        row({ shot_no: '3', shot: '中景' }),
      ]);
      expect(acrossUnknown.issues).toEqual([]);
    });

    it('首镜人物自查：整片有角色而首镜没有才报，全片无角色不报', () => {
      expect(
        computeScriptShotRhythm([
          row({ shot_no: '1', shot: '全景' }),
          row({ shot_no: '2', shot: '特写', character_1: '阿雀' }),
        ]).firstShotWithoutCharacter,
      ).toBe(true);
      // 首镜有人 → 不报。
      expect(
        computeScriptShotRhythm([
          row({ shot_no: '1', shot: '全景', character_1: '阿雀' }),
          row({ shot_no: '2', shot: '特写' }),
        ]).firstShotWithoutCharacter,
      ).toBe(false);
      // 整片就是空镜 / 风光 → 首镜没人是对的，不报（否则纯风光的脚本永远挂着一条噪声）。
      expect(
        computeScriptShotRhythm([
          row({ shot_no: '1', shot: '全景' }),
          row({ shot_no: '2', shot: '特写' }),
        ]).firstShotWithoutCharacter,
      ).toBe(false);
      expect(computeScriptShotRhythm([]).firstShotWithoutCharacter).toBe(false);
    });
  });
});
