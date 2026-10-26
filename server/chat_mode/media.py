"""Local media uploads, independent of any workspace upload machinery."""
from io import BytesIO
from flask import jsonify, request, send_file
from server.auth_helpers import api_login_required
from utils.media_store import MediaStore
from modules.chat_mode.media import MAX_VIDEO, inspect_media, resolve_refs
from . import chat_mode_bp
from .context import resources


@chat_mode_bp.post("/media")
@api_login_required
def upload_media():
    _, scope, _ = resources()
    upload = request.files.get("media")
    if upload is None:
        raise ValueError("Select an image or video")
    payload = upload.stream.read(MAX_VIDEO + 1)
    kind, mime = inspect_media(payload)
    reference = MediaStore(scope.root).store_bytes(payload, kind=kind, mime_type=mime)
    from modules.chat_mode.media_gc import collect
    from modules.chat_mode.storage import ChatStorage
    try:
        collect(ChatStorage(scope.root))
    except (OSError, ValueError, KeyError) as exc:
        from server.utils_common import debug_log
        debug_log(f"[Chat] Media collection deferred: {exc}")
    return jsonify(success=True, data={key: reference[key] for key in
                                     ("media_id", "kind", "mime_type", "size", "sha256")})


@chat_mode_bp.get("/media/<media_id>")
@api_login_required
def read_media(media_id):
    _, scope, _ = resources()
    reference = resolve_refs(scope.root, [media_id])[0]
    payload = MediaStore(scope.root).load_bytes_by_media_id(media_id)
    if payload is None:
        raise FileNotFoundError("Chat media unavailable")
    response = send_file(BytesIO(payload), mimetype=reference["mime_type"],
                         download_name=media_id, max_age=0)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
