import { useQueries } from "@tanstack/react-query";
import { fetchIndexJob } from "@/services/indexJobService";

export function useIndexJobs(jobIds: string[]) {
  const ids = [...new Set(jobIds)];
  return useQueries({
    queries: ids.map((jobId) => ({
      queryKey: ["indexJob", jobId],
      queryFn: ({ signal }: { signal: AbortSignal }) => fetchIndexJob(jobId, signal),
      refetchInterval: (query: { state: { data?: { status: string }; error: unknown } }) => {
        const status = query.state.data?.status;
        return status === "succeeded" || status === "failed" || query.state.error ? false : 2000;
      },
      retry: 2,
      staleTime: 1000,
    })),
  });
}
