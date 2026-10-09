// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { jsonWithBackendError } from "@/lib/api-errors";
import { p } from "@/lib/api-path";
import { queryKeys } from "@/lib/query-keys";
import type { ErrorResponse, OkResponse } from "@/types/api";
import type {
  StoryLabArtifact,
  StoryLabConfig,
  StoryLabExportReceipt,
  StoryLabGenerationStage,
  StoryLabPublishReceipt,
  StoryLabState,
  StoryLabTaskReceipt,
} from "@/types/story-lab";

interface StoryLabWireState extends Omit<StoryLabState, "stages"> {
  stages?: StoryLabState["stages"];
  artifacts?: StoryLabState["stages"];
}

function normalizeState(response: OkResponse<StoryLabWireState>): OkResponse<StoryLabState> {
  return {
    ...response,
    data: {
      ...response.data,
      stages: response.data.stages ?? response.data.artifacts ?? {},
    },
  };
}

async function expectOk<T>(request: Promise<Response>): Promise<OkResponse<T>> {
  const response = await jsonWithBackendError<OkResponse<T> | ErrorResponse>(
    request,
  );
  if (!response.ok) throw new Error(response.error);
  return response;
}

export function useStoryLab(project: string) {
  return useQuery({
    queryKey: queryKeys.storyLab(project),
    queryFn: async ({ signal }) =>
      normalizeState(
        await api
          .get(p`api/v1/projects/${project}/story-lab`, { signal })
          .json<OkResponse<StoryLabWireState>>(),
      ),
    enabled: Boolean(project),
    staleTime: 10_000,
  });
}

export function useSaveStoryLabConfig(project: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (config: StoryLabConfig) =>
      normalizeState(
        await expectOk<StoryLabWireState>(
          api.put(p`api/v1/projects/${project}/story-lab/config`, {
            json: config,
            throwHttpErrors: false,
          }),
        ),
      ),
    onSuccess: (response) => {
      queryClient.setQueryData(queryKeys.storyLab(project), response);
    },
  });
}

export function useGenerateStoryLabStage(project: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      stage,
      instructions,
    }: {
      stage: StoryLabGenerationStage;
      instructions?: string;
    }) =>
      expectOk<StoryLabTaskReceipt>(
        api.post(p`api/v1/projects/${project}/story-lab/generate`, {
          json: { stage, ...(instructions?.trim() ? { instructions } : {}) },
          throwHttpErrors: false,
        }),
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.tasks(project) });
      queryClient.invalidateQueries({ queryKey: queryKeys.storyLab(project) });
    },
  });
}

export function useStoryLabResult(
  project: string,
  stage: StoryLabGenerationStage,
  enabled = true,
) {
  return useQuery({
    queryKey: queryKeys.storyLabResult(project, stage),
    queryFn: ({ signal }) =>
      api
        .get(p`api/v1/projects/${project}/story-lab/stages/${stage}`, { signal })
        .json<OkResponse<StoryLabArtifact | null>>(),
    enabled: Boolean(project) && enabled,
  });
}

export function useSaveStoryLabResult(project: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      stage,
      result,
      editorNote,
    }: {
      stage: StoryLabGenerationStage;
      result: Record<string, unknown>;
      editorNote?: string;
    }) =>
      expectOk<StoryLabArtifact>(
        api.put(p`api/v1/projects/${project}/story-lab/stages/${stage}`, {
          json: {
            result,
            ...(editorNote?.trim() ? { note: editorNote } : {}),
          },
          throwHttpErrors: false,
        }),
      ),
    onSuccess: (response, variables) => {
      queryClient.setQueryData(
        queryKeys.storyLabResult(project, variables.stage),
        response,
      );
      queryClient.invalidateQueries({ queryKey: queryKeys.storyLab(project) });
    },
  });
}

export function useExportStoryLab(project: string) {
  return useMutation({
    mutationFn: (filename?: string) =>
      expectOk<StoryLabExportReceipt>(
        api.post(p`api/v1/projects/${project}/story-lab/export`, {
          json: filename?.trim() ? { filename } : {},
          throwHttpErrors: false,
        }),
      ),
  });
}

export function usePublishStoryLab(project: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ filename, rebuild = true }: { filename?: string; rebuild?: boolean }) =>
      expectOk<StoryLabPublishReceipt>(
        api.post(p`api/v1/projects/${project}/story-lab/publish`, {
          json: { ...(filename?.trim() ? { filename } : {}), rebuild },
          throwHttpErrors: false,
        }),
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.tasks(project) });
    },
  });
}
