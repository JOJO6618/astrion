"""Read-only local fixture. Never calls a model or writes conversation data."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path

LOG = Path('cache/windows-quick-smoke/gateway.jsonl')
LOG.parent.mkdir(parents=True, exist_ok=True)

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        authorized = self.headers.get('Authorization') == 'Bearer quick-smoke-token'
        with LOG.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'route': self.path, 'authorized': authorized}) + '\n')
        if not authorized:
            self.send_response(401)
            payload = {'success': False, 'error': 'unauthorized'}
        else:
            self.send_response(200)
            if self.path == '/api/host/workspaces':
                payload = {'success': True, 'data': {'workspaces': [], 'default_workspace_id': ''}}
            elif self.path == '/api/v1/models':
                payload = {'success': True, 'items': []}
            elif self.path == '/api/personalization':
                payload = {'success': True, 'personalization': {'default_model': '', 'theme': 'classic'}}
            else:
                payload = {'success': False, 'error': 'unsupported fixture route'}
        body = json.dumps(payload).encode()
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass

if __name__ == '__main__':
    ThreadingHTTPServer(('127.0.0.1', 8321), Handler).serve_forever()
