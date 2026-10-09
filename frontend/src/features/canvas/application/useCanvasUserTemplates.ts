// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useQuery } from '@tanstack/react-query';

import { listCanvasUserTemplates } from '@/api/canvas';
import { queryKeys } from '@/lib/query-keys';

export function useCanvasUserTemplates(projectId?: string) {
  const normalizedProjectId = projectId?.trim() ?? '';
  return useQuery({
    queryKey: queryKeys.canvasUserTemplates(normalizedProjectId),
    queryFn: () => listCanvasUserTemplates(normalizedProjectId),
    enabled: Boolean(normalizedProjectId),
    staleTime: 30_000,
  });
}
