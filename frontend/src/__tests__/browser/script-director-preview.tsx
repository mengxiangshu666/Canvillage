import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import '@/index.css';
import type { FreezoneStoryDirectorPlan } from '@/api/scriptContract';
import { ScriptDirectorPlan } from '@/features/canvas/nodes/script/ScriptDirectorPlan';
import { ScriptCreativeView } from '@/features/canvas/nodes/script/ScriptCreativeView';
import { useCanvasStore } from '@/stores/canvasStore';
import { buildScriptRowSnapshots } from '@/features/canvas/nodes/script/scriptStaleness';
import type { CanvasNode } from '@/features/canvas/domain/canvasNodes';
import { generateScriptStoryboard } from '@/features/canvas/nodes/script/generateScriptStoryboard';
import { scatterScriptShotVideos, planScriptShotVideos } from '@/features/canvas/nodes/script/scriptShotVideos';
import { assembleScriptFilm } from '@/features/canvas/nodes/script/scriptShotCompose';
import { ScriptShotRewriteDialog } from '@/features/canvas/nodes/script/ScriptShotRewriteDialog';
import { ScriptShotVideoDialog } from '@/features/canvas/nodes/script/ScriptShotVideoDialog';

const rows = [
  { shot_no: 1, duration: 2, shot: '全景', visual_description: '阿波站在高楼边缘，查看滑板与远处落点。', shot_purpose: '看清危险与距离', film_language: '空间建立与视线匹配', start_state: '右脚压板，身体前倾', end_state: '向右离开楼缘', cut_reason: '切向跨越间隙的动作', sound: '风声与板轮摩擦声', transition_plan: 'continuous_action' },
  { shot_no: 2, duration: 3, shot: '中景', visual_description: '阿波滑向右侧平台，压低身体穿过悬梁。', shot_purpose: '体验压力与速度', start_state: '身体向右腾空', end_state: '右脚落板，膝盖缓冲', cut_reason: '收束挑战' },
];
// Synthetic rendered frames only exercise result selection, never artistic quality.
function frame(color: string) {
  const canvas = document.createElement('canvas');
  canvas.width = 640; canvas.height = 360;
  const ctx = canvas.getContext('2d')!;
  ctx.fillStyle = color; ctx.fillRect(0, 0, 640, 360);
  ctx.fillStyle = '#323c40'; ctx.fillRect(0, 270, 640, 90);
  ctx.fillStyle = '#ffffff'; ctx.fillRect(300, 130, 40, 140);
  return canvas.toDataURL('image/png');
}
const images = [frame('#328aa1'), frame('#528c60')];
const cameraTexts = [
  '固定的中景背影机位，镜头始终跟随跑者奔跑，跑者起跑后镜头不再移动',
  '固定的中景背影机位，镜头跟随跑者奔跑，速度稳定，无切镜',
  '摄影机先沿岸边缓慢横移，落到倒影后停住',
  '固定在桌边，双人中景全程同框，保持结束构图',
];
const cameraRows = cameraTexts.map((camera, i) => ({ shot_no: i + 1, duration: 8, shot_prompt: `[画面构图：中景${i}]`,
  video_motion_prompt: `[${camera}] + [主体动作：主体按本镜剧情行动] + [环境物理动态：稳定] + [音效：环境声] + [对话台词：无] + [时长：8s]` }));
const cameraSnapshots = buildScriptRowSnapshots(cameraRows);
const cameraPlan = planScriptShotVideos('camera-script', {
  nodes: [{ id: 'camera-script', type: 'scriptNode', position: { x: 0, y: 0 }, data: { scriptResult: { title: '摄影核对', rows: cameraRows } } },
    ...cameraSnapshots.map((row, i) => ({ id: `camera-frame-${i}`, type: 'imageGenNode', position: { x: 0, y: 0 },
      data: { scriptRowKey: row.rowKey, scriptRowPrompt: row.prompt, scriptRowReference: row.reference, scriptRowAssetSnapshot: row.assetRevision, imageUrl: images[i % 2] } }))] as CanvasNode[],
  edges: cameraSnapshots.map((_, i) => ({ id: `camera-edge-${i}`, source: 'camera-script', target: `camera-frame-${i}`, data: { role: 'storyboard' } })),
});
if (!cameraPlan.ok) throw new Error(cameraPlan.reason);
const cameraAuditRows = cameraPlan.rows;
const snapshots = buildScriptRowSnapshots(rows);
useCanvasStore.setState({
  nodes: [{ id: 'fixture-script', type: 'scriptNode', position: { x: 0, y: 0 }, data: {} }, ...snapshots.map((row, i) => ({ id: `fixture-${i}`, type: 'imageGenNode', position: { x: 0, y: 0 }, data: { scriptRowKey: row.rowKey, scriptRowPrompt: row.prompt, scriptRowReference: row.reference, scriptRowAssetSnapshot: row.assetRevision, imageUrl: images[i] } }))] as CanvasNode[],
  edges: snapshots.map((_, i) => ({ id: `fixture-edge-${i}`, source: 'fixture-script', target: `fixture-${i}`, data: { role: 'storyboard' } })),
});
function Fixture() {
  const [plan, setPlan] = useState<FreezoneStoryDirectorPlan>({ story_promise: '惊险但温暖的高空挑战', rhythm_curve: '观察、起跳、紧张、落地释放', sound_plan: '风声延续，落板瞬间强调冲击', visual_bible: { visual_style: '动画，清晰的轮廓与空间关系', lighting: '侧后方日光随场景保持一致' }, sequences: [{ sequence_id: 'S1', title: '高空挑战', shot_nos: [1, 2], dramatic_goal: '期待安全落地', resistance: '间隙与低悬梁', turn: '压低身体越障', release: '落地后松一口气' }] });
  const [pending, setPending] = useState(false);
  const [gateResult, setGateResult] = useState('');
  const [rewriteSequence, setRewriteSequence] = useState<string | null>(null);
  const [rewriteInstruction, setRewriteInstruction] = useState('');
  const [durationDialog, setDurationDialog] = useState(false);
  const [cameraDialog, setCameraDialog] = useState(false);
  return <main className="mx-auto min-h-screen max-w-5xl bg-bg-dark p-3 text-text-dark">
    <ScriptDirectorPlan plan={plan} rows={rows} pending={pending} onRewriteSequence={setRewriteSequence} onCommit={next => { setPlan(next); setPending(true); useCanvasStore.getState().updateNodeData('fixture-script', { scriptDirectorPlanNeedsSync: true }); }} />
    <ScriptShotRewriteDialog open={rewriteSequence !== null} rowIndex={0} shotNo="" sequenceLabel="高空挑战" targetCount={2}
      currentSummary={rows.map(row => row.visual_description).join('；')} frozenFacts={[]} untouchedCount={0}
      onCancel={() => setRewriteSequence(null)} onSubmit={instruction => { setRewriteInstruction(`${rewriteSequence}:${instruction}`); setRewriteSequence(null); }} />
    <output aria-label="段落返工要求">{rewriteInstruction}</output>
    <button onClick={() => setDurationDialog(true)}>检查素材时长</button>
    <button onClick={() => setCameraDialog(true)}>检查摄影安排</button>
    <ScriptShotVideoDialog open={cameraDialog} mode="regenerate" shotCount={4} pendingCount={4} model="fixture" aspectKey="16:9"
      rows={cameraAuditRows} onCancel={() => setCameraDialog(false)} onModelChange={() => {}} onAspectChange={() => {}} onConfirm={() => setCameraDialog(false)} />
    <ScriptShotVideoDialog open={durationDialog} mode="create" shotCount={2} pendingCount={2} model="fixture" aspectKey="16:9" priceDisplay="10"
      rows={rows.map((row, i) => ({ rowKey: `shot:${i + 1}`, shotNumber: String(i + 1), durationSec: row.duration,
        generationDurationSec: 5, firstFrameUrl: images[i], camera: '快速横移，保持动作节奏', promptSource: 'motion', dialogue: '', audioRoute: 'native', issues: [] }))}
      onCancel={() => setDurationDialog(false)} onModelChange={() => {}} onAspectChange={() => {}} onConfirm={() => setDurationDialog(false)} />
    <div className="pt-3"><ScriptCreativeView rows={rows} scriptNodeId="fixture-script" /></div>
    <button onClick={() => useCanvasStore.getState().updateNodeData('fixture-0', { imageUrl: images[1] })}>替换测试分镜</button>
    <button onClick={() => useCanvasStore.getState().updateNodeData('fixture-0', { scriptRowPrompt: '旧内容' })}>标记测试分镜过期</button>
    <button onClick={() => {
      const before = useCanvasStore.getState();
      const results = [generateScriptStoryboard({ scriptNodeId: 'fixture-script', rows, scriptSize: { width: 800, height: 400 }, config: { model: 'fixture' }, generateImages: true }), scatterScriptShotVideos({ scriptNodeId: 'fixture-script', generateVideos: true }), assembleScriptFilm({ scriptNodeId: 'fixture-script' })];
      const after = useCanvasStore.getState();
      const refused = results.every(result => !result.ok && result.reason.includes('请先重新生成脚本'));
      setGateResult(refused && before.nodes === after.nodes && before.edges === after.edges ? '三项均阻断，节点与边未修改' : '检查失败');
    }}>验证规划未同步阻断</button>
    <output aria-label="制作阻断结果">{gateResult}</output>
  </main>;
}
createRoot(document.getElementById('root')!).render(<Fixture />);
