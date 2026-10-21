import { http } from "@/lib/http";
import type { ApiResponse, UploadDocumentData, UploadDocumentRequest } from "@/types/api";

export async function uploadDocument(payload: UploadDocumentRequest) {
  const response = await http.post<ApiResponse<UploadDocumentData>>(
    `/api/v1/knowledge-bases/${payload.kb_id}/documents`,
    payload,
  );
  if (response.data.code !== 0) {
    throw new Error(response.data.msg);
  }
  return response.data.data;
}

export async function uploadDocumentFile(formData: FormData) {
  const response = await http.post<ApiResponse<UploadDocumentData>>(
    `/api/v1/knowledge-bases/${formData.get("kb_id")}/documents`,
    formData,
  );
  if (response.data.code !== 0) {
    throw new Error(response.data.msg);
  }
  return response.data.data;
}

export async function deleteDocument(documentId: string) {
  await http.delete<void>(`/api/v1/documents/${documentId}`);
}

export async function reindexDocument(documentId: string) {
  const response = await http.post<ApiResponse<UploadDocumentData>>(
    `/api/v1/documents/${documentId}/index-jobs`,
  );
  if (response.data.code !== 0) {
    throw new Error(response.data.msg);
  }
  return response.data.data;
}
