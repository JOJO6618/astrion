"""Shared attachment path notices for ordinary inputs and runtime guidance."""
from typing import Any, List, Optional


def extract_media_path(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("path") or "").strip()
    return str(item or "").strip()


def build_media_paths_message(images: Optional[List[Any]], videos: Optional[List[Any]]) -> Optional[str]:
    paths = []
    for item in (images or []) + (videos or []):
        path = extract_media_path(item)
        if path and path not in paths:
            paths.append(path)
    if not paths:
        return None
    return "\n".join(["[系统通知|media_paths]", "本次消息附带的媒体文件路径：",
                      *(f"- `{path}`" for path in paths)])


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
