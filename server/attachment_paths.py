"""Shared attachment path notices for ordinary inputs and runtime guidance."""
from typing import Any, List, Optional


def extract_media_path(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("path") or "").strip()
    return str(item or "").strip()


def build_media_paths_message(
    images: Optional[List[Any]], videos: Optional[List[Any]], *, media_paths_only: bool = False,
) -> Optional[str]:
    paths = []
    for item in (images or []) + (videos or []):
        path = extract_media_path(item)
        if path and path not in paths:
            paths.append(path)
    if not paths:
        return None
    if media_paths_only:
        guidance = (
            "本次引导仅提供媒体文件路径。如需查看图片内容，请调用 view_image 打开对应文件。"
            "如需查看视频内容，请使用当前可用的媒体处理工具。"
        )
    else:
        guidance = (
            "这些媒体已随本次消息一并发送，若你已能看到图片/视频内容，就不要再调用 view_image / view_video 重新打开；"
            "下面的路径只用于后续裁切、局部放大或引用。"
        )
    return "\n".join([
        "[系统通知|media_paths]",
        guidance,
        "本次消息附带的媒体文件路径：",
        *(f"- `{path}`" for path in paths),
    ])


def normalize_attached_files(files: Optional[List[Any]]) -> List[str]:
    result = []
    for item in files or []:
        if isinstance(item, str) and item.strip() and item.strip() not in result:
            result.append(item.strip())
        if len(result) >= 9:
            break
    return result


def build_file_paths_message(files: Optional[List[Any]]) -> Optional[str]:
    paths = normalize_attached_files(files)
    if not paths:
        return None
    return "\n".join(["[系统通知|file_paths]", "本次消息附带的文件路径：",
                      *(f"- `{path}`" for path in paths)])
