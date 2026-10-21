"""仅供集成与 Compose 测试的本地 OpenAI 协议桩。"""

import hashlib
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        if self.path.endswith("/embeddings"):
            texts = payload["input"]
            if isinstance(texts, str):
                texts = [texts]
            if any("__slow__" in text for text in texts):
                time.sleep(3)
            dimensions = payload.get("dimensions", 1536)
            body = {
                "data": [
                    {
                        "index": i,
                        "embedding": [
                            1.0
                            if j
                            == int.from_bytes(hashlib.sha256(value.encode()).digest()[:2], "big")
                            % dimensions
                            else 0.01
                            for j in range(dimensions)
                        ],
                    }
                    for i, value in enumerate(texts)
                ]
            }
        elif self.path.endswith("/reranks"):
            body = {
                "results": [
                    {"index": i, "relevance_score": 1.0 / (i + 1)}
                    for i, _ in enumerate(payload.get("documents", []))
                ]
            }
        else:
            body = {
                "choices": [{"message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            # 中断 Worker 的恢复测试会主动断开尚未返回的模型请求。
            pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 9000), Handler).serve_forever()
