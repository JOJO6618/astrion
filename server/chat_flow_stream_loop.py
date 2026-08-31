from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, Optional

from config.model_profiles import get_model_profile

from utils.token_usage import extract_usage_payload

from .utils_common import debug_log, log_backend_chunk
from .chat_flow_runner_helpers import extract_intent_from_partial
from .chat_flow_task_support import wait_retry_delay, cancel_pending_tools
from .state import get_stop_flag, clear_stop_flag

from modules.i18n import tr


async def run_streaming_attempts(*, web_terminal, messages, tools, sender, client_sid: str, username: str, conversation_id: Optional[str], current_iteration: int, max_api_retries: int, retry_delay_seconds: int, detected_tool_intent: Dict[str, str], full_response: str, tool_calls: list, current_thinking: str, detected_tools: Dict[str, str], last_usage_payload, in_thinking: bool, thinking_started: bool, thinking_ended: bool, text_started: bool, text_has_content: bool, text_streaming: bool, text_chunk_index: int, last_text_chunk_time, chunk_count: int, reasoning_chunks: int, content_chunks: int, tool_chunks: int, last_finish_reason: Optional[str], accumulated_response: str) -> Dict[str, Any]:
    api_error = None
    tool_call_stream_active = False
    for api_attempt in range(max_api_retries + 1):
        api_error = None
        if api_attempt > 0:
            full_response = ""
            tool_calls = []
            current_thinking = ""
            detected_tools = {}
            last_usage_payload = None
            in_thinking = False
            thinking_started = False
            thinking_ended = False
            text_started = False
            text_has_content = False
            text_streaming = False
            text_chunk_index = 0
            last_text_chunk_time = None
            chunk_count = 0
            reasoning_chunks = 0
            content_chunks = 0
            tool_chunks = 0
            last_finish_reason = None
            tool_call_stream_active = False

        # 通知前端：API 请求已发出、尚未收到首个响应（每次重试前都会重新触发）。
        # 前端据此驱动状态形象的「等待 API 响应…」文案；响应开始（thinking_start/
        # text_start/tool_preparing）或 error/任务终结时由前端清除。
        sender('api_request_start', {
            'attempt': api_attempt + 1,
            'max_attempts': max_api_retries + 1,
        })

        # 收集流式响应
        async for chunk in web_terminal.api_client.chat(messages, tools, stream=True):
            chunk_count += 1

            # 检查停止标志
            client_stop_info = get_stop_flag(client_sid, username, include_user=False)
            if client_stop_info:
                stop_requested = client_stop_info.get('stop', False) if isinstance(client_stop_info, dict) else client_stop_info
                if stop_requested:
                    debug_log(f"检测到停止请求，中断流处理")
                    cancel_pending_tools(tool_calls_list=tool_calls, sender=sender, messages=messages)
                    sender('task_stopped', {
                        'message': tr("tool_loop.cancelled_by_user"),
                        'reason': 'user_stop'
                    })
                    clear_stop_flag(client_sid, username)
                    return {
                        "stopped": True,
                        "full_response": full_response,
                        "tool_calls": tool_calls,
                        "current_thinking": current_thinking,
                        "detected_tools": detected_tools,
                        "last_usage_payload": last_usage_payload,
                        "in_thinking": in_thinking,
                        "thinking_started": thinking_started,
                        "thinking_ended": thinking_ended,
                        "text_started": text_started,
                        "text_has_content": text_has_content,
                        "text_streaming": text_streaming,
                        "text_chunk_index": text_chunk_index,
                        "last_text_chunk_time": last_text_chunk_time,
                        "chunk_count": chunk_count,
                        "reasoning_chunks": reasoning_chunks,
                        "content_chunks": content_chunks,
                        "tool_chunks": tool_chunks,
                        "last_finish_reason": last_finish_reason,
                        "accumulated_response": accumulated_response,
                    }

            if isinstance(chunk, dict) and chunk.get("error"):
                api_error = chunk.get("error")
                break

            # 尽可能从流式 chunk 的各种位置提取 token usage（不按模型名做特例）
            usage_info = extract_usage_payload(chunk)
            if usage_info:
                last_usage_payload = usage_info

            if "choices" not in chunk:
                debug_log(f"Chunk {chunk_count}: 无choices字段")
                continue
            if not chunk.get("choices"):
                debug_log(f"Chunk {chunk_count}: choices为空列表")
                continue
            choice = chunk["choices"][0]
            delta = choice.get("delta", {})
            finish_reason = choice.get("finish_reason")
            if finish_reason:
                last_finish_reason = finish_reason

            # 处理思考内容（兼容 reasoning_content / reasoning_details）
            reasoning_content = ""
            if "reasoning_content" in delta:
                reasoning_content = delta.get("reasoning_content") or ""
            elif "reasoning_details" in delta:
                details = delta.get("reasoning_details")
                if isinstance(details, list):
                    parts = []
                    for item in details:
                        if isinstance(item, dict):
                            text = item.get("text")
                            if text:
                                parts.append(text)
                    if parts:
                        reasoning_content = "".join(parts)
            if reasoning_content:
                reasoning_chunks += 1
                debug_log(f"  思考内容 #{reasoning_chunks}: {len(reasoning_content)} 字符")

                if not thinking_started:
                    in_thinking = True
                    thinking_started = True
                    sender('thinking_start', {})
                    await asyncio.sleep(0.05)

                current_thinking += reasoning_content
                sender('thinking_chunk', {'content': reasoning_content})

            # 收集工具调用 - 实时发送准备状态
            delta_tool_calls = delta.get("tool_calls")
            if isinstance(delta_tool_calls, list) and delta_tool_calls:
                tool_call_stream_active = True
                tool_chunks += 1
                for tc in delta_tool_calls:
                    found = False
                    for existing in tool_calls:
                        if existing.get("index") == tc.get("index"):
                            if "function" in tc and "arguments" in tc["function"]:
                                arg_chunk = tc["function"]["arguments"]
                                existing_fn = existing.get("function", {})
                                existing_args = existing_fn.get("arguments", "")
                                existing_fn["arguments"] = (existing_args or "") + (arg_chunk or "")
                                existing["function"] = existing_fn

                                combined_args = existing_fn.get("arguments", "")
                                tool_id = existing.get("id") or tc.get("id")
                                tool_name = (
                                    existing_fn.get("name")
                                    or tc.get("function", {}).get("name", "")
                                )
                                intent_value = extract_intent_from_partial(combined_args)
                                if (
                                    intent_value
                                    and tool_id
                                    and detected_tool_intent.get(tool_id) != intent_value
                                ):
                                    detected_tool_intent[tool_id] = intent_value
                                    debug_log(f"[intent] 增量提取 {tool_name}: {intent_value}")
                                    sender('tool_intent', {
                                        'id': tool_id,
                                        'name': tool_name,
                                        'intent': intent_value,
                                        'conversation_id': conversation_id
                                    })
                                    debug_log(f"    发送工具意图: {tool_name} -> {intent_value}")
                                    await asyncio.sleep(0.01)
                            found = True
                            break

                    if not found and tc.get("id"):
                        tool_id = tc["id"]
                        tool_name = tc.get("function", {}).get("name", "")
                        arguments_str = tc.get("function", {}).get("arguments", "") or ""

                        # 新工具检测到，立即发送准备事件
                        if tool_id not in detected_tools and tool_name:
                            detected_tools[tool_id] = tool_name

                            # 尝试提前提取 intent
                            intent_value = None
                            if arguments_str:
                                intent_value = extract_intent_from_partial(arguments_str)
                                if intent_value:
                                    detected_tool_intent[tool_id] = intent_value
                                    debug_log(f"[intent] 预提取 {tool_name}: {intent_value}")

                            # 立即发送工具准备中事件
                            debug_log(f"[tool] 准备调用 {tool_name} (id={tool_id}) intent={intent_value or '-'}")
                            sender('tool_preparing', {
                                'id': tool_id,
                                'name': tool_name,
                                'message': tr("stream_loop.preparing_tool", tool=tool_name),
                                'intent': intent_value,
                                'conversation_id': conversation_id
                            })
                            debug_log(f"    发送工具准备事件: {tool_name}")
                            await asyncio.sleep(0.1)

                        tool_calls.append({
                            "id": tool_id,
                            "index": tc.get("index"),
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": arguments_str
                            }
                        })
                        # 尝试从增量参数中抽取 intent，并单独推送
                        if tool_id and arguments_str:
                            intent_value = extract_intent_from_partial(arguments_str)
                            if intent_value and detected_tool_intent.get(tool_id) != intent_value:
                                detected_tool_intent[tool_id] = intent_value
                                sender('tool_intent', {
                                    'id': tool_id,
                                    'name': tool_name,
                                    'intent': intent_value,
                                    'conversation_id': conversation_id
                                })
                                debug_log(f"    发送工具意图: {tool_name} -> {intent_value}")
                                await asyncio.sleep(0.01)
                        debug_log(f"    新工具: {tool_name}")

            # 处理正常内容
            if "content" in delta:
                content = delta["content"]
                if content:
                    # 某些供应商在 tool_calls 流式阶段会把参数碎片误放进 content。
                    # 一旦进入工具调用流，抑制正文 chunk，避免 write_file 参数泄露到前端。
                    if tool_call_stream_active:
                        debug_log(
                            "  抑制可疑正文chunk（tool_call阶段）: "
                            f"{repr((content or '')[:100])}"
                        )
                        continue

                    content_chunks += 1
                    debug_log(f"  正式内容 #{content_chunks}: {repr(content[:100] if content else 'None')}")

                    if in_thinking and not thinking_ended:
                        in_thinking = False
                        thinking_ended = True
                        sender('thinking_end', {'full_content': current_thinking})
                        await asyncio.sleep(0.1)

                    if not text_started:
                        text_started = True
                        text_streaming = True
                        sender('text_start', {})
                        debug_log("模型输出了内容")
                        await asyncio.sleep(0.05)

                    full_response += content
                    accumulated_response += content
                    text_has_content = True
                    emit_time = time.time()
                    elapsed = 0.0 if last_text_chunk_time is None else emit_time - last_text_chunk_time
                    last_text_chunk_time = emit_time
                    text_chunk_index += 1
                    log_backend_chunk(
                        conversation_id,
                        current_iteration,
                        text_chunk_index,
                        elapsed,
                        len(content),
                        content[:32]
                    )
                    sender('text_chunk', {
                        'content': content,
                        'index': text_chunk_index,
                        'elapsed': elapsed
                    })

        # 检查是否被停止
        client_stop_info = get_stop_flag(client_sid, username, include_user=False)
        if client_stop_info:
            stop_requested = client_stop_info.get('stop', False) if isinstance(client_stop_info, dict) else client_stop_info
            if stop_requested:
                debug_log("任务在流处理完成后检测到停止状态")
                sender('task_stopped', {
                    'message': tr("tool_loop.cancelled_by_user"),
                    'reason': 'user_stop'
                })
                cancel_pending_tools(tool_calls_list=tool_calls, sender=sender, messages=messages)
                clear_stop_flag(client_sid, username)
                return {
                    "stopped": True,
                    "full_response": full_response,
                    "tool_calls": tool_calls,
                    "current_thinking": current_thinking,
                    "detected_tools": detected_tools,
                    "last_usage_payload": last_usage_payload,
                    "in_thinking": in_thinking,
                    "thinking_started": thinking_started,
                    "thinking_ended": thinking_ended,
                    "text_started": text_started,
                    "text_has_content": text_has_content,
                    "text_streaming": text_streaming,
                    "text_chunk_index": text_chunk_index,
                    "last_text_chunk_time": last_text_chunk_time,
                    "chunk_count": chunk_count,
                    "reasoning_chunks": reasoning_chunks,
                    "content_chunks": content_chunks,
                    "tool_chunks": tool_chunks,
                    "last_finish_reason": last_finish_reason,
                    "accumulated_response": accumulated_response,
                }

        # === API响应完成后只计算输出token ===
        # 断流作废轮不计 usage：本论响应不完整，重试成功后会按新一轮 usage 计数，
        # 若在 error 分支判断之前 apply 会导致同一回答重复计数。
        if last_usage_payload and not api_error:
            try:
                web_terminal.context_manager.apply_usage_statistics(last_usage_payload)
                debug_log(
                    f"Usage统计: prompt={last_usage_payload.get('prompt_tokens', 0)}, "
                    f"completion={last_usage_payload.get('completion_tokens', 0)}, "
                    f"total={last_usage_payload.get('total_tokens', 0)}"
                )
            except Exception as e:
                debug_log(f"Usage统计更新失败: {e}")
        else:
            debug_log("未获取到usage字段，跳过token统计更新")


        if api_error:
            try:
                debug_log(f"API错误原始数据: {json.dumps(api_error, ensure_ascii=False)}")
            except Exception:
                debug_log(f"API错误原始数据(不可序列化): {repr(api_error)}")
            error_message = ""
            error_status = None
            error_type = None
            error_code = None
            error_text = ""
            request_dump = None
            error_base_url = None
            error_model_id = None
            if isinstance(api_error, dict):
                error_status = api_error.get("status_code")
                error_type = api_error.get("error_type") or api_error.get("type")
                error_code = api_error.get("error_code") or api_error.get("code")
                error_text = api_error.get("error_text") or ""
                error_message = (
                    api_error.get("error_message")
                    or api_error.get("message")
                    or error_text
                    or ""
                )
                request_dump = api_error.get("request_dump")
                error_base_url = api_error.get("base_url")
                error_model_id = api_error.get("model_id")
            elif isinstance(api_error, str):
                error_message = api_error
            if not error_message:
                if error_status:
                    error_message = f"API 请求失败（HTTP {error_status}）"
                else:
                    error_message = "API 请求失败"
            # 重试判定：网络类断流（status_code 为空：连接错误/超时/远端断开，
            # 区别于 HTTP 业务错误）允许「有接收」重试——半截内容只存在于局部变量
            # 与前端，后端历史与磁盘均未写入，清除重来无副作用；HTTP 业务错误
            # （4xx 等，实际上不可能有接收）保持原逻辑仅零接收重试。
            has_partial_output = bool(full_response or tool_calls or current_thinking)
            is_network_error = error_status is None
            can_retry = api_attempt < max_api_retries and (is_network_error or not has_partial_output)
            sender('error', {
                'message': error_message,
                'status_code': error_status,
                'error_type': error_type,
                'error_code': error_code,
                'error_text': error_text,
                'request_dump': request_dump,
                'base_url': error_base_url,
                'model_id': error_model_id,
                'retry': bool(can_retry),
                'retry_in': retry_delay_seconds if can_retry else None,
                'attempt': api_attempt + 1,
                'max_attempts': max_api_retries + 1
            })
            if can_retry:
                if has_partial_output:
                    # 「清除重来」：通知前端清掉本轮 attempt 已渲染的半截内容
                    # （思考块含已闭合的 / 文本 / preparing 工具条目），重试的新内容
                    # 将由新一轮 thinking_start/text_start/tool_preparing 重新推送。
                    # 后端内存历史与磁盘均无半截状态，无需清理。
                    sender('stream_reset', {
                        'reason': 'stream_disconnected',
                        'attempt': api_attempt + 2,
                        'max_attempts': max_api_retries + 1,
                        'retry_in': retry_delay_seconds,
                    })
                try:
                    profile = get_model_profile(getattr(web_terminal, "model_key", None))
                    web_terminal.apply_model_profile(profile)
                except Exception as exc:
                    debug_log(f"重试前更新模型配置失败: {exc}")
                cancelled = await wait_retry_delay(delay_seconds=retry_delay_seconds, client_sid=client_sid, username=username, sender=sender, get_stop_flag=get_stop_flag, clear_stop_flag=clear_stop_flag)
                if cancelled:
                    return {
                        "stopped": True,
                        "full_response": full_response,
                        "tool_calls": tool_calls,
                        "current_thinking": current_thinking,
                        "detected_tools": detected_tools,
                        "last_usage_payload": last_usage_payload,
                        "in_thinking": in_thinking,
                        "thinking_started": thinking_started,
                        "thinking_ended": thinking_ended,
                        "text_started": text_started,
                        "text_has_content": text_has_content,
                        "text_streaming": text_streaming,
                        "text_chunk_index": text_chunk_index,
                        "last_text_chunk_time": last_text_chunk_time,
                        "chunk_count": chunk_count,
                        "reasoning_chunks": reasoning_chunks,
                        "content_chunks": content_chunks,
                        "tool_chunks": tool_chunks,
                        "last_finish_reason": last_finish_reason,
                        "accumulated_response": accumulated_response,
                    }
                continue
            cancel_pending_tools(tool_calls_list=tool_calls, sender=sender, messages=messages)
            return {
                "stopped": True,
                "full_response": full_response,
                "tool_calls": tool_calls,
                "current_thinking": current_thinking,
                "detected_tools": detected_tools,
                "last_usage_payload": last_usage_payload,
                "in_thinking": in_thinking,
                "thinking_started": thinking_started,
                "thinking_ended": thinking_ended,
                "text_started": text_started,
                "text_has_content": text_has_content,
                "text_streaming": text_streaming,
                "text_chunk_index": text_chunk_index,
                "last_text_chunk_time": last_text_chunk_time,
                "chunk_count": chunk_count,
                "reasoning_chunks": reasoning_chunks,
                "content_chunks": content_chunks,
                "tool_chunks": tool_chunks,
                "last_finish_reason": last_finish_reason,
                "accumulated_response": accumulated_response,
            }
        break

    return {
        "stopped": False,
        "full_response": full_response,
        "tool_calls": tool_calls,
        "current_thinking": current_thinking,
        "detected_tools": detected_tools,
        "last_usage_payload": last_usage_payload,
        "in_thinking": in_thinking,
        "thinking_started": thinking_started,
        "thinking_ended": thinking_ended,
        "text_started": text_started,
        "text_has_content": text_has_content,
        "text_streaming": text_streaming,
        "text_chunk_index": text_chunk_index,
        "last_text_chunk_time": last_text_chunk_time,
        "chunk_count": chunk_count,
        "reasoning_chunks": reasoning_chunks,
        "content_chunks": content_chunks,
        "tool_chunks": tool_chunks,
        "last_finish_reason": last_finish_reason,
        "accumulated_response": accumulated_response,
    }
