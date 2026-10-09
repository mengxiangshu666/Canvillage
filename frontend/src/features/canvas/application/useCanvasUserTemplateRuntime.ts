// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useCallback, useMemo } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';

import {
  deleteCanvasUserTemplate,
  getCanvasUserTemplate,
  type CanvasUserTemplateSummary,
} from '@/api/canvas';
import { addCanvasUserTemplateToCanvas } from '@/features/canvas/application/canvasUserTemplateActions';
import { queryKeys } from '@/lib/query-keys';
import { useCanvasStore } from '@/stores/canvasStore';

import { useCanvasUserTemplates } from './useCanvasUserTemplates';

export interface CanvasUserTemplatePanelProps {
  userTemplates?: readonly CanvasUserTemplateSummary[];
  userTemplatesLoading?: boolean;
  onUseUserTemplate?: (templateId: string) => void;
  onDeleteUserTemplate?: (templateId: string) => void;
}

export function useCanvasUserTemplateRuntime({
  projectId,
  spawnAtViewportCenter,
  scheduleCanvasPersist,
}: {
  projectId?: string;
  spawnAtViewportCenter: () => { x: number; y: number };
  scheduleCanvasPersist: (delayMs?: number) => void;
}) {
  const queryClient = useQueryClient();
  const query = useCanvasUserTemplates(projectId);
  const setSelectedNode = useCanvasStore((state) => state.setSelectedNode);

  const onUseUserTemplate = useCallback(
    async (templateId: string) => {
      if (!projectId) {
        toast.error('当前项目不可用，暂时无法插入模板');
        return;
      }
      try {
        const template = await getCanvasUserTemplate(projectId, templateId);
        const created = addCanvasUserTemplateToCanvas(template, spawnAtViewportCenter());
        if (!created) {
          toast.error('模板结构已失效，无法插入');
          return;
        }
        setSelectedNode(created.nodeIds[created.nodeIds.length - 1] ?? null);
        scheduleCanvasPersist(0);
        toast.success(`已插入「${created.title}」`);
      } catch (caught) {
        toast.error(caught instanceof Error ? caught.message : '模板插入失败，请重试');
      }
    },
    [
      projectId,
      scheduleCanvasPersist,
      setSelectedNode,
      spawnAtViewportCenter,
    ],
  );

  const onDeleteUserTemplate = useCallback(
    async (templateId: string) => {
      if (!projectId) return;
      try {
        await deleteCanvasUserTemplate(projectId, templateId);
        await queryClient.invalidateQueries({
          queryKey: queryKeys.canvasUserTemplates(projectId),
        });
        toast.success('模板已删除');
      } catch (caught) {
        toast.error(caught instanceof Error ? caught.message : '模板删除失败，请重试');
      }
    },
    [projectId, queryClient],
  );

  const panelProps = useMemo<CanvasUserTemplatePanelProps>(
    () => ({
      userTemplates: query.data,
      userTemplatesLoading: query.isLoading,
      onUseUserTemplate: (templateId) => {
        void onUseUserTemplate(templateId);
      },
      onDeleteUserTemplate: (templateId) => {
        void onDeleteUserTemplate(templateId);
      },
    }),
    [onDeleteUserTemplate, onUseUserTemplate, query.data, query.isLoading],
  );

  return { panelProps };
}
