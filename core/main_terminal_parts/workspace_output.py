"""Write workspace artifacts using the selected execution filesystem."""
from __future__ import annotations


def ensure_directory(files, path: str) -> dict:
    info = files.inspect_path(path, access="write")
    if not info.get("success"):
        return info
    if info.get("exists"):
        if info.get("is_dir"):
            return {"success": True, "path": info.get("path") or path}
        return {"success": False, "error": "Output directory is not a directory"}
    result = files.create_folder(path)
    if result.get("success"):
        return result
    # A concurrent creator is acceptable only after executor-side revalidation.
    info = files.inspect_path(path, access="write")
    if info.get("success") and info.get("is_dir"):
        return {"success": True, "path": info.get("path") or path}
    return result


def write_workspace_text(files, directory: str, filename: str, content: str) -> dict:
    prepared = ensure_directory(files, directory)
    if not prepared.get("success"):
        return prepared
    target = f"{directory.rstrip('/')}/{filename}"
    return files.write_file(target, content, mode="w")
