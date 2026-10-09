// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useState } from 'react';
import { BookmarkPlus, Loader2, X } from 'lucide-react';
import { useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';

import { createCanvasUserTemplate } from '@/api/canvas';
import { queryKeys } from '@/lib/query-keys';

import type { CanvasUserTemplateCreatePayload } from '../application/userCanvasTemplates';

export function SaveCanvasTemplateDialog({
  projectId,
  payload,
  onClose,
}: {
  projectId?: string;
  payload: CanvasUserTemplateCreatePayload | null;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!payload) return;
    setTitle('');
    setDescription('');
    setError(null);
    setSaving(false);
  }, [payload]);

  if (!payload) {
    return null;
  }

  const submit = async () => {
    if (!projectId || saving) return;
    const normalizedTitle = title.trim();
    if (!normalizedTitle) {
      setError('请填写模板名称');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const created = await createCanvasUserTemplate(projectId, {
        title: normalizedTitle,
        description: description.trim(),
        nodes: payload.nodes,
        edges: payload.edges,
      });
      await queryClient.invalidateQueries({
        queryKey: queryKeys.canvasUserTemplates(projectId),
      });
      toast.success(`已保存「${created.title}」`);
      onClose();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '模板保存失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-[12000] flex items-center justify-center bg-black/55 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label="保存画布模板"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !saving) onClose();
      }}
    >
      <div className="w-full max-w-[420px] overflow-hidden rounded-2xl border border-white/10 bg-[#242426] text-text-dark shadow-[0_24px_80px_rgba(0,0,0,0.48)]">
        <div className="flex items-start justify-between gap-4 border-b border-white/[0.07] px-4 py-3">
          <div className="flex items-center gap-2.5">
            <span className="flex size-8 items-center justify-center rounded-lg border border-white/[0.09] bg-white/[0.045]">
              <BookmarkPlus className="size-4 text-white/72" />
            </span>
            <div>
              <div className="text-sm font-semibold text-white/92">保存为我的模板</div>
              <div className="mt-0.5 text-[11px] text-white/42">
                {payload.nodes.length} 个节点 · {payload.edges.length} 条连线
              </div>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={saving}
            className="rounded-md p-1 text-white/45 transition-colors hover:bg-white/[0.08] hover:text-white/80 disabled:opacity-40"
            aria-label="关闭"
          >
            <X className="size-4" />
          </button>
        </div>

        <div className="space-y-3 px-4 py-4">
          <label className="block">
            <span className="mb-1.5 block text-[11px] text-white/55">模板名称</span>
            <input
              autoFocus
              value={title}
              maxLength={60}
              onChange={(event) => setTitle(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') void submit();
                if (event.key === 'Escape') onClose();
              }}
              className="h-9 w-full rounded-lg border border-white/[0.10] bg-white/[0.035] px-3 text-[13px] text-white/90 outline-none transition-colors placeholder:text-white/25 focus:border-white/[0.22] focus:bg-white/[0.055]"
              placeholder="例如：雨夜追逐三镜"
            />
          </label>
          <label className="block">
            <span className="mb-1.5 block text-[11px] text-white/55">说明</span>
            <textarea
              value={description}
              maxLength={240}
              rows={3}
              onChange={(event) => setDescription(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Escape') onClose();
              }}
              className="w-full resize-none rounded-lg border border-white/[0.10] bg-white/[0.035] px-3 py-2 text-[13px] leading-5 text-white/90 outline-none transition-colors placeholder:text-white/25 focus:border-white/[0.22] focus:bg-white/[0.055]"
              placeholder="写清适用场景，方便以后找到"
            />
          </label>
          {error ? (
            <div className="rounded-lg border border-rose-400/20 bg-rose-400/[0.07] px-3 py-2 text-xs text-rose-100/85">
              {error}
            </div>
          ) : null}
        </div>

        <div className="flex justify-end gap-2 border-t border-white/[0.07] px-4 py-3">
          <button
            type="button"
            onClick={onClose}
            disabled={saving}
            className="h-8 rounded-lg px-3 text-xs text-white/60 transition-colors hover:bg-white/[0.075] hover:text-white/85 disabled:opacity-40"
          >
            取消
          </button>
          <button
            type="button"
            onClick={() => void submit()}
            disabled={saving || !projectId}
            className="flex h-8 items-center gap-1.5 rounded-lg bg-white px-3 text-xs font-medium text-black transition-colors hover:bg-white/90 disabled:cursor-not-allowed disabled:opacity-45"
          >
            {saving ? <Loader2 className="size-3.5 animate-spin" /> : <BookmarkPlus className="size-3.5" />}
            保存
          </button>
        </div>
      </div>
    </div>
  );
}
