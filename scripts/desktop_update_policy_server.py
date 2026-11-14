"""Public, read-only release policy service; deployed separately from the desktop backend."""
from __future__ import annotations

import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys

from flask import Flask, jsonify, request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules.desktop_update_policy import evaluate_policy


def create_app(downloads: Path, rules_file: Path, log_file: Path) -> Flask:
    app = Flask('astrion-release-policy')
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8')
    app.logger.handlers = [handler]
    app.logger.setLevel(logging.INFO)

    @app.after_request
    def no_cache(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @app.get('/downloads/update-policy')
    def policy():
        client = {
            'version': request.args.get('current_version', ''),
            'build_id': request.args.get('build_id', ''),
            'platform': request.args.get('platform', ''),
            'arch': request.args.get('arch', ''),
        }
        if not all(client.values()):
            return jsonify(error='missing_client_identity'), 400
        if any(len(value) > (16 if key in ('platform', 'arch') else 128)
               for key, value in client.items()):
            return jsonify(error='invalid_client_identity'), 400
        if client['platform'] not in ('win32', 'darwin') or client['arch'] not in ('x64', 'arm64'):
            return jsonify(error='unsupported_client'), 400
        try:
            document = json.loads(rules_file.read_text(encoding='utf-8-sig'))
            manifest_name = 'latest-mac.json' if client['platform'] == 'darwin' else 'latest-windows-x86_64.json'
            manifest = json.loads((downloads / manifest_name).read_text(encoding='utf-8-sig'))
            return jsonify(evaluate_policy(document, client, str(manifest.get('version') or '')))
        except (OSError, ValueError, TypeError, KeyError):
            # Do not turn deployment errors into a successful allow policy: an
            # existing valid required policy must remain effective offline.
            app.logger.exception('Release policy could not be evaluated')
            return jsonify(error='policy_unavailable'), 503

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--downloads', type=Path, required=True)
    parser.add_argument('--rules', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8098)
    parser.add_argument('--log', type=Path, default=Path.home() / '.astrion/release-policy/logs/policy.log')
    args = parser.parse_args()
    app = create_app(args.downloads.resolve(), args.rules.resolve(), args.log.expanduser())
    app.run(host='127.0.0.1', port=args.port, threaded=True, use_reloader=False)


if __name__ == '__main__':
    main()
