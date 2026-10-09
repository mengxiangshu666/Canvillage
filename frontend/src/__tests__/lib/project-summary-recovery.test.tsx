import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { expect, it, vi } from "vitest";
import { useAllProjectSummaries } from "@/lib/queries/projects";

const { read } = vi.hoisted(() => ({ read: vi.fn() }));
vi.mock("@/lib/api", () => ({ api: { get: () => ({ json: read }) } }));

it("exposes a loading failure and recovers through retry rather than an empty result", async () => {
  read.mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce({ data: [] });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  const { result, unmount } = renderHook(useAllProjectSummaries, { wrapper });
  await waitFor(() => expect(result.current.isError).toBe(true));
  expect(result.current.data).toBeUndefined();
  await act(async () => { await result.current.refetch(); });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data).toEqual([]);
  unmount();
  client.clear();
});
