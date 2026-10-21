from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram


class Metrics:
    """每个进程容器拥有独立注册表，标签只使用有限的路由、方法和状态。"""

    def __init__(self):
        self.registry = CollectorRegistry()

        # HTTP 指标
        self.requests = Counter(
            "rag_http_requests_total",
            "HTTP 请求",
            ["method", "route", "status"],
            registry=self.registry,
        )
        self.latency = Histogram(
            "rag_http_request_duration_seconds",
            "HTTP 延迟",
            ["method", "route"],
            registry=self.registry,
        )
        self.jobs = Counter(
            "rag_index_job_attempts_total",
            "索引任务尝试",
            ["type", "status"],
            registry=self.registry,
        )
        self.queue = Gauge("rag_index_jobs", "数据库任务数", ["status"], registry=self.registry)

        # LLM 指标
        self.llm_requests = Counter(
            "rag_llm_requests_total",
            "LLM 调用次数",
            ["model", "role", "status"],
            registry=self.registry,
        )
        self.llm_duration = Histogram(
            "rag_llm_request_duration_seconds",
            "LLM 调用延迟",
            ["model"],
            registry=self.registry,
        )
        self.llm_tokens = Counter(
            "rag_llm_tokens_total",
            "LLM token 消耗",
            ["model", "kind"],
            registry=self.registry,
        )
        self.llm_cost = Counter(
            "rag_llm_cost_total",
            "LLM 调用成本（美元）",
            ["model"],
            registry=self.registry,
        )

        # Embedding 和 Rerank 指标
        self.embedding_requests = Counter(
            "rag_embedding_requests_total",
            "Embedding 调用次数",
            ["model", "status"],
            registry=self.registry,
        )
        self.embedding_duration = Histogram(
            "rag_embedding_duration_seconds",
            "Embedding 调用延迟",
            ["model"],
            registry=self.registry,
        )
        self.rerank_requests = Counter(
            "rag_rerank_requests_total",
            "Rerank 调用次数",
            ["model", "status"],
            registry=self.registry,
        )
        self.rerank_duration = Histogram(
            "rag_rerank_duration_seconds",
            "Rerank 调用延迟",
            ["model"],
            registry=self.registry,
        )

        # Research 指标
        self.research_runs = Counter(
            "rag_research_runs_total",
            "Research 运行次数",
            ["stop_reason", "degraded"],
            registry=self.registry,
        )
        self.research_stage_duration = Histogram(
            "rag_research_stage_duration_seconds",
            "Research 阶段耗时",
            ["stage"],
            registry=self.registry,
        )

        # 检索失败指标
        self.retrieval_failures = Counter(
            "rag_retrieval_branch_failures_total",
            "检索分支失败次数",
            ["mode"],
            registry=self.registry,
        )

        # 降级指标
        self.degradations = Counter(
            "rag_degradations_total",
            "降级事件次数",
            ["component"],
            registry=self.registry,
        )

        # 限流指标
        self.ratelimit_rejections = Counter(
            "rag_ratelimit_rejections_total",
            "限流拒绝次数",
            ["kind"],
            registry=self.registry,
        )

        # Worker 指标
        self.lease_renew_failures = Counter(
            "rag_index_lease_renew_failures_total",
            "租约续期失败次数",
            registry=self.registry,
        )
        self.worker_heartbeat = Gauge(
            "rag_worker_heartbeat_timestamp",
            "Worker 心跳时间戳",
            ["worker_id"],
            registry=self.registry,
        )
