"""Backend i18n message pack: file_manager-family user-visible messages.

Covers modules/file_manager/ (base.py, path_mixin.py, read_mixin.py,
list_mixin.py, crud_mixin.py, patch_mixin.py, replace_mixin.py). Pure data
module — do not import anything here. Auto-discovered and merged by
modules/i18n.py at import time.
zh-CN copy is verbatim from source; en-US is concise product-level
English (sentence case).
"""

MESSAGES = {
    # ── base.py（容器未就绪） ──
    "file_manager.container_not_ready": {
        "zh-CN": "容器未就绪，无法执行文件操作",
        "en-US": "Container is not ready; cannot perform file operations",
    },

    # ── path_mixin.py（_validate_path / _ensure_host_access，传播到多处显示） ──
    "file_manager.path_outside_workspace_detailed": {
        "zh-CN": "路径必须在项目文件夹内。请检查是否使用的是不带/workspace的相对路径。",
        "en-US": "Path must be inside the project folder. Please use a relative path without /workspace.",
    },
    "file_manager.path_outside_project": {
        "zh-CN": "路径必须在项目文件夹内",
        "en-US": "Path must be inside the project folder",
    },
    "file_manager.path_traversal_blocked": {
        "zh-CN": "不允许使用../向上遍历",
        "en-US": "Path traversal (../) is not allowed",
    },
    "file_manager.path_forbidden_root": {
        "zh-CN": "禁止访问根目录: {path}",
        "en-US": "Access to root directory is forbidden: {path}",
    },
    "file_manager.path_forbidden_system": {
        "zh-CN": "禁止访问系统目录: {path}",
        "en-US": "Access to system directory is forbidden: {path}",
    },
    "file_manager.host_access_write_denied": {
        "zh-CN": "目标路径不在可写授权范围内，请在路径授权中添加后重试。",
        "en-US": "Target path is outside the writable authorization scope. Please add it to path authorization and retry.",
    },
    "file_manager.host_access_read_denied": {
        "zh-CN": "目标路径不在可读授权范围内，请在路径授权中添加后重试。",
        "en-US": "Target path is outside the readable authorization scope. Please add it to path authorization and retry.",
    },

    # ── read_mixin.py ──
    "file_manager.file_not_found": {
        "zh-CN": "文件不存在",
        "en-US": "File not found",
    },
    "file_manager.file_too_large": {
        "zh-CN": "文件太大 ({size}MB > {limit}MB)",
        "en-US": "File too large ({size}MB > {limit}MB)",
    },
    "file_manager.not_utf8_text": {
        "zh-CN": "文件不是 UTF-8 文本，无法直接读取，请改用 run_command 调用合适的解析工具或 Python 解释器。",
        "en-US": "File is not UTF-8 text and cannot be read directly. Use run_command with a suitable parser or Python instead.",
    },
    "file_manager.read_failed": {
        "zh-CN": "读取文件失败: {error}",
        "en-US": "Failed to read file: {error}",
    },
    "file_manager.not_a_file": {
        "zh-CN": "不是文件",
        "en-US": "Not a file",
    },
    "file_manager.start_line_out_of_file": {
        "zh-CN": "起始行超出文件长度",
        "en-US": "Start line exceeds the file length",
    },
    "file_manager.missing_search_query": {
        "zh-CN": "缺少搜索关键词",
        "en-US": "Search query is missing",
    },
    "file_manager.missing_segments": {
        "zh-CN": "缺少要提取的行区间",
        "en-US": "Missing segments to extract",
    },
    "file_manager.segments_items_must_be_objects": {
        "zh-CN": "segments 数组中的每一项都必须是对象",
        "en-US": "Every item in the segments array must be an object",
    },
    "file_manager.segments_need_line_bounds": {
        "zh-CN": "所有区间都必须包含 start_line 和 end_line",
        "en-US": "Every segment must include start_line and end_line",
    },
    "file_manager.segment_range_invalid": {
        "zh-CN": "行区间不合法",
        "en-US": "Invalid line range",
    },
    "file_manager.segment_start_exceeds": {
        "zh-CN": "区间起点 {line} 超出文件行数",
        "en-US": "Segment start line {line} exceeds the file line count",
    },

    # ── list_mixin.py ──
    "file_manager.line_start_must_be_one": {
        "zh-CN": "行号必须从1开始",
        "en-US": "Line numbers must start at 1",
    },
    "file_manager.end_line_lt_start_line": {
        "zh-CN": "结束行号不能小于起始行号",
        "en-US": "End line cannot be smaller than the start line",
    },
    "file_manager.edit_start_line_out_of_range": {
        "zh-CN": "起始行号 {start_line} 超出文件范围 (共 {total_lines} 行)",
        "en-US": "Start line {start_line} is out of the file range (file has {total_lines} lines)",
    },
    "file_manager.edit_end_line_out_of_range": {
        "zh-CN": "结束行号 {end_line} 超出文件范围 (共 {total_lines} 行)",
        "en-US": "End line {end_line} is out of the file range (file has {total_lines} lines)",
    },
    "file_manager.unknown_operation": {
        "zh-CN": "未知的操作类型: {operation}",
        "en-US": "Unknown operation type: {operation}",
    },
    "file_manager.dir_not_found": {
        "zh-CN": "目录不存在",
        "en-US": "Directory not found",
    },
    "file_manager.not_a_directory": {
        "zh-CN": "不是目录",
        "en-US": "Not a directory",
    },
    "file_manager.desc_replace_lines": {
        "zh-CN": "替换第 {start}-{end} 行",
        "en-US": "Replaced lines {start}-{end}",
    },
    "file_manager.desc_insert_before": {
        "zh-CN": "在第 {line} 行前插入",
        "en-US": "Inserted before line {line}",
    },
    "file_manager.desc_delete_lines": {
        "zh-CN": "删除第 {start}-{end} 行",
        "en-US": "Deleted lines {start}-{end}",
    },

    # ── crud_mixin.py ──
    "file_manager.original_not_found": {
        "zh-CN": "原文件不存在",
        "en-US": "Original file not found",
    },
    "file_manager.target_exists": {
        "zh-CN": "目标文件已存在",
        "en-US": "Target file already exists",
    },
    "file_manager.folder_exists": {
        "zh-CN": "文件夹已存在",
        "en-US": "Folder already exists",
    },
    "file_manager.folder_not_found": {
        "zh-CN": "文件夹不存在",
        "en-US": "Folder not found",
    },
    "file_manager.not_a_folder": {
        "zh-CN": "不是文件夹",
        "en-US": "Not a folder",
    },
    "file_manager.content_too_long": {
        "zh-CN": "内容过长({length}字符)，超过100KB限制",
        "en-US": "Content too long ({length} chars); exceeds the 100KB limit",
    },
    "file_manager.storage_quota_exceeded": {
        "zh-CN": "写入失败：超出项目磁盘配额",
        "en-US": "Write failed: project disk quota exceeded",
    },

    # ── patch_mixin.py (apply_diff_patch / apply_modify_blocks) ──
    "file_manager.patch_missing_markers": {
        "zh-CN": "补丁格式错误：缺少 *** Begin Patch / *** End Patch 标记。",
        "en-US": "Patch format error: missing *** Begin Patch / *** End Patch markers.",
    },
    "file_manager.patch_bad_end_marker": {
        "zh-CN": "补丁格式错误：结束标记位置异常。",
        "en-US": "Patch format error: end marker position is invalid.",
    },
    "file_manager.patch_block_empty": {
        "zh-CN": "补丁块缺少内容：{header}",
        "en-US": "Patch block is missing content: {header}",
    },
    "file_manager.patch_block_id_not_int": {
        "zh-CN": "补丁块编号必须是整数：{header}",
        "en-US": "Patch block id must be an integer: {header}",
    },
    "file_manager.patch_content_before_first_block": {
        "zh-CN": "补丁格式错误：在检测到第一个 @@ 块之前出现内容。",
        "en-US": "Patch format error: content appears before the first @@ block.",
    },
    "file_manager.patch_no_blocks": {
        "zh-CN": "补丁格式错误：未检测到任何 @@ [id:n] 块。",
        "en-US": "Patch format error: no @@ [id:n] blocks found.",
    },
    "file_manager.patch_block_no_content": {
        "zh-CN": "补丁块 {index} 未包含任何 + / - / 上下文行。",
        "en-US": "Patch block {index} contains no + / - / context lines.",
    },
    "file_manager.patch_no_valid_blocks": {
        "zh-CN": "未检测到有效的补丁块。",
        "en-US": "No valid patch blocks detected.",
    },
    "file_manager.append_write_failed": {
        "zh-CN": "追加写入失败: {error}",
        "en-US": "Failed to append: {error}",
    },
    "file_manager.write_failed": {
        "zh-CN": "写入文件失败: {error}",
        "en-US": "Failed to write file: {error}",
    },
    "file_manager.patch_summary_header": {
        "zh-CN": "向 {path} 应用 {total} 个补丁块",
        "en-US": "Applied {total} patch blocks to {path}",
    },
    "file_manager.patch_summary_success": {
        "zh-CN": "成功 {count} 个",
        "en-US": "{count} succeeded",
    },
    "file_manager.patch_summary_failed": {
        "zh-CN": "失败 {count} 个",
        "en-US": "{count} failed",
    },
    "file_manager.patch_summary_append": {
        "zh-CN": "追加 {blocks} 块，写入 {lines} 行（{bytes} 字节）",
        "en-US": "Appended {blocks} blocks, wrote {lines} lines ({bytes} bytes)",
    },

    # ── replace_mixin.py ──
    "file_manager.replace_old_text_too_long": {
        "zh-CN": "要替换的文本过长，可能导致性能问题",
        "en-US": "Text to replace is too long and may cause performance issues",
    },
    "file_manager.replace_new_text_too_long": {
        "zh-CN": "替换的新文本过长，建议分块处理",
        "en-US": "Replacement text is too long; consider splitting it into smaller parts",
    },
    "file_manager.replace_not_found": {
        "zh-CN": "未找到要替换的内容",
        "en-US": "Replacement target not found",
    },
    "file_manager.replacements_required": {
        "zh-CN": "replacements 必须是非空数组",
        "en-US": "replacements must be a non-empty array",
    },
    "file_manager.replacements_too_many": {
        "zh-CN": "replacements 数量过多，最多支持 100 组",
        "en-US": "Too many replacements; at most 100 groups are supported",
    },
    "file_manager.replace_group_failed": {
        "zh-CN": "第 {index} 组替换失败：{reason}",
        "en-US": "Replacement group {index} failed: {reason}",
    },
    "file_manager.replace_found_report": {
        "zh-CN": "发现{found}处，于{lines}行共替换{count}处",
        "en-US": "Found {found} matches; replaced {count} at lines {lines}",
    },
    "file_manager.replace_short_old_notice": {
        "zh-CN": "提示：old_string 少于3行，已继续执行；需要批量替换的场景可以单行或不足一行",
        "en-US": "Note: old_string is shorter than 3 lines; execution continued. For batch replacement, use single-line or shorter strings",
    },
    "file_manager.replace_many_summary": {
        "zh-CN": "共 {groups} 组替换，替换 {replacements} 处",
        "en-US": "{groups} groups processed; {replacements} replacements made",
    },
    "file_manager.replace_many_short_notice": {
        "zh-CN": "提示：第 {indices} 组 old_string 少于3行，已继续执行",
        "en-US": "Note: old_string in groups {indices} is shorter than 3 lines; execution continued",
    },
}