"""Chat drafts reuse the shared ordered, atomic draft writer."""
from flask import jsonify, request
from modules.composer_drafts import read_draft, save_draft
from modules.chat_mode.media import resolve_refs
from server.auth_helpers import api_login_required
from . import chat_mode_bp
from .context import resources, body


@chat_mode_bp.route("/drafts/<key>", methods=["GET", "PUT"])
@api_login_required
def draft(key):
    _, scope, storage = resources()
    if key != "new":
        storage.read(key)
    path = scope.root / "drafts" / f"{key}.json"
    if request.method == "PUT":
        payload = body()
        text = payload.get("text", "")
        if not isinstance(text, str) or len(text) > 1_000_000:
            raise ValueError("Invalid Chat draft")
        media = resolve_refs(scope.root, payload.get("media", []))
        client = payload.get("client_id")
        sequence = payload.get("sequence")
        revision = payload.get("base_revision")
        if not isinstance(client, str) or not 1 <= len(client) <= 128:
            raise ValueError("Invalid draft writer")
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
            raise ValueError("Invalid draft sequence")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
            raise ValueError("Invalid draft revision")
        save_draft(path, {"text": text, "media": media}, client_id=client,
                   sequence=sequence, base_revision=revision)
    return jsonify(success=True, data=read_draft(path))
