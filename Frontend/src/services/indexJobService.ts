import { http } from "@/lib/http";
import type { ApiResponse, IndexJobData } from "@/types/api";

export async function fetchIndexJob(jobId: string, signal?: AbortSignal) {
  const response = await http.get<ApiResponse<IndexJobData>>(
    `/api/v1/index-jobs/${encodeURIComponent(jobId)}`, { signal },
  );
  return response.data.data;
}
