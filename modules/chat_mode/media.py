"""Validate local media and resolve references exclusively in an account store."""
from io import BytesIO
from PIL import Image, UnidentifiedImageError
from utils.media_store import MediaStore

MAX_IMAGE = 25 * 1024 * 1024
MAX_VIDEO = 100 * 1024 * 1024
IMAGE_MIME = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp",
              "GIF": "image/gif", "BMP": "image/bmp"}


def inspect_media(payload):
    if not payload or len(payload) > MAX_VIDEO:
        raise ValueError("Media is empty or exceeds 100 MB")
    try:
        with Image.open(BytesIO(payload)) as image:
            mime = IMAGE_MIME.get(image.format)
            if not mime or image.width * image.height > 100_000_000:
                raise ValueError("Unsupported image format or dimensions")
            image.verify()
        if len(payload) > MAX_IMAGE:
            raise ValueError("Image exceeds 25 MB")
        return "image", mime
    except (UnidentifiedImageError, OSError):
        pass
    # Container signatures, never a client-supplied extension/path. Video
    # decoding is delegated to the same multimodal provider as Agent media.
    if len(payload) >= 16 and payload[4:8] == b"ftyp":
        brand = payload[8:12]
        if brand in {b"avif", b"avis", b"heic", b"heix", b"mif1"}:
            raise ValueError("Unsupported image format")
        return "video", "video/quicktime" if brand == b"qt  " else "video/mp4"
    if payload.startswith(b"\x1aE\xdf\xa3") and b"webm" in payload[:4096]:
        return "video", "video/webm"
    raise ValueError("Only supported images and videos can be sent in Chat")


def resolve_refs(root, values, kind=None):
    if not isinstance(values, list) or len(values) > 20:
        raise ValueError("Invalid media list")
    store = MediaStore(root)
    result = []
    for value in values:
        media_id = value.get("media_id") if isinstance(value, dict) else value
        if not isinstance(media_id, str) or len(media_id) > 100:
            raise ValueError("Expected an uploaded media ID")
        entry = store.get_media_entry(media_id)
        if not entry or entry.get("kind") not in {"image", "video"}:
            raise FileNotFoundError("Chat media unavailable")
        if kind and entry["kind"] != kind:
            raise ValueError("Wrong media kind")
        result.append({key: entry[key] for key in ("media_id", "kind", "mime_type", "size", "sha256")})
    return result
