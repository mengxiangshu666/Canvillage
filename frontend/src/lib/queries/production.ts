// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  commandProductionRun,
  getProductionControl,
  getProductionOverview,
  startProductionRun,
  uploadProductionNovel,
} from "@/api/production";
import type {
  ProductionControlStart,
  ProductionRunCommand,
} from "@/types/production";

export const productionQueryKeys = {
  overview: (project: string) => ["production", "overview", project] as const,
  control: (project: string) => ["production", "control", project] as const,
};

export function productionControlRefetchInterval(status: unknown): number {
  return status === "running" || status === "pausing" ? 2_000 : 10_000;
}

export function useProductionOverview(project: string) {
  return useQuery({
    queryKey: productionQueryKeys.overview(project),
    queryFn: ({ signal }) => getProductionOverview(project, signal),
    enabled: Boolean(project),
    refetchInterval: 15_000,
    staleTime: 5_000,
  });
}

export function useProductionControl(project: string) {
  return useQuery({
    queryKey: productionQueryKeys.control(project),
    queryFn: ({ signal }) => getProductionControl(project, signal),
    enabled: Boolean(project),
    refetchInterval: (query) =>
      productionControlRefetchInterval(query.state.data?.latest_run?.status),
    staleTime: 1_000,
  });
}

function useInvalidateProduction(project: string) {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({
      queryKey: productionQueryKeys.control(project),
    });
    void queryClient.invalidateQueries({
      queryKey: productionQueryKeys.overview(project),
    });
  };
}

export function useUploadProductionNovel(project: string) {
  const invalidate = useInvalidateProduction(project);
  return useMutation({
    mutationFn: (file: File) => uploadProductionNovel(project, file),
    onSuccess: invalidate,
  });
}

export function useStartProductionRun(project: string) {
  const invalidate = useInvalidateProduction(project);
  return useMutation({
    mutationFn: (payload: ProductionControlStart) =>
      startProductionRun(project, payload),
    onSuccess: invalidate,
  });
}

export function useProductionRunCommand(project: string) {
  const invalidate = useInvalidateProduction(project);
  return useMutation({
    mutationFn: ({
      runId,
      command,
      confirmedPaidMedia = false,
    }: {
      runId: string;
      command: ProductionRunCommand;
      confirmedPaidMedia?: boolean;
    }) => commandProductionRun(project, runId, command, confirmedPaidMedia),
    onSuccess: invalidate,
  });
}
