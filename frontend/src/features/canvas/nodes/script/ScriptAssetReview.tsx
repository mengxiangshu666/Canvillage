import { useState } from 'react';
import { Check, RefreshCw, X } from 'lucide-react';
import { resolveImageDisplayUrl } from '@/features/canvas/application/imageData';
import { focusDerivedNodes } from '@/features/canvas/application/focusDerivedNodes';
import type { ScriptAsset } from './scriptAssets';
import { saveAssetReview } from './scriptAssetReviewReceipt';

export function ScriptAssetReview({ asset, onClose }: { asset: ScriptAsset; onClose: () => void }) {
  const [checks, setChecks] = useState(asset.visualReview?.checks ?? { clean: false, identity: false, structure: false });
  const [views, setViews] = useState<string[]>(asset.visualReview?.views ?? []);
  const [notes, setNotes] = useState(asset.visualReview?.notes ?? '');
  const [error, setError] = useState('');
  const [loaded, setLoaded] = useState(false);
  const [imageError, setImageError] = useState(false);
  const save = (status: 'passed' | 'blocked') => {
    const reason = saveAssetReview(asset, { status, checks, views, notes });
    if (reason) setError(reason); else onClose();
  };
  return <section aria-label={`审看资产 ${asset.name}`} className="nodrag nowheel border-b border-white/10 pb-3 text-xs">
    <header className="flex items-center justify-between gap-2"><span className="break-words">{asset.name}</span><button type="button" aria-label="关闭资产审看" title="关闭资产审看" onClick={onClose} className="p-1"><X size={16} /></button></header>
    <img src={resolveImageDisplayUrl(asset.imageUrl!)} alt={asset.name} onLoad={() => setLoaded(true)} onError={() => { setImageError(true); setLoaded(false); }} className="my-2 max-h-96 w-full object-contain" />
    {imageError && <p role="alert">图片加载失败</p>}
    <div className="flex flex-wrap gap-3">{([
      ['clean', '无随机噪点、压缩块、摩尔纹'], ['identity', '身份与风格一致'], ['structure', '结构和材质细节正确'],
    ] as const).map(([key, label]) => <label key={key} className="flex items-start gap-1"><input type="checkbox" checked={checks[key]} onChange={event => setChecks({ ...checks, [key]: event.target.checked })} />{label}</label>)}</div>
    <div className="my-2 flex flex-wrap gap-3">{[...new Set([...(asset.requiredViews ?? []), ...(asset.plannedViews ?? [])])].map(view => <label key={view} className="flex items-center gap-1"><input type="checkbox" checked={views.includes(view)} onChange={event => setViews(event.target.checked ? [...views, view] : views.filter(item => item !== view))} />{({ front: '正面', side: '侧面', back: '背面', full_body: '全身', top: '俯视', low: '仰视', expression: '表情', wide: '全景', geometry: '空间布局', hero: '主视图', multi_view: '多视图' } as Record<string, string>)[view] ?? view}</label>)}</div>
    <textarea aria-label="资产画面问题" maxLength={2000} value={notes} onChange={event => setNotes(event.target.value)} placeholder="噪点、结构、风格或视角问题" className="w-full rounded border border-white/15 bg-black/20 p-2" />
    {error && <p role="alert" className="text-red-300">{error}</p>}
    <div className="mt-2 flex flex-wrap gap-3"><button type="button" disabled={!loaded} onClick={() => save('passed')} className="flex items-center gap-1 disabled:opacity-40"><Check size={14} />确认画面</button><button type="button" disabled={!loaded} onClick={() => save('blocked')} className="flex items-center gap-1 disabled:opacity-40"><X size={14} />退回重做</button><button type="button" onClick={() => asset.generatedNodeId && focusDerivedNodes({ nodeIds: [asset.generatedNodeId] })} className="flex items-center gap-1"><RefreshCw size={14} />定位资产节点</button></div>
  </section>;
}
