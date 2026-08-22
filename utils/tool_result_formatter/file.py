from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from utils.tool_result_formatter.common import (
    _format_failure, _preview_text, _summarize_output_block, _summarize_todo_tasks
)

def format_read_file_result(result_data: Dict[str, Any]) -> str:
    """格式化 read_file 工具的输出，兼容读取/搜索/抽取模式。"""
    if not isinstance(result_data, dict):
        return str(result_data)
    if not result_data.get("success"):
        return _format_failure("read_file", result_data)

    read_type = result_data.get("type", "read")
    truncated_note = "（内容已截断）" if result_data.get("truncated") else ""
    path = result_data.get("path", "未知路径")
    max_chars = result_data.get("max_chars")
    max_note = f"(max_chars={max_chars})" if max_chars else ""

    if read_type == "read":
        header = (
            f"读取 {path} 行 {result_data.get('line_start')}~{result_data.get('line_end')} "
            f"{max_note}{truncated_note}"
        ).strip()
        content = result_data.get("content", "")
        return f"{header}\n```\n{content}\n```"

    if read_type == "search":
        query = result_data.get("query", "")
        actual = result_data.get("actual_matches", 0)
        returned = result_data.get("returned_matches", 0)
        case_hint = "区分大小写" if result_data.get("case_sensitive") else "不区分大小写"
        header = (
            f"在 {path} 中搜索 \"{query}\"，返回 {returned}/{actual} 条结果（{case_hint}） "
            f"{max_note}{truncated_note}"
        ).strip()
        match_texts: List[str] = []
        for idx, match in enumerate(result_data.get("matches", []), 1):
            match_note = "（片段截断）" if match.get("truncated") else ""
            hits = match.get("hits") or []
            hit_text = ", ".join(str(h) for h in hits) if hits else "无"
            label = match.get("id") or f"match_{idx}"
            snippet = match.get("snippet", "")
            match_texts.append(
                f"[{label}] 行 {match.get('line_start')}~{match.get('line_end')} 命中行: {hit_text}{match_note}\n```\n{snippet}\n```"
            )
        if not match_texts:
            match_texts.append("未找到匹配内容。")
        return "\n".join([header] + match_texts)

    if read_type == "extract":
        segments = result_data.get("segments", [])
        header = f"从 {path} 抽取 {len(segments)} 个片段 {max_note}{truncated_note}".strip()
        seg_texts: List[str] = []
        for idx, segment in enumerate(segments, 1):
            seg_note = "（片段截断）" if segment.get("truncated") else ""
            label = segment.get("label") or f"segment_{idx}"
            snippet = segment.get("content", "")
            seg_texts.append(
                f"[{label}] 行 {segment.get('line_start')}~{segment.get('line_end')}{seg_note}\n```\n{snippet}\n```"
            )
        if not seg_texts:
            seg_texts.append("未提供可抽取的片段。")
        return "\n".join([header] + seg_texts)

    return _format_failure("read_file", {"error": "不支持的读取模式"})

def _format_write_file_diff(result_data: Dict[str, Any], raw_text: str) -> str:
    path = result_data.get("path", "目标文件")
    summary = result_data.get("summary") or result_data.get("message")
    completed = result_data.get("completed") or []
    failed_blocks = result_data.get("failed") or []
    success_blocks = result_data.get("blocks") or []
    lines = [f"[文件补丁] {path}"]
    if summary:
        lines.append(summary)
    if completed:
        lines.append(f"✅ 成功块: {', '.join(str(i) for i in completed)}")
    if failed_blocks:
        fail_descriptions = []
        for item in failed_blocks[:3]:
            idx = item.get("index")
            reason = item.get("reason") or item.get("error") or "未说明原因"
            fail_descriptions.append(f"#{idx}: {reason}")
        lines.append("⚠️ 失败块: " + "；".join(fail_descriptions))
        if len(failed_blocks) > 3:
            lines.append(f"(其余 {len(failed_blocks) - 3} 个失败块略)")
        # 通用排查提示：把最常见的坑点一次性说清楚，减少来回沟通成本
        lines.append("🔎 排查提示（常见易错点）：")
        lines.append("- 是否把“要新增/要删除/要替换”的每一行都标了 `+` 或 `-`？（漏标会被当成上下文/锚点）")
        lines.append("- 空行也要写成单独一行的 `+`（只有 `+` 和换行），否则空行会消失或被当成上下文导致匹配失败。")
        lines.append("- 若目标文件是空文件：应使用“仅追加”写法（块内只有 `+` 行，不要混入未加前缀的正文）。")
        lines.append("- 若希望在文件中间插入/替换：必须提供足够的上下文行（以空格开头）或删除行（`-`）来锚定位置，不能只贴 `+`。")
        lines.append("- 是否存在空格/Tab/缩进差异、全角半角标点差异、大小写差异？上下文与原文必须字节级一致。")
        lines.append("- 是否是 CRLF(\\r\\n) 与 LF(\\n) 混用导致原文匹配失败？可先用终端查看/统一换行后再补丁。")
        lines.append("- 是否遗漏 `*** Begin Patch`/`*** End Patch` 或在第一个 `@@` 之前写了其它内容？")
        detail_sections: List[str] = []
        for item in failed_blocks:
            idx = item.get("index")
            reason = item.get("reason") or item.get("error") or "未说明原因"
            hint = item.get("hint")
            block_patch = item.get("block_patch") or item.get("patch")
            # 自动判别常见错误形态，便于快速定位问题
            diagnostics = _classify_diff_block_issue(item, result_data)
            if not block_patch:
                old_text = item.get("old_text") or ""
                new_text = item.get("new_text") or ""
                synthetic_lines: List[str] = []
                if old_text:
                    synthetic_lines.extend(f"-{line}" for line in old_text.splitlines())
                if new_text:
                    synthetic_lines.extend(f"+{line}" for line in new_text.splitlines())
                if synthetic_lines:
                    block_patch = "\n".join(synthetic_lines)
            detail_sections.append(f"- #{idx}: {reason}")
            if diagnostics:
                detail_sections.append(f"  错误类型: {diagnostics}")
            if hint:
                detail_sections.append(f"  提示: {hint}")
            if block_patch:
                detail_sections.append("```diff")
                detail_sections.append(block_patch.rstrip("\n"))
                detail_sections.append("```")
            detail_sections.append("")
        if detail_sections and detail_sections[-1] == "":
            detail_sections.pop()
        if detail_sections:
            lines.append("⚠️ 失败块详情：")
            lines.extend(detail_sections)

    # 对“成功块”做轻量体检：如果检测到潜在格式风险，给出风险提示（不影响 success 判定）
    risk_sections: List[str] = []
    for item in success_blocks:
        if not isinstance(item, dict):
            continue
        status = item.get("status")
        idx = item.get("index")
        if status != "success":
            continue
        diag = _classify_diff_block_issue(item, result_data)
        if diag:
            risk_sections.append(f"- #{idx}: {diag}")
    if risk_sections:
        lines.append("⚠️ 风险提示（补丁虽成功但格式可能有隐患）：")
        lines.extend(risk_sections)

    if result_data.get("success") is False and result_data.get("error"):
        lines.append(f"⚠️ 错误: {result_data.get('error')}")
    formatted = "\n".join(line for line in lines if line)
    return formatted or raw_text

def _format_create_file(result_data: Dict[str, Any]) -> str:
    if not result_data.get("success"):
        return _format_failure("create_file", result_data)
    return result_data.get("message") or f"已创建空文件: {result_data.get('path', '未知路径')}"

def _format_write_file(result_data: Dict[str, Any]) -> str:
    if not result_data.get("success"):
        return _format_failure("write_file", result_data)
    path = result_data.get("path") or "未知路径"
    mode = str(result_data.get("mode") or "w")
    action = "追加" if mode == "a" else "覆盖"
    size = result_data.get("size")
    message = result_data.get("message")
    parts = [f"{action}写入: {path}"]
    if size is not None:
        parts.append(f"{size} 字节")
    if message:
        parts.append(str(message))
    return "，".join(parts)

def _format_edit_file(result_data: Dict[str, Any]) -> str:
    if not result_data.get("success"):
        base = _format_failure("edit_file", result_data)
        failed_details = result_data.get("failed_details")
        details = result_data.get("details")
        detail_lines: List[str] = []
        if isinstance(failed_details, list) and failed_details:
            for item in failed_details[:5]:
                if not isinstance(item, dict):
                    continue
                index = item.get("index")
                reason = item.get("reason") or item.get("error") or "未知原因"
                status = item.get("status")
                prefix = f"第 {index} 组" if index is not None else "某一组"
                if status:
                    detail_lines.append(f"{prefix}失败（{status}）：{reason}")
                else:
                    detail_lines.append(f"{prefix}失败：{reason}")
        if isinstance(details, list):
            completed = [
                item for item in details
                if isinstance(item, dict) and item.get("status") == "success"
            ]
            if completed:
                detail_lines.append(
                    f"失败前已在内存中完成 {len(completed)} 组预处理，但因后续失败未写入文件。"
                )
        if result_data.get("write_performed") is False:
            detail_lines.append("文件未写入。")
        if detail_lines:
            return base + "\n" + "\n".join(detail_lines)
        return base
    path = result_data.get("path") or "目标文件"
    count = result_data.get("replacements")
    groups = result_data.get("replacement_groups")
    found = result_data.get("found_matches")
    msg = result_data.get("message")
    replaced_note = f"替换 {count} 处" if isinstance(count, int) else "已完成替换"
    if isinstance(groups, int):
        replaced_note = f"完成 {groups} 组替换，{replaced_note}"
    parts = [f"{replaced_note}: {path}"]
    if isinstance(found, int):
        parts.append(f"累计找到 {found} 处匹配")
    details = result_data.get("details")
    if isinstance(details, list) and details:
        group_summaries: List[str] = []
        for item in details[:10]:
            if not isinstance(item, dict):
                continue
            index = item.get("index")
            item_found = item.get("found_matches")
            item_replaced = item.get("replacements")
            replace_all = item.get("replace_all")
            mode = "全部匹配" if replace_all is True else "首个匹配"
            if index is not None and isinstance(item_found, int) and isinstance(item_replaced, int):
                group_summaries.append(f"第 {index} 组{mode}：找到 {item_found} 处，替换 {item_replaced} 处")
        if group_summaries:
            parts.append("；".join(group_summaries))
    if msg:
        parts.append(str(msg))
    return "，".join(parts)

def _classify_diff_block_issue(block: Dict[str, Any], result_data: Dict[str, Any]) -> str:
    """
    针对 write_file_diff 常见的“离谱/易错”用法做启发式判别，返回简短错误类型说明。
    不改变后端逻辑，只用于提示。
    """
    patch_text = block.get("block_patch") or block.get("patch") or ""
    lines = patch_text.splitlines()
    plus = sum(1 for ln in lines if ln.startswith("+"))
    minus = sum(1 for ln in lines if ln.startswith("-"))
    context = sum(1 for ln in lines if ln.startswith(" "))
    total = len([ln for ln in lines if ln.strip() != ""])

    reasons: List[str] = []
    # 1) 完全没加 + / - ：最常见的“把目标当上下文”
    if total > 0 and plus == 0 and minus == 0:
        reasons.append("缺少 + / -，整块被当作上下文，无法定位到文件")
    # 2) 全是 + 且没有上下文/删除：解析为“纯追加”，若目标非末尾插入会失败
    if plus > 0 and minus == 0 and context == 0:
        reasons.append("仅包含 + 行，被视为追加块；若想中间插入/替换需提供上下文或 -")
    # 3) 没有上下文或删除行却不是 append_only（多数是漏写空格前缀）
    if block.get("append_only") and (context > 0 or minus > 0):
        reasons.append("块被解析为追加模式，但混入了上下文/删除行，可能写法不一致")
    # 4) 未找到匹配时，提示检查空格/缩进/全角半角/换行差异
    reason_text = (block.get("reason") or "").lower()
    if "未找到匹配" in reason_text:
        reasons.append("上下文未匹配：检查空格/缩进、全角半角、CRLF/LF、大小写是否与原文完全一致")
    # 5) 空行未加 '+' 的典型情形：
    #    a) 有空白行但整块没有前缀（此前已由 #1 捕获），仍补充提示
    #    b) 有空白行且块中存在 + / -，说明空行漏写前缀，易导致上下文匹配失败
    if any(ln == "" or ln.strip() == "" for ln in lines):
        if plus == 0 and minus == 0:
            reasons.append("空行未写 `+`，被当作上下文，建议空行写成单独一行 `+`")
        else:
            reasons.append("空行未写 `+`（或空格上下文），混入补丁时会被当作上下文，建议空行单独写成 `+`")

    return "；".join(reasons)

def _format_delete_file(result_data: Dict[str, Any]) -> str:
    if not result_data.get("success"):
        return _format_failure("delete_file", result_data)
    path = result_data.get("path") or "未知路径"
    action = result_data.get("action") or "deleted"
    return f"已{action}文件: {path}"

def _format_rename_file(result_data: Dict[str, Any]) -> str:
    if not result_data.get("success"):
        return _format_failure("rename_file", result_data)
    old_path = result_data.get("old_path") or "旧路径未知"
    new_path = result_data.get("new_path") or "新路径未知"
    return f"已重命名: {old_path} -> {new_path}"

def _format_create_folder(result_data: Dict[str, Any]) -> str:
    if not result_data.get("success"):
        return _format_failure("create_folder", result_data)
    return f"已创建文件夹: {result_data.get('path', '未知路径')}"
