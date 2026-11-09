"""Disposable stdlib backend: diagnostic protocol, not production transport."""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from secrets import compare_digest

TOKEN = os.environ["BROWSER_LAB_TOKEN"]
STATE = {"sent": False, "result": None, "polls": 0}
LOCK = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def reply(self, code, payload, kind="application/json"):
        data = payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def authorized(self):
        value = self.headers.get("Authorization", "")
        return compare_digest(value, "Bearer " + TOKEN)

    def do_GET(self):
        if self.path == "/health":
            return self.reply(200, {"ok": True})
        if self.path == "/trusted":
            return self.reply(200, '<title>Docker lab trusted placeholder</title>', 'text/html')
        if self.path in ("/app", "/fixture"):
            # A normal page, deliberately not frameable. It is loaded natively.
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(b'<title>Container developer app</title><label for="msg">Container message</label><input id="msg"><output id="out"></output>')
            return
        if not self.authorized():
            return self.reply(401, {"error": "unauthorized"})
        if self.path == "/next":
            with LOCK:
                STATE["polls"] += 1
                if STATE["sent"]:
                    return self.reply(200, {"command": None})
                STATE["sent"] = True
            return self.reply(200, {"command": {"id": "lab-command-1", "operation": "fill", "selector": "#msg", "value": "Docker to local native view 中文"}})
        if self.path == "/result":
            with LOCK:
                return self.reply(200, STATE)
        return self.reply(404, {"error": "not_found"})

    def do_POST(self):
        if not self.authorized():
            return self.reply(401, {"error": "unauthorized"})
        if self.path != "/result":
            return self.reply(404, {"error": "not_found"})
        data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        with LOCK:
            STATE["result"] = data
        return self.reply(200, {"ok": True})


ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
