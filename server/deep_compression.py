from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

def _load_summary_prompt(web_terminal) -> str:
    """从 prompts/deep_compression_summary.txt 加载压缩总结提示词。"""
    try:
        return web_terminal.load_prompt("deep_compression_summary").strip()
    except Exception:
        return (
            "由于当前对话过长，系统正在自动压缩。请你基于已有上下文输出一份可继续执行的工作总结。"
        )


def _build_summary_prompt(template: str, *, compression_index: int, latest_user_input: str) -> str:
    """把压缩轮次、最新用户输入、增量条目占位符替换进总结提示词模板。

    - {compression_index}: 当前压缩轮次（总是替换）
    - {latest_user_input}: 最新一条真实用户输入原文
    - {incremental_item}: 第2次压缩起插入增量总结条目，首次压缩替换为空
    """
    prompt = template or ""
    latest = (latest_user_input or "").strip() or "（无）"
    if compression_index > 1:
        incremental_item = (
            "9) 自上次压缩之后到现在新完成的工作：单独列出这段时间内新完成的工作与关键结果，"
            "此前已被历次压缩总结覆盖的内容不必重复。\n"
        )
    else:
        incremental_item = ""
    prompt = prompt.replace("{compression_index}", str(compression_index))
    prompt = prompt.replace("{latest_user_input}", latest)
    prompt = prompt.replace("{incremental_item}", incremental_item)
    return prompt


def _emit(sender, event_type: str, payload: Dict[str, Any]):
    if not callable(sender):
        return
    try:
        sender(event_type, payload)
    except Exception:
        pass


def _clear_compression_state_on_error(func):
    """压缩过程异常退出时清理持久化的 compression_in_progress 标记。

    压缩中途异常（或调用侧取消）若不清标记，对话 metadata 会永久残留
    in_progress=True，前端据此锁输入栏/拦切换对话（只能删对话解决）。
    """
    @wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except Exception:
            try:
                web_terminal = kwargs.get("web_terminal")
                conversation_id = kwargs.get("conversation_id")
                cm = getattr(web_terminal, "context_manager", None)
                if cm is not None and conversation_id:
                    if getattr(cm, "current_conversation_id", None) == conversation_id:
                        cm.set_compression_state(in_progress=False)
                    else:
                        target_manager = (
                            cm._get_conversation_manager_for_id(conversation_id)
                            if hasattr(cm, "_get_conversation_manager_for_id")
                            else cm.conversation_manager
                        )
                        target_manager.update_conversation_metadata(conversation_id, {
                            "compression_in_progress": False,
                            "compression_mode": None,
                            "compression_stage": None,
                            "compression_job_id": None,
                            "compression_resume_payload": None,
                            "compression_pid": None,
                        })
            except Exception:
                pass
            raise
    return wrapper


def heal_stale_compression_flag(
    target_manager,
    conversation_id: str,
    metadata: Dict[str, Any],
    context_manager=None,
) -> bool:
    """读取压缩进行态并懒清理残留标记，返回有效的 in_progress。

    compression_in_progress 持久化在对话 metadata 中。进程在压缩中途被杀时标记
    残留为 True（压缩随进程死亡，不可能仍在进行），会导致前端误以为仍在压缩。
    判定依据：标记写入时记录了发起进程的 pid（compression_pid，见
    compression_mixin.set_compression_state）；与当前进程 pid 不一致（或缺失，
    即旧版本写入的标记）即判定为残留并清除（磁盘 + 内存双清）。
    """
    if not bool((metadata or {}).get("compression_in_progress", False)):
        return False
    flag_pid = (metadata or {}).get("compression_pid")
    if flag_pid is not None and flag_pid == os.getpid():
        return True
    clear_updates = {
        "compression_in_progress": False,
        "compression_mode": None,
        "compression_stage": None,
        "compression_job_id": None,
        "compression_resume_payload": None,
        "compression_pid": None,
    }
    try:
        target_manager.update_conversation_metadata(conversation_id, clear_updates)
    except Exception:
        pass
    # 同步内存中的 metadata：该对话若正被加载，工具循环的
    # is_compression_in_progress() 读的是内存副本，不清理会一直误判压缩中。
    try:
        if (
            context_manager is not None
            and getattr(context_manager, "current_conversation_id", None) == conversation_id
            and isinstance(getattr(context_manager, "conversation_metadata", None), dict)
        ):
            context_manager.conversation_metadata.update(clear_updates)
    except Exception:
        pass
    return False


def _normalize_deep_compression_records(metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
    records = metadata.get("deep_compression_records")
    if not isinstance(records, list):
        return []
    normalized: List[Dict[str, Any]] = []
    for item in records:
        if not isinstance(item, dict):
            continue
        try:
            count = int(item.get("count", 0) or 0)
        except Exception:
            count = 0
        path = str(item.get("compact_file") or "").strip()
        if count <= 0 or not path:
            continue
        try:
            user_inputs_before = int(item.get("user_inputs_before", 0) or 0)
        except Exception:
            user_inputs_before = 0
        normalized.append({
            "count": count,
            "compact_file": path,
            "created_at": item.get("created_at"),
            "source_conversation_id": item.get("source_conversation_id"),
            "compressed_conversation_id": item.get("compressed_conversation_id"),
            "user_inputs_before": user_inputs_before,
            "summary": str(item.get("summary") or ""),
        })
    normalized.sort(key=lambda x: (int(x.get("count") or 0), str(x.get("created_at") or "")))
    deduped: List[Dict[str, Any]] = []
    seen = set()
    for rec in normalized:
        key = (int(rec.get("count") or 0), str(rec.get("compact_file") or ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(rec)
    return deduped


def _collect_runtime_state_lines(web_terminal, conversation_id: str) -> List[str]:
    """收集压缩后模型续接所需的运行时状态。

    - 子智能体编号：仅传统模式列出（该模式下编号由模型指定且重复会创建失败）；
      多智能体模式不列编号（模型可通过 list_active_sub_agents 工具查询活跃实例）。
    - 持久终端：只列数量与名称。
    """
    lines: List[str] = []
    is_multi_agent = bool(getattr(web_terminal, "multi_agent_mode", False))
    if not is_multi_agent:
        used: List[int] = []
        manager = getattr(web_terminal, "sub_agent_manager", None)
        if manager is not None:
            try:
                agents_map = getattr(manager, "conversation_agents", None) or {}
                raw_used = agents_map.get(conversation_id) or []
                used = sorted(int(x) for x in raw_used)
            except Exception:
                used = []
        if used:
            next_id = max(used) + 1
            used_text = "、".join(str(x) for x in used)
            lines.append(
                f"- 子智能体编号：本对话已使用编号 {used_text}，下一个可用编号为 {next_id}。"
                "新建子智能体时请使用该编号，重复使用已用编号会创建失败。"
            )
        else:
            lines.append("- 子智能体编号：本对话尚未创建过子智能体，下一个可用编号为 1。")
    terminal_names: List[str] = []
    terminal_manager = getattr(web_terminal, "terminal_manager", None)
    if terminal_manager is not None:
        try:
            terminals = getattr(terminal_manager, "terminals", None) or {}
            terminal_names = [str(name) for name in terminals.keys()]
        except Exception:
            terminal_names = []
    if terminal_names:
        names_text = "、".join(terminal_names)
        lines.append(f"- 持久终端：当前共有 {len(terminal_names)} 个终端会话：{names_text}。")
    else:
        lines.append("- 持久终端：当前没有终端会话。")
    return lines


def _build_guide_message(*, compression_index: int, compact_file: str) -> str:
    """生成文件模式的引导语：仅提示压缩文件位置，由模型自行阅读。"""
    return f"当前对话已经被第{compression_index}次压缩。请阅读 {compact_file} 并继续工作。"


def _read_compact_file_content(project_path: Path, relative_path: str) -> str:
    """读取 compact 文件全文，读取失败时返回空串。"""
    rel = str(relative_path or "").strip()
    if not rel:
        return ""
    try:
        target = (Path(project_path) / rel).resolve()
        return target.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _read_summary_from_record(record: Dict[str, Any]) -> str:
    """从 deep_compression_records 条目中读取保存的总结内容。"""
    summary = record.get("summary")
    return str(summary).strip() if isinstance(summary, str) else ""


def _build_user_inputs_section(
    user_inputs: List[str],
    previous_records: List[Dict[str, Any]],
    current_count: int,
) -> str:
    """构建'用户的所有输入'区块，按历史压缩轮次插入分段标记。"""
    if not user_inputs:
        return "（无）"
    breakpoints: Dict[int, int] = {}
    for rec in previous_records:
        count = int(rec.get("count") or 0)
        before = int(rec.get("user_inputs_before") or 0)
        if count > 0 and before > 0:
            breakpoints[before] = count
    sorted_breaks = sorted(breakpoints.items(), key=lambda x: x[0])
    break_iter = iter(sorted_breaks)
    next_break = next(break_iter, None)
    lines: List[str] = []
    last_break_index = 0
    for idx, text in enumerate(user_inputs, start=1):
        lines.append(f"{idx}. {text}")
        if next_break and idx == next_break[0]:
            lines.append(f"<第{next_break[1]}次压缩>")
            last_break_index = idx
            next_break = next(break_iter, None)
    # 当前压缩之后还有新增输入时，追加当前压缩标记
    if len(user_inputs) > last_break_index:
        lines.append(f"<当前触发的第{current_count}次压缩>")
    return "\n".join(lines)


def _build_summaries_section(
    previous_records: List[Dict[str, Any]],
    current_summary: str,
    current_count: int,
) -> List[str]:
    """构建'历次压缩总结'区块，按顺序列出每次压缩的总结。"""
    lines: List[str] = []
    for rec in previous_records:
        count = int(rec.get("count") or 0)
        if count <= 0:
            continue
        summary = _read_summary_from_record(rec)
        lines.append(f"### 第{count}次的总结")
        lines.append(summary or "（读取失败）")
        lines.append("")
    lines.append(f"### 第{current_count}次的总结")
    lines.append(current_summary or "（生成失败）")
    return lines


def _build_inject_guide_message(
    *,
    compression_index: int,
    current_record: Dict[str, Any],
    previous_records: List[Dict[str, Any]],
    user_inputs: List[str],
    runtime_state_lines: Optional[List[str]] = None,
) -> str:
    """生成直接注入模式的引导语：把历次压缩总结、用户输入按顺序拼入正文。"""
    lines: List[str] = [
        f"当前对话已被第{compression_index}次压缩。以下为按时间顺序汇总的用户输入、历次压缩总结，请据此继续工作。",
        "",
        "用户的所有输入",
    ]
    lines.append(_build_user_inputs_section(user_inputs, previous_records, compression_index))
    lines.append("")
    lines.append("历次压缩总结")
    lines.append("")
    summary_lines = _build_summaries_section(
        previous_records,
        current_record.get("summary", ""),
        compression_index,
    )
    lines.extend(summary_lines)
    if runtime_state_lines:
        lines.append("")
        lines.append("当前运行时状态")
        lines.extend(runtime_state_lines)
    return "\n".join(lines).strip()


def _extract_text_only(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                txt = item.get("text")
                if isinstance(txt, str) and txt.strip():
                    parts.append(txt)
        return "\n".join(parts).strip()
    return str(content)


def _collect_user_texts(messages: List[Dict[str, Any]]) -> List[str]:
    # 口径：用户亲手输入 = message_source 为 'user'（直接发送）或 'presend'（提前输入排队后转正），
    # 字段缺失的老消息视为 'user'；guidance（手动引导）/notify/compression 等运行期注入消息不计入。
    # 与前端左侧输入导航 isRealUserNavMessage（ChatArea.vue）口径保持一致。
    result: List[str] = []
    for msg in messages:
        if msg.get("role") != "user":
            continue
        metadata = msg.get("metadata") if isinstance(msg.get("metadata"), dict) else {}
        source = str(metadata.get("message_source") or "user").strip().lower()
        if source not in ("user", "presend"):
            continue
        text = _extract_text_only(msg.get("content"))
        if text.strip():
            result.append(text.strip())
    return result


async def _generate_summary(web_terminal, prompt: str, retries: int = 5) -> Tuple[str, Optional[str]]:
    last_reason: Optional[str] = None
    context = web_terminal.build_context()
    messages = web_terminal.build_messages(context, prompt)
    # build_messages 不会自动附加 user_input，压缩总结必须显式注入
    if prompt and isinstance(prompt, str):
        messages = list(messages) + [{"role": "user", "content": prompt}]
    # 关键：总结请求必须与"用户正常发一条消息"完全无差别，才能 100% 命中前缀缓存。
    # 因此 tools 必须照常传入（缺失 tools 字段会改变请求前缀、破坏缓存）。
    # 模型即便返回 tool_calls 也会被忽略——我们只取 content；prompt 已要求其直接输出总结。
    tools = web_terminal.define_tools()
    for _ in range(max(1, retries)):
        try:
            response_text = ""
            async for chunk in web_terminal.api_client.chat(messages, tools=tools, stream=False):
                if not isinstance(chunk, dict):
                    continue
                choices = chunk.get("choices") or []
                if choices:
                    msg = choices[0].get("message") or {}
                    content = msg.get("content")
                    if isinstance(content, str):
                        response_text = content
            if response_text.strip():
                return response_text.strip(), None
            last_reason = "模型返回空内容"
        except Exception as exc:
            last_reason = str(exc)
        await asyncio.sleep(0.2)
    return f"生成总结失败（{last_reason or '未知原因'}）", last_reason


def _write_compact_file(
    project_path: Path,
    *,
    compression_index: int,
    summary_text: str,
    user_inputs: List[str],
    previous_records: List[Dict[str, Any]],
    runtime_state_lines: Optional[List[str]] = None,
) -> str:
    compact_dir = project_path / ".astrion" / "compact_result"
    compact_dir.mkdir(parents=True, exist_ok=True)
    filename = f"compact_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{compression_index:03d}.md"
    file_path = compact_dir / filename

    lines: List[str] = [
        f"# 对话已被第{compression_index}次压缩",
        "",
        "## 用户的所有输入",
    ]
    lines.append(_build_user_inputs_section(user_inputs, previous_records, compression_index))
    lines.append("")
    lines.append("## 历次压缩总结")
    lines.append("")
    summary_lines = _build_summaries_section(
        previous_records,
        summary_text,
        compression_index,
    )
    lines.extend(summary_lines)
    if runtime_state_lines:
        lines.append("")
        lines.append("## 当前运行时状态")
        lines.extend(runtime_state_lines)
    file_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return str(file_path.relative_to(project_path))


def _mark_history_compacted(history: List[Dict[str, Any]], *, round_index: int, now: str) -> int:
    """把当前对话历史中所有尚未标记的消息打上 deep_compacted 标记（in-place）。

    深压缩按"整段前缀"处理：本次压缩时，历史里所有还没被标记的消息整体视为已压缩前缀。
    这天然保证 assistant.tool_calls 与其 tool 响应被一并标记/排除，不会出现配对悬空。
    原文保留在 metadata 中，持久化与重新加载时照常显示，仅在 build_messages 构建请求时被跳过。
    返回本次新标记的消息条数。
    """
    if not isinstance(history, list):
        return 0
    marked = 0
    for msg in history:
        if not isinstance(msg, dict):
            continue
        metadata = msg.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        if metadata.get("deep_compacted"):
            continue
        metadata["deep_compacted"] = True
        metadata["deep_compacted_round"] = round_index
        metadata["deep_compacted_at"] = now
        msg["metadata"] = metadata
        marked += 1
    return marked


@_clear_compression_state_on_error
async def run_deep_compression(
    *,
    web_terminal,
    workspace,
    conversation_id: str,
    mode: str,
    sender=None,
) -> Dict[str, Any]:
    cm = web_terminal.context_manager
    target_manager = (
        cm._get_conversation_manager_for_id(conversation_id)
        if hasattr(cm, "_get_conversation_manager_for_id")
        else cm.conversation_manager
    )
    conv_data = target_manager.load_conversation(conversation_id)
    if not conv_data:
        return {"success": False, "error": f"对话不存在: {conversation_id}"}

    metadata = conv_data.get("metadata", {}) or {}
    if metadata.get("compression_in_progress"):
        return {"success": False, "error": "对话正在压缩中", "in_progress": True}

    # 读取个性化压缩设置：
    # - compress_form: file（生成文件，引导语提示位置） / inject（把历次压缩内容注入引导语）
    try:
        from modules.personalization_manager import load_personalization_config
        pconfig = load_personalization_config(workspace.data_dir) or {}
    except Exception:
        pconfig = {}
    compress_form = str(pconfig.get("deep_compress_form") or "file").strip().lower()
    if compress_form not in ("file", "inject"):
        compress_form = "file"
    # compress_behavior 固定规则（无个性化开关）：
    # - 手动压缩：只生成压缩消息（引导语），不自动续接，等待用户继续发送消息才工作。
    # - 自动深压缩：当前任务尚未完成才触发，压缩后必须继续工作。
    compress_behavior = "wait" if mode == "manual" else "continue"

    # in-place 压缩全程操作"当前对话"的内存历史：总结、标记、状态写入都依赖
    # cm.conversation_history / cm.conversation_metadata。若当前对话不是目标对话，
    # 必须先切换过去，否则总结会基于错误历史、压缩状态也会写错对话。
    if getattr(cm, "current_conversation_id", None) != conversation_id:
        try:
            web_terminal.load_conversation(conversation_id)
        except Exception as exc:
            return {"success": False, "error": f"加载目标对话失败: {exc}"}

    compression_count = int(metadata.get("compression_count", 0) or 0)
    previous_records = _normalize_deep_compression_records(metadata)
    previous_max_count = max([int(item.get("count") or 0) for item in previous_records], default=0)
    target_count = max(compression_count, previous_max_count) + 1
    job_id = f"cmp_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    cm.set_compression_state(
        in_progress=True,
        mode=mode,
        stage="generating_summary",
        job_id=job_id,
        resume_payload={"conversation_id": conversation_id, "mode": mode},
    )
    _emit(sender, "compression_state", {
        "conversation_id": conversation_id,
        "in_progress": True,
        "mode": mode,
        "stage": "generating_summary",
        "job_id": job_id,
    })

    # 提前收集用户输入与运行时状态：总结提示词需要嵌入最新输入与压缩轮次，
    # compact 文件 / 注入引导语需要追加运行时状态区块。
    messages = conv_data.get("messages") or []
    user_inputs = _collect_user_texts(messages)
    latest_user_input = user_inputs[-1] if user_inputs else ""
    user_inputs_before = len(user_inputs)
    runtime_state_lines = _collect_runtime_state_lines(web_terminal, conversation_id)

    summary_prompt = _build_summary_prompt(
        _load_summary_prompt(web_terminal),
        compression_index=target_count,
        latest_user_input=latest_user_input,
    )
    summary_text, summary_fail_reason = await _generate_summary(web_terminal, summary_prompt, retries=5)
    if summary_fail_reason:
        _emit(sender, "system_message", {"content": f"自动压缩总结失败，将使用失败占位文本：{summary_fail_reason}"})

    cm.set_compression_state(
        in_progress=True,
        mode=mode,
        stage="writing_compact",
        job_id=job_id,
    )
    _emit(sender, "compression_state", {
        "conversation_id": conversation_id,
        "in_progress": True,
        "mode": mode,
        "stage": "writing_compact",
        "job_id": job_id,
    })

    relative_compact_path = _write_compact_file(
        Path(workspace.project_path),
        compression_index=target_count,
        summary_text=summary_text,
        user_inputs=user_inputs,
        previous_records=previous_records,
        runtime_state_lines=runtime_state_lines,
    )

    cm.set_compression_state(
        in_progress=True,
        mode=mode,
        stage="marking_history",
        job_id=job_id,
    )

    # === in-place 压缩：不创建/切换新对话，只把当前对话历史前缀打上 deep_compacted 标记 ===
    now_iso = datetime.now().isoformat()
    marked_count = _mark_history_compacted(
        cm.conversation_history or [],
        round_index=target_count,
        now=now_iso,
    )
    # 标记后立即持久化历史（标记写在每条消息的 metadata 中）。
    try:
        cm.save_current_conversation()
    except Exception as exc:
        _emit(sender, "system_message", {"content": f"压缩标记保存失败：{exc}"})

    # 关键：重置 current_context_tokens，避免自动压缩续接后阈值判断仍读到压缩前的大值而陷入死循环。
    # 真实上下文长度会在下一次 API 响应后被重新写入。
    try:
        target_manager.update_token_statistics(
            conversation_id,
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            current_context_tokens=0,
        )
    except Exception as exc:
        _emit(sender, "system_message", {"content": f"压缩后重置上下文统计失败：{exc}"})

    current_record = {
        "count": target_count,
        "compact_file": relative_compact_path,
        "created_at": now_iso,
        "source_conversation_id": conversation_id,
        "compressed_conversation_id": conversation_id,
        "user_inputs_before": user_inputs_before,
        "summary": summary_text,
    }
    all_records = previous_records + [current_record]

    # 构建引导语（按压缩形式）。
    if compress_form == "inject":
        guide_message = _build_inject_guide_message(
            compression_index=target_count,
            current_record=current_record,
            previous_records=previous_records,
            user_inputs=user_inputs,
            runtime_state_lines=runtime_state_lines,
        )
    else:
        guide_message = _build_guide_message(
            compression_index=target_count,
            compact_file=relative_compact_path,
        )

    # 更新对话 metadata：压缩记录 + 清理压缩状态标记（同一对话，无切换）。
    # 同时清除 frozen prompt 缓存，使压缩后下一次请求自动重新加载动态内容。
    REBUILD_FROZEN_KEYS = (
        "frozen_main_system_prompt",
        "frozen_permission_prompt",
        "frozen_execution_prompt",
        "frozen_recent_conversations_prompt",
        "frozen_personalization_prompt",
        "frozen_workspace_prompt",
        "frozen_agents_md_prompt",
        "frozen_skills_prompt",
        "frozen_memory_prompt",
        "frozen_custom_system_prompt",
        "frozen_disabled_tools_prompt",
    )
    meta_updates = {
        "compression_count": target_count,
        "deep_compression_records": all_records,
        "last_deep_compression_record": current_record,
        "compression_in_progress": False,
        "compression_mode": None,
        "compression_stage": None,
        "compression_job_id": None,
        "compression_error": summary_fail_reason,
        "compression_resume_payload": None,
        "is_ultra_long_conversation": False,
    }
    for frozen_key in REBUILD_FROZEN_KEYS:
        meta_updates[frozen_key] = None
    target_manager.update_conversation_metadata(conversation_id, meta_updates)
    # 同步内存中的 metadata，清除 frozen 缓存
    try:
        if getattr(cm, "current_conversation_id", None) == conversation_id and isinstance(cm.conversation_metadata, dict):
            for frozen_key in REBUILD_FROZEN_KEYS:
                cm.conversation_metadata.pop(frozen_key, None)
            cm.conversation_metadata.update(meta_updates)
    except Exception:
        pass

    _emit(sender, "compression_finished", {
        "source_conversation_id": conversation_id,
        "conversation_id": conversation_id,
        "in_progress": False,
        "compact_file": relative_compact_path,
        "marked_count": marked_count,
        "compress_form": compress_form,
        "compress_behavior": compress_behavior,
        "job_id": job_id,
    })
    return {
        "success": True,
        "in_place": True,
        "compressed_conversation_id": conversation_id,
        "compact_file": relative_compact_path,
        "marked_count": marked_count,
        "compress_form": compress_form,
        "compress_behavior": compress_behavior,
        "summary_failed": bool(summary_fail_reason),
        "guide_message": guide_message,
    }
