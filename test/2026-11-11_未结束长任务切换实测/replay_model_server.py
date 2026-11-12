"""本地 OpenAI 兼容回放服务：按原对话记录逐轮流式返回思考、正文和工具调用。

replay-main：每次请求返回下一轮记录；记录用完后保持“仍在思考”的流，任务不结束。
其他模型（思考摘要、标题等辅助请求）：返回一段短文本。
用法：python3 -I replay_model_server.py <replay_script.json> <port> <log.jsonl>
环境变量：REPLAY_CHUNK_CHARS（默认 24）、REPLAY_CHUNK_DELAY（秒，默认 0.03）
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SCRIPT = json.load(open(sys.argv[1], encoding="utf-8"))
PORT = int(sys.argv[2])
LOG = open(sys.argv[3], "a", encoding="utf-8")
CHUNK = int(os.environ.get("REPLAY_CHUNK_CHARS", "24"))
DELAY = float(os.environ.get("REPLAY_CHUNK_DELAY", "0.03"))
TURNS = SCRIPT["turns"]
# 默认不回放最后一轮（原记录的纯文本收尾），使任务停在“工作尚未结束”。
HOLD = {"at": int(os.environ.get("REPLAY_HOLD_AT", str(len(TURNS) - 1)))}
STATE = {"next": 0}
LOCK = threading.Lock()


def log(**row):
    row["t"] = time.time()
    with LOCK:
        LOG.write(json.dumps(row, ensure_ascii=False) + "\n")
        LOG.flush()


def pieces(text):
    return [text[i:i + CHUNK] for i in range(0, len(text), CHUNK)]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            body = json.dumps({"data": [{"id": "replay-main"}, {"id": "replay-aux"}]}).encode()
        elif self.path.startswith("/control/limit"):
            HOLD["at"] = min(int(self.path.split("n=")[1]), len(TURNS) - 1)
            body = json.dumps({"limit": HOLD["at"]}).encode()
        elif self.path.startswith("/control/state"):
            body = json.dumps({"next": STATE["next"], "turns": len(TURNS), "limit": HOLD["at"]}).encode()
        else:
            self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers(); return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _sse(self, obj):
        data = b"data: " + json.dumps(obj, ensure_ascii=False).encode() + b"\n\n"
        self.wfile.write(b"%x\r\n" % len(data) + data + b"\r\n")
        self.wfile.flush()

    def _end(self):
        self._sse_raw(b"data: [DONE]\n\n")
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def _sse_raw(self, data):
        self.wfile.write(b"%x\r\n" % len(data) + data + b"\r\n")

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        request = json.loads(self.rfile.read(length) or b"{}")
        model = request.get("model")
        stream = bool(request.get("stream"))
        if model == "replay-main":
            with LOCK:
                index = STATE["next"]
                STATE["next"] += 1
            log(kind="main", turn=index, request_messages=len(request.get("messages") or []),
                request_bytes=length)
            turn = TURNS[index] if index < HOLD["at"] else None
        else:
            log(kind="aux", model=model, request_bytes=length, stream=stream)
            turn = {"reasoning": "", "content": "**摘要**\n已完成当前步骤的分析。", "tool_calls": []}
        if not stream:
            message = {"role": "assistant", "content": turn["content"] if turn else ""}
            body = json.dumps({"id": "replay", "object": "chat.completion", "model": model,
                               "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
                               "usage": {"prompt_tokens": 1000, "completion_tokens": 20, "total_tokens": 1020}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        base = {"id": f"replay-{time.time_ns()}", "object": "chat.completion.chunk", "model": model}
        try:
            while turn is None and model == "replay-main":
                # 到达检查点：保持流打开（任务处于“等待模型”的运行态），检查点抬高后继续这一轮。
                self._sse({**base, "choices": [{"index": 0, "delta": {}}]})
                time.sleep(0.3)
                if index < HOLD["at"]:
                    turn = TURNS[index]
            for text in pieces(turn["reasoning"]):
                self._sse({**base, "choices": [{"index": 0, "delta": {"reasoning_content": text}}]})
                time.sleep(DELAY)
            for text in pieces(turn["content"]):
                self._sse({**base, "choices": [{"index": 0, "delta": {"content": text}}]})
                time.sleep(DELAY)
            for i, call in enumerate(turn["tool_calls"]):
                args = call["arguments"]
                self._sse({**base, "choices": [{"index": 0, "delta": {"tool_calls": [{
                    "index": i, "id": call["id"], "type": "function",
                    "function": {"name": call["name"], "arguments": ""}}]}}]})
                for text in pieces(args):
                    self._sse({**base, "choices": [{"index": 0, "delta": {"tool_calls": [{
                        "index": i, "function": {"arguments": text}}]}}]})
                    time.sleep(DELAY / 3)
            finish = "tool_calls" if turn["tool_calls"] else "stop"
            self._sse({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]})
            self._sse({**base, "choices": [], "usage": {"prompt_tokens": 60000, "completion_tokens": 500,
                                                       "total_tokens": 60500}})
            self._end()
        except (BrokenPipeError, ConnectionResetError):
            log(kind="client_closed", model=model)


ThreadingHTTPServer.daemon_threads = True
print(f"replay server on {PORT}, turns={len(TURNS)}", flush=True)
ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
