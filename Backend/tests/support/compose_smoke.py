"""通过真实 API、调度器与 prefork Worker 检查连续两次任务的连接池复用。"""

import sys
import time

import httpx


def main(base_url):
    with httpx.Client(base_url=base_url, timeout=10) as client:
        assert client.get("/health/ready").status_code == 200
        response = client.post("/api/v1/knowledge-bases", json={"name": "compose-smoke"})
        response.raise_for_status()
        kb_id = response.json()["data"]["kb_id"]
        for index in range(2):
            path = f"/api/v1/knowledge-bases/{kb_id}/documents"
            response = (
                client.post(path, json={"title": "文本规则", "content": "签收七天内可以退货。"})
                if index == 0
                else client.post(
                    path,
                    files={"file": ("rules.md", "签收七天内可以退货。".encode(), "text/markdown")},
                )
            )
            response.raise_for_status()
            job_id = response.json()["data"]["job_id"]
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                response = client.get(f"/api/v1/index-jobs/{job_id}")
                response.raise_for_status()
                status = response.json()["data"]["status"]
                if status in {"succeeded", "failed"}:
                    assert status == "succeeded", response.text
                    break
                time.sleep(0.5)
            else:
                raise TimeoutError("索引任务未在 90 秒内完成")
        response = client.post(
            "/api/v1/rag/retrieve",
            json={
                "kb_id": kb_id,
                "user_id": "smoke",
                "query": "退货",
                "profile": "custom",
                "retrieval_options": {"mode": "hybrid"},
                "rerank_options": {"enabled": False},
                "query_options": {"enabled": False},
            },
        )
        response.raise_for_status()
        assert response.json()["data"]["retrieved_chunks"], response.text
        assert client.delete(f"/api/v1/knowledge-bases/{kb_id}").status_code == 204
    print("Compose 冒烟检查通过")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000")
