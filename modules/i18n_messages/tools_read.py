"""Message pack: tools_read (core/main_terminal_parts/tools_read.py).

read_file / read_skill / search_project_memory / recall_project_memory 等工具
handler 返回 dict 的用户可见消息（error/message/content/summary，显示在前端工具块）。
纯数据模块：禁止 import modules.i18n；由 modules/i18n.py import 时自动聚合。
插值用 str.format 命名参数：tr("tools_read.<key>", name=value)。
"""

MESSAGES = {
    # ── skill 解析 ──
    "tools_read.skill_name_empty": {
        "zh-CN": "skill_name 不能为空",
        "en-US": "skill_name cannot be empty",
    },
    "tools_read.skill_name_conflict": {
        "zh-CN": "技能 '{sid}' 存在同名重复：{primary_dir}/{sid}/SKILL.md 与 {conflict_dir}/{sid}/SKILL.md 均存在，read_skill 无法确定读取哪一份。\n请改用 read_file 工具按具体路径读取查看（可分别读取 {primary_dir}/{sid}/SKILL.md 与 {conflict_dir}/{sid}/SKILL.md 对比），或告知用户删除/重命名其中一份以消除重复。",
        "en-US": "Skill '{sid}' has a name conflict: both {primary_dir}/{sid}/SKILL.md and {conflict_dir}/{sid}/SKILL.md exist, and read_skill cannot determine which one to read.\nUse read_file with the exact path instead (e.g. read {primary_dir}/{sid}/SKILL.md and {conflict_dir}/{sid}/SKILL.md separately to compare), or ask the user to delete/rename one of them to remove the conflict.",
    },
    "tools_read.skill_name_ambiguous": {
        "zh-CN": "skill_name 匹配到多个技能: {matches}，请改用 skill id",
        "en-US": "skill_name matches multiple skills: {matches}; please use the skill id instead",
    },
    "tools_read.skill_not_found": {
        "zh-CN": "未找到技能: {name}",
        "en-US": "Skill not found: {name}",
    },

    # ── 项目记忆工具 ──
    "tools_read.memory_name_invalid": {
        "zh-CN": "记忆名称不合法: {name}",
        "en-US": "Invalid memory name: {name}",
    },
    "tools_read.search_memory_needs_keywords": {
        "zh-CN": "search_project_memory 需要至少 1 个关键词",
        "en-US": "search_project_memory requires at least 1 keyword",
    },
    "tools_read.memory_dir_not_exists": {
        "zh-CN": "项目记忆目录不存在，暂无项目记忆可检索。",
        "en-US": "The project memory directory does not exist; no project memories to search.",
    },
    "tools_read.search_no_match_content": {
        "zh-CN": "未找到匹配的项目记忆（关键词：{keywords}）。不要更换关键词重复检索；继续当前任务即可。",
        "en-US": "No matching project memories found (keywords: {keywords}). Do not re-run the search with different keywords; continue the current task.",
    },
    "tools_read.search_no_match_summary": {
        "zh-CN": "未找到匹配的项目记忆",
        "en-US": "No matching project memories found",
    },
    "tools_read.search_found_header": {
        "zh-CN": "找到 {count} 个匹配的项目记忆（关键词：{keywords}）：",
        "en-US": "Found {count} matching project memories (keywords: {keywords}):",
    },
    "tools_read.search_result_item": {
        "zh-CN": "[{rank}] {name}（.astrion/memory/{file}）",
        "en-US": "[{rank}] {name} (.astrion/memory/{file})",
    },
    "tools_read.search_result_desc": {
        "zh-CN": "    描述：{description}",
        "en-US": "    Description: {description}",
    },
    "tools_read.search_result_snippets_label": {
        "zh-CN": "    匹配片段：",
        "en-US": "    Matching snippets:",
    },
    "tools_read.search_read_full_hint": {
        "zh-CN": "如需完整内容，使用 recall_project_memory 读取对应记忆。",
        "en-US": "For the full content, use recall_project_memory to read the matching memory.",
    },
    "tools_read.search_found_summary": {
        "zh-CN": "找到 {count} 个匹配的项目记忆",
        "en-US": "Found {count} matching project memories",
    },

    # ── read_file 参数校验 / 结果 ──
    "tools_read.missing_file_path": {
        "zh-CN": "缺少文件路径参数",
        "en-US": "Missing file path argument",
    },
    "tools_read.unknown_read_type": {
        "zh-CN": "未知的读取类型: {read_type}",
        "en-US": "Unknown read type: {read_type}",
    },
    "tools_read.param_must_be_int": {
        "zh-CN": "{field_name} 必须是整数",
        "en-US": "{field_name} must be an integer",
    },
    "tools_read.param_must_be_gte_1": {
        "zh-CN": "{field_name} 必须大于等于1",
        "en-US": "{field_name} must be >= 1",
    },
    "tools_read.end_line_ge_start_line": {
        "zh-CN": "end_line 必须大于等于 start_line",
        "en-US": "end_line must be >= start_line",
    },
    "tools_read.search_requires_query": {
        "zh-CN": "搜索模式需要提供 query 参数",
        "en-US": "Search mode requires the query argument",
    },
    "tools_read.extract_requires_segments": {
        "zh-CN": "extract 模式需要提供 segments 数组",
        "en-US": "extract mode requires the segments array",
    },
    "tools_read.read_success": {
        "zh-CN": "已读取 {path} 的内容（行 {line_start}~{line_end}）",
        "en-US": "Read content of {path} (lines {line_start}~{line_end})",
    },
    "tools_read.search_success": {
        "zh-CN": "在 {path} 中搜索 \"{query}\"，返回 {count} 条结果",
        "en-US": "Searched {path} for \"{query}\", returned {count} results",
    },
    "tools_read.extract_success": {
        "zh-CN": "已从 {path} 抽取 {count} 个片段",
        "en-US": "Extracted {count} segments from {path}",
    },
}