import { describe, expect, it } from 'vitest';
import { collectScriptAssetLedger } from '@/features/canvas/nodes/script/scriptAssets';
import { collectScriptVisualStyle, generateScriptAssetImages, scriptAssetImagePrompt } from '@/features/canvas/nodes/script/scriptAssetGen';
import { useCanvasStore } from '@/stores/canvasStore';
import { CANVAS_NODE_TYPES } from '@/features/canvas/domain/canvasNodes';
import { buildScriptShotSpecs } from '@/features/canvas/nodes/script/scriptStoryboard';
import { scriptShotVideoPrompt } from '@/features/canvas/nodes/script/scriptShotVideos';
import { SCRIPT_IMAGE_RENDER_QUALITY, SCRIPT_VIDEO_RENDER_QUALITY, withScriptImageQuality, withScriptVideoQuality } from '@/features/canvas/nodes/script/scriptRenderQuality';

describe('script render quality reaches generated prompts', () => {
  const row = {
    shot_no: '1', character_1: '阿波', character_description_1: '绿色毛发，红色夹克',
    scene_tags: '屋顶', prop_tags: '滑板', shot_prompt: '晨光下阿波站在屋顶',
    video_motion_prompt: '阿波先压低重心，随后蹬地向前滑行',
    start_state: '滑板前轮接触地面', end_state: '滑向屋顶边缘',
  };

  it('keeps composite style and nested material facts in every actual asset node', () => {
    const style = '手绘笔触 + 风格化3D，材质[织物 + 金属]，洁净渐变';
    const styled = { ...row, shot_prompt: `[角色卡：[阿波：绿毛] + [小雀：蓝羽]] + [视觉风格/质感：${style}] + [技术参数：35mm]` };
    expect(collectScriptVisualStyle([styled])).toBe(style);
    const previousNodes = useCanvasStore.getState().nodes;
    const previousEdges = useCanvasStore.getState().edges;
    try {
      useCanvasStore.getState().setCanvasData([{
        id: 'style-test-script', type: CANVAS_NODE_TYPES.script, position: { x: 0, y: 0 },
        data: { scriptResult: { rows: [styled] } },
      }], []);
      const ledger = collectScriptAssetLedger([styled], new Map());
      const result = generateScriptAssetImages({
        scriptNodeId: 'style-test-script', assets: ledger.all, scriptSize: { width: 800, height: 400 },
        config: { model: 'test-image', aspectRatio: 'auto' }, style: collectScriptVisualStyle([styled]),
        generateImages: false,
      });
      expect(result.ok).toBe(true);
      const nodes = useCanvasStore.getState().nodes.filter(node => node.id !== 'style-test-script');
      expect(nodes).toHaveLength(3);
      for (const node of nodes) {
        expect(node.data.prompt).toContain(`图片风格为：${style}。`);
        expect(node.data.prompt).toContain(SCRIPT_IMAGE_RENDER_QUALITY);
        expect(node.data.canvas_auto_generate_once).toBe(false);
      }
    } finally {
      useCanvasStore.getState().setCanvasData(previousNodes, previousEdges);
    }
  });

  it('covers every asset family and both character templates without dropping design', () => {
    const ledger = collectScriptAssetLedger([row], new Map());
    expect(ledger.all.map((asset) => asset.role)).toEqual(['character', 'scene', 'prop']);
    for (const asset of ledger.all) {
      for (const viewMode of ['multi_view', 'single_view']) {
        const prompt = scriptAssetImagePrompt(asset, { viewMode, style: '风格化3D动画' });
        expect(prompt).toContain(SCRIPT_IMAGE_RENDER_QUALITY);
        expect(prompt).toContain(asset.name);
        expect(prompt).toContain('风格化3D动画');
      }
    }
  });

  it('preserves first-frame state and moving action while adding quality to final requests', () => {
    const frame = buildScriptShotSpecs([row])[0];
    expect(frame.prompt).toContain(SCRIPT_IMAGE_RENDER_QUALITY);
    expect(frame.prompt).toContain(row.start_state);
    expect(frame.basePrompt).not.toContain('RENDER QUALITY:');
    const video = scriptShotVideoPrompt(row);
    expect(video.prompt).toContain(SCRIPT_VIDEO_RENDER_QUALITY);
    expect(video.prompt).toContain(row.video_motion_prompt);
    expect(video.prompt).toContain(row.end_state);
    expect(scriptShotVideoPrompt({}).prompt).toBe('');
    expect(buildScriptShotSpecs([{}])[0].prompt).toBe('');
  });

  it('keeps quality inside the environment slot of a structured motion prompt', () => {
    const structured = {
      ...row,
      video_motion_prompt: '[明确的摄影机运镜轨迹与速度：跟拍] + [主体极其具体的物理动作细节或状态变化：阿波蹬地滑行] + [环境物理动态：夹克随风摆动] + [音效与氛围描述：轮声] + [对话台词与语气：无] + [时长：5s]',
    };
    const prompt = scriptShotVideoPrompt(structured).prompt;
    const chunks = prompt.split(' + ');
    expect(chunks).toHaveLength(6);
    expect(chunks[2]).toContain(SCRIPT_VIDEO_RENDER_QUALITY);
    expect(chunks[4]).toBe('[对话台词与语气：无]');
    expect(chunks[5]).toContain('[时长：5s]');
    expect(prompt).toContain(row.end_state);
  });

  it('guards temporal noise without erasing identity, texture or motivated movement', () => {
    const frame = withScriptImageQuality(row.shot_prompt);
    expect(frame).toContain('去噪不改变角色五官');
    expect(withScriptImageQuality(frame)).toBe(frame);
    const video = withScriptVideoQuality(row.video_motion_prompt);
    for (const detail of ['噪点跳动', '纹理爬动', '边缘闪烁', '自然运动模糊', '真实光影变化', '剧情要求的磨损']) {
      expect(video).toContain(detail);
    }
    expect(withScriptVideoQuality(video)).toBe(video);
  });

  it('actual frame and video prompts carry current clothing and goggles over baseline clothing', () => {
    const current = { ...row, character_state_start: { 阿波: '赤膊，红色护目镜戴在眼前' },
      character_state_end: { 阿波: '赤膊，红色护目镜戴在眼前' } };
    const frame = buildScriptShotSpecs([current])[0];
    const video = scriptShotVideoPrompt(current);
    for (const prompt of [frame.prompt, video.prompt]) {
      expect(prompt).toContain('阿波：赤膊，红色护目镜戴在眼前');
      expect(prompt).toContain('当前状态优先于角色卡和参考图中的基准服装');
    }
    expect(frame.prompt).toContain(SCRIPT_IMAGE_RENDER_QUALITY);
    expect(video.prompt).toContain(SCRIPT_VIDEO_RENDER_QUALITY);
    expect(video.prompt).toContain(current.video_motion_prompt);
    expect(video.prompt).toContain('全程：阿波：赤膊');
    expect(video.prompt.match(/阿波：赤膊/g)).toHaveLength(1);
  });

  it('keeps distinct equipment changes and the complete physical result', () => {
    const changed = { ...row, character_state_start: { 阿波: '护目镜戴在眼前' },
      character_state_end: { 阿波: '护目镜摘下握在右手' },
      video_motion_prompt: '阿波停稳，右手摘下护目镜，停顿后望向小雀，夹克衣摆缓慢落下' };
    const prompt = scriptShotVideoPrompt(changed).prompt;
    expect(prompt).toContain('起始：阿波：护目镜戴在眼前 → 结束：阿波：护目镜摘下握在右手');
    expect(prompt).toContain(changed.video_motion_prompt);
    expect(prompt).toContain('在后续动作和结束状态中持续成立');
  });

  it('current states replace stale internal state blocks without losing action', () => {
    const old = '<character_state>阿波穿蓝衣。</character_state>';
    const current = { ...row, character_state_start: { 阿波: '赤膊，红色护目镜戴在眼前' },
      character_state_end: { 阿波: '赤膊，红色护目镜戴在眼前' },
      shot_prompt: row.shot_prompt + old, video_motion_prompt: row.video_motion_prompt + old };
    for (const prompt of [buildScriptShotSpecs([current])[0].prompt, scriptShotVideoPrompt(current).prompt]) {
      expect(prompt).toContain('赤膊，红色护目镜戴在眼前');
      expect(prompt).not.toContain('蓝衣');
      expect(prompt).not.toContain('character_state');
    }
    expect(scriptShotVideoPrompt(current).prompt).toContain(row.video_motion_prompt);
  });

  it('段内多动作连接不破坏质量段、摄影机或时长', () => {
    const row = { video_motion_prompt: '[明确的摄影机运镜轨迹与速度：固定] + [主体极其具体的物理动作细节或状态变化：阿波伸手 + 小雀接住] + [环境物理动态：暖主光 + 冷补光] + [音效与氛围描述：风声] + [对话台词与语气：无] + [时长：5s]', camera_movement: '缓慢横移' };
    const result = scriptShotVideoPrompt(row).prompt;
    expect(result).toContain('[明确的摄影机运镜轨迹与速度：缓慢横移]');
    expect(result).toContain('阿波伸手 + 小雀接住');
    expect(result).toContain(`[环境物理动态：暖主光 + 冷补光；${SCRIPT_VIDEO_RENDER_QUALITY}]`);
    expect(result).toContain('[对话台词与语气：无] + [时长：5s]');
  });

  it('binds design definitions by exact asset name and invalidates a changed design', () => {
    const designed = {
      ...row, scene_tags: '屋顶、仓库', prop_tags: '滑板、护目镜',
      scene_descriptions: { 屋顶: '红色排气管在东侧，围栏沿西边，两扇天窗之间可通行', 仓库: '蓝色卷帘门朝南' },
      prop_descriptions: { 滑板: '青色短板，黑色防滑面，银色桥架，橙色轮子', 护目镜: '透明镜片、紫色绑带' },
    };
    const ledger = collectScriptAssetLedger([designed], new Map());
    const roof = ledger.scenes.find((asset) => asset.name === '屋顶')!;
    const board = ledger.props.find((asset) => asset.name === '滑板')!;
    expect(scriptAssetImagePrompt(roof)).toContain(designed.scene_descriptions.屋顶);
    expect(scriptAssetImagePrompt(roof)).not.toContain('蓝色卷帘门');
    expect(scriptAssetImagePrompt(board)).toContain(designed.prop_descriptions.滑板);
    expect(scriptAssetImagePrompt(board)).not.toContain('紫色绑带');
    const changed = collectScriptAssetLedger([{ ...designed, prop_descriptions: { ...designed.prop_descriptions, 滑板: '红色长板' } }], new Map());
    expect(changed.props.find((asset) => asset.name === '滑板')!.contentHash).not.toBe(board.contentHash);
    expect(changed.scenes.find((asset) => asset.name === '屋顶')!.contentHash).toBe(roof.contentHash);
    // An old first row without definitions does not hide a later explicit definition.
    expect(collectScriptAssetLedger([row, designed], new Map()).props[0].description).toBe(designed.prop_descriptions.滑板);
  });
});
