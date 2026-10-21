import type { components } from "./openapi";

type Schema = components["schemas"];
// FastAPI 的响应会序列化默认字段；生成契约中的可选字段在响应视图中始终存在。
type Output<T> = T extends (infer Item)[]
  ? Output<Item>[]
  : T extends object
    ? { [Key in keyof T]-?: Output<T[Key]> }
    : T;

type ResponseSchema<Name extends keyof Schema> = Output<Schema[Name]>;
export type ApiErrorResponse = Schema["ApiErrorResponse"];
export type ApiResponse<T> = Omit<ResponseSchema<"ApiResponse_KnowledgeBaseData_">, "data"> & { data: T };

export type CreateKnowledgeBaseRequest = Schema["CreateKnowledgeBaseRequest"];
export type UpdateKnowledgeBaseRequest = Schema["UpdateKnowledgeBaseRequest"];
export type KnowledgeBaseData = ResponseSchema<"KnowledgeBaseData">;
export type KnowledgeBaseDetailData = ResponseSchema<"KnowledgeBaseDetailData">;
export type DocumentTreeItem = ResponseSchema<"DocumentTreeItem">;
export type IndexVersionTreeItem = ResponseSchema<"IndexVersionTreeItem">;
export type KnowledgeBaseTreeItem = ResponseSchema<"KnowledgeBaseTreeItem">;
export type TenantTreeItem = ResponseSchema<"TenantTreeItem">;
export type AuthFeatures = ResponseSchema<"AuthFeatures">;
export type AuthLimits = ResponseSchema<"AuthLimits">;
export type AuthUsage = ResponseSchema<"AuthUsage">;
export type AuthMeData = ResponseSchema<"AuthMeData">;
export type UploadDocumentData = ResponseSchema<"UploadDocumentData">;
export type IndexJobData = ResponseSchema<"IndexJobData">;

// kb_id 是前端路由参数，文本与文件共用上传入口。
export interface UploadDocumentRequest {
  kb_id: string;
  title: string;
  content: string;
}

export interface UploadFileResult {
  fileName: string;
  relativePath: string;
  status: "submitted" | "success" | "failed";
  documentId?: string;
  jobId?: string;
  chunkCount?: number;
  message?: string;
}

export type RetrievalOptions = Schema["RetrievalOptions"];
export type RetrievalMode = NonNullable<RetrievalOptions["mode"]>;
export type RerankOptions = Schema["RerankOptions"];
export type EvidenceOptions = Schema["EvidenceOptions"];
export type QueryOptions = Schema["QueryOptions"];
export type RetrieveRequest = Schema["RetrieveRequest"];
export type RetrieveProfile = NonNullable<RetrieveRequest["profile"]>;
export type RetrievedChunkData = ResponseSchema<"RetrievedChunkData">;
export type RetrievedChunkRelation = Schema["RetrievedChunkContextSource"]["relation"];
export type RetrievedChunkContext = ResponseSchema<"RetrievedChunkContext">;
export type RetrieveRerankMetadata = ResponseSchema<"RetrieveRerankMetadata">;
export type QueryProcessingMetadata = ResponseSchema<"QueryProcessingMetadata">;
export type TenantPolicyMetadata = ResponseSchema<"TenantPolicyMetadata">;

// Provider 检索统计在后端是扩展 JSON；此处声明管理界面使用的可选统计字段。
export interface RetrieveRetrievalMetadata {
  mode?: string;
  fusion?: string | null;
  fusion_scope?: string | null;
  rrf_k?: number | null;
  vector_store?: string;
  keyword_search?: string | null;
  vector_top_k?: number | null;
  bm25_top_k?: number | null;
  vector_count?: number;
  bm25_count?: number;
  fused_count?: number;
  degraded?: boolean;
  degraded_reason?: string | null;
  cost_ms?: number;
  multi_kb?: boolean;
  kb_count?: number;
  kb_ids?: string[];
  index_version?: string;
  index_versions?: Record<string, string>;
  embedding_request_count?: number;
  vector_request_count?: number;
  bm25_request_count?: number;
  source_distribution?: Record<string, number>;
  vector_latency_ms?: number;
  embedding_latency_ms?: number;
  vector_store_latency_ms?: number;
  bm25_latency_ms?: number;
  hybrid_latency_ms?: number;
}

export type RetrieveMetadata = Omit<ResponseSchema<"RetrieveMetadata">, "retrieval" | "candidate_filter"> & {
  retrieval: RetrieveRetrievalMetadata | null;
  candidate_filter: {
    candidate_count_before_filter?: number;
    candidate_count_after_filter?: number;
    filtered_count?: number;
    filtered_reasons?: Record<string, number>;
  } | null;
};
export type RetrieveData = Omit<ResponseSchema<"RetrieveData">, "metadata"> & { metadata: RetrieveMetadata };
export type EvidenceRole = Schema["EvidenceGroupItem"]["role"];
export type EvidenceStatus = Schema["EvidencePack"]["status"];
export type EvidenceItem = ResponseSchema<"EvidenceItem">;
export type EvidenceGroup = ResponseSchema<"EvidenceGroup">;
export type EvidencePack = ResponseSchema<"EvidencePack">;
export type RetrieveEvidenceMetadata = ResponseSchema<"RetrieveEvidenceMetadata">;
export type FeedbackRequest = Schema["FeedbackRequest"];
export type FeedbackData = ResponseSchema<"FeedbackData">;

export type RetrieveMode = "single" | "research";
export type ResearchRequest = Schema["ResearchRequest"];
export type ResearchAspect = ResponseSchema<"ResearchAspect">;
export type PlannedQuery = ResponseSchema<"PlannedQuery">;
export type ResearchPlanData = ResponseSchema<"ResearchPlanData">;
export type ResearchTaskMetadata = ResponseSchema<"ResearchTaskMetadata">;
export type ResearchRoundMetadata = ResponseSchema<"ResearchRoundMetadata">;
export type ResearchStopReason = Schema["ResearchMetadata"]["stop_reason"];
export type ResearchStageUsage = ResponseSchema<"ResearchStageUsage">;
export type ResearchMetadata = ResponseSchema<"ResearchMetadata">;
export type ResearchData = ResponseSchema<"ResearchData">;
