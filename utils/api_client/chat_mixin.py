# ========== api_client.py ==========
# utils/api_client.py - OpenAI-compatible API 客户端（支持Web模式）

import httpx
import json
import asyncio
import base64
import mimetypes
import os
from typing import List, Dict, Optional, AsyncGenerator, Any
from pathlib import Path
from datetime import datetime
from pathlib import Path
from typing import Tuple
try:
    from config import (
        OUTPUT_FORMATS,
        DEFAULT_RESPONSE_MAX_TOKENS,
        LOGS_DIR,
    )
except ImportError:
    import sys
    from pathlib import Path
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from config import (
        OUTPUT_FORMATS,
        DEFAULT_RESPONSE_MAX_TOKENS,
        LOGS_DIR,
    )

from utils.log_rotation import append_line, prune_dir



from utils.api_client.utils import _api_dump_enabled

class APIClientChatMixin:
    async def chat(
        self,
        messages: List[Dict],
        tools: Optional[List[Dict]] = None,
        stream: bool = True
    ) -> AsyncGenerator[Dict, None]:
        """
        异步调用 OpenAI-compatible API
        
        Args:
            messages: 消息列表
            tools: 工具定义列表
            stream: 是否流式输出
        
        Yields:
            响应内容块
        """
        # 检查API密钥
        if not self.api_key or self.api_key.startswith("your-"):
            self._print(f"{OUTPUT_FORMATS['error']} API密钥未配置，请检查模型配置")
            return
        
        # 决定是否使用思考模式（思考模式下每次请求都用思考配置）
        current_thinking_mode = self.get_current_thinking_mode()
        api_config = self._select_api_config(current_thinking_mode)
        headers = self._build_headers(api_config["api_key"])
        
        try:
            override_max = self.thinking_max_tokens if current_thinking_mode else self.fast_max_tokens
            if override_max is not None:
                max_tokens = int(override_max)
            else:
                max_tokens = int(DEFAULT_RESPONSE_MAX_TOKENS)
            if max_tokens <= 0:
                raise ValueError("max_tokens must be positive")
        except (TypeError, ValueError):
            max_tokens = 4096

        # 动态收缩 max_tokens，避免超过模型上下文窗口
        budget_max_context = self.max_context_tokens or self.default_context_window
        if budget_max_context and budget_max_context > 0:
            used_tokens = max(0, int(self.current_context_tokens or 0))
            available = budget_max_context - used_tokens
            if available <= 0:
                # 兜底：让上游错误处理，这里至少给1防止API报参数错误
                max_tokens = 1
            else:
                max_tokens = min(max_tokens, available)

        final_messages = self._merge_system_messages(messages)
        final_messages = self._sanitize_messages_for_model_capability(final_messages)
        final_messages = self._sanitize_message_fields_for_api(final_messages)
        payload = {
            "model": api_config["model_id"],
            "messages": final_messages,
            "stream": stream,
        }
        payload["max_tokens"] = max_tokens
        # 注入模型配置中的额外参数
        extra_params = self.thinking_extra_params if current_thinking_mode else self.fast_extra_params
        if extra_params:
            payload.update(extra_params)
        # 推理强度：思考模式下且模型支持时注入（与额外参数同级的平铺参数）
        if current_thinking_mode and self.supports_reasoning_effort and self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        # 将本次请求落盘，便于出错时快速定位
        try:
            self._debug_log({
                "event": "request_prepare",
                "model_key": self.model_key,
                "thinking_mode_flag": bool(self.thinking_mode),
                "current_call_use_thinking": bool(current_thinking_mode),
                "reasoning_effort": self.reasoning_effort,
                "api_base_url": api_config.get("base_url"),
                "api_model_id": api_config.get("model_id"),
                "payload_model": payload.get("model"),
                "payload_has_thinking": "thinking" in payload,
                "payload_thinking": payload.get("thinking"),
                "payload_enable_thinking": payload.get("enable_thinking"),
                "payload_max_tokens": payload.get("max_tokens"),
                "payload_max_completion_tokens": payload.get("max_completion_tokens"),
            })
        except Exception:
            pass
        dump_path = self._dump_request_payload(payload, api_config, headers)

        try:
            async with httpx.AsyncClient(http2=True, timeout=300) as client:
                if stream:
                    async with client.stream(
                        "POST",
                        f"{api_config['base_url']}/chat/completions",
                        json=payload,
                        headers=headers
                    ) as response:
                        # 检查响应状态
                        if response.status_code != 200:
                            error_bytes = await response.aread()
                            error_text = error_bytes.decode('utf-8', errors='ignore') if hasattr(error_bytes, 'decode') else str(error_bytes)
                            self.last_error_info = {
                                "status_code": response.status_code,
                                "error_text": error_text,
                                "error_type": None,
                                "error_message": None,
                                "request_dump": (str(dump_path) if dump_path else None),
                                "base_url": api_config.get("base_url"),
                                "model_id": api_config.get("model_id"),
                                "model_key": self.model_key
                            }
                            try:
                                parsed = json.loads(error_text)
                                err = parsed.get("error") if isinstance(parsed, dict) else {}
                                if isinstance(err, dict):
                                    self.last_error_info["error_type"] = err.get("type")
                                    self.last_error_info["error_message"] = err.get("message")
                            except Exception:
                                pass
                            self._debug_log({
                                "event": "http_error_stream",
                                "status_code": response.status_code,
                                "error_text": error_text,
                                "base_url": api_config.get("base_url"),
                                "model_id": api_config.get("model_id"),
                                "model_key": self.model_key,
                                "request_dump": (str(dump_path) if dump_path else None)
                            })
                            self._print(
                                f"{OUTPUT_FORMATS['error']} API请求失败 ({response.status_code}): {error_text} "
                                f"(base_url={api_config.get('base_url')}, model_id={api_config.get('model_id')})"
                            )
                            self._mark_request_error(dump_path, response.status_code, error_text)
                            yield {"error": self.last_error_info}
                            return
                            
                        async for line in response.aiter_lines():
                            if line.startswith("data:"):
                                json_str = line[5:].strip()
                                if json_str == "[DONE]":
                                    break
                                
                                try:
                                    data = json.loads(json_str)
                                    yield data
                                except json.JSONDecodeError:
                                    continue
                else:
                    response = await client.post(
                        f"{api_config['base_url']}/chat/completions",
                        json=payload,
                        headers=headers
                    )
                    if response.status_code != 200:
                        error_text = response.text
                        self.last_error_info = {
                            "status_code": response.status_code,
                            "error_text": error_text,
                            "error_type": None,
                            "error_message": None,
                            "request_dump": (str(dump_path) if dump_path else None),
                            "base_url": api_config.get("base_url"),
                            "model_id": api_config.get("model_id"),
                            "model_key": self.model_key
                        }
                        try:
                            parsed = response.json()
                            err = parsed.get("error") if isinstance(parsed, dict) else {}
                            if isinstance(err, dict):
                                self.last_error_info["error_type"] = err.get("type")
                                self.last_error_info["error_message"] = err.get("message")
                        except Exception:
                            pass
                        self._debug_log({
                            "event": "http_error",
                            "status_code": response.status_code,
                            "error_text": error_text,
                            "base_url": api_config.get("base_url"),
                            "model_id": api_config.get("model_id"),
                            "model_key": self.model_key,
                            "request_dump": (str(dump_path) if dump_path else None)
                        })
                        self._print(
                            f"{OUTPUT_FORMATS['error']} API请求失败 ({response.status_code}): {error_text} "
                            f"(base_url={api_config.get('base_url')}, model_id={api_config.get('model_id')})"
                        )
                        self._mark_request_error(dump_path, response.status_code, error_text)
                        yield {"error": self.last_error_info}
                        return
                    # 成功则清空错误状态
                    self.last_error_info = None
                    yield response.json()
                    
        except httpx.ConnectError as e:
            connect_detail = str(e).strip() or repr(e)
            self._print(
                f"{OUTPUT_FORMATS['error']} 无法连接到API服务器，请检查网络连接"
                f"（{connect_detail}）"
            )
            self.last_error_info = {
                "status_code": None,
                "error_text": "connect_error",
                "error_type": "connection_error",
                "error_message": f"无法连接到API服务器: {connect_detail}",
                "error_detail": connect_detail,
                "request_dump": (str(dump_path) if dump_path else None),
                "base_url": api_config.get("base_url"),
                "model_id": api_config.get("model_id"),
                "model_key": self.model_key
            }
            self._debug_log({
                "event": "connect_error",
                "status_code": None,
                "error_text": "connect_error",
                "error_detail": connect_detail,
                "base_url": api_config.get("base_url"),
                "model_id": api_config.get("model_id"),
                "model_key": self.model_key,
                "request_dump": (str(dump_path) if dump_path else None)
            })
            self._mark_request_error(dump_path, error_text=f"connect_error: {connect_detail}")
            yield {"error": self.last_error_info}
        except httpx.TimeoutException:
            self._print(f"{OUTPUT_FORMATS['error']} API请求超时")
            self.last_error_info = {
                "status_code": None,
                "error_text": "timeout",
                "error_type": "timeout",
                "error_message": "API请求超时",
                "request_dump": (str(dump_path) if dump_path else None),
                "base_url": api_config.get("base_url"),
                "model_id": api_config.get("model_id"),
                "model_key": self.model_key
            }
            self._debug_log({
                "event": "timeout",
                "status_code": None,
                "error_text": "timeout",
                "base_url": api_config.get("base_url"),
                "model_id": api_config.get("model_id"),
                "model_key": self.model_key,
                "request_dump": (str(dump_path) if dump_path else None)
            })
            self._mark_request_error(dump_path, error_text="timeout")
            yield {"error": self.last_error_info}
        except httpx.RemoteProtocolError as e:
            disconnect_detail = str(e).strip() or repr(e)
            self._print(f"{OUTPUT_FORMATS['error']} API服务器连接断开（{disconnect_detail}）")
            self.last_error_info = {
                "status_code": None,
                "error_text": disconnect_detail,
                "error_type": "connection_error",
                "error_message": f"API服务器连接断开: {disconnect_detail}",
                "request_dump": (str(dump_path) if dump_path else None),
                "base_url": api_config.get("base_url"),
                "model_id": api_config.get("model_id"),
                "model_key": self.model_key
            }
            self._debug_log({
                "event": "server_disconnected",
                "status_code": None,
                "error_text": disconnect_detail,
                "base_url": api_config.get("base_url"),
                "model_id": api_config.get("model_id"),
                "model_key": self.model_key,
                "request_dump": (str(dump_path) if dump_path else None)
            })
            self._mark_request_error(dump_path, error_text=f"server_disconnected: {disconnect_detail}")
            yield {"error": self.last_error_info}
        except Exception as e:
            error_text = str(e).strip() or repr(e)
            self._print(f"{OUTPUT_FORMATS['error']} API调用异常: {error_text}")
            self.last_error_info = {
                "status_code": None,
                "error_text": error_text,
                "error_type": "exception",
                "error_message": error_text,
                "request_dump": (str(dump_path) if dump_path else None),
                "base_url": api_config.get("base_url"),
                "model_id": api_config.get("model_id"),
                "model_key": self.model_key
            }
            self._debug_log({
                "event": "exception",
                "status_code": None,
                "error_text": error_text,
                "base_url": api_config.get("base_url"),
                "model_id": api_config.get("model_id"),
                "model_key": self.model_key,
                "request_dump": (str(dump_path) if dump_path else None)
            })
            self._mark_request_error(dump_path, error_text=error_text)
            yield {"error": self.last_error_info}

    async def chat_with_tools(
        self,
        messages: List[Dict],
        tools: List[Dict],
        tool_handler: callable
    ) -> str:
        """
        带工具调用的对话（支持多轮）
        
        Args:
            messages: 消息列表
            tools: 工具定义
            tool_handler: 工具处理函数
        
        Returns:
            最终回答
        """
        final_response = ""
        max_iterations = 200  # 最大迭代次数
        iteration = 0
        all_tool_results = []  # 记录所有工具调用结果

        while iteration < max_iterations:
            iteration += 1
            
            # 调用API（始终提供工具定义）
            full_response = ""
            tool_calls = []
            current_thinking = ""
            
            # 状态标志
            in_thinking = False
            thinking_printed = False

            async for chunk in self.chat(messages, tools, stream=True):
                if chunk.get("error"):
                    # 直接返回错误，让上层处理
                    err = chunk["error"]
                    self.last_error_info = err
                    err_msg = err.get("error_message") or err.get("error_text") or "API调用失败"
                    status = err.get("status_code")
                    self._print(f"{OUTPUT_FORMATS['error']} 模型API错误{f'({status})' if status is not None else ''}: {err_msg}")
                    return ""
                
                if "choices" not in chunk:
                    continue
                    
                delta = chunk["choices"][0].get("delta", {})
                
                # 处理思考内容
                reasoning_content = self._extract_reasoning_delta(delta)
                if reasoning_content:
                    if not in_thinking:
                        self._print("💭 [正在思考]\n", end="", flush=True)
                        in_thinking = True
                        thinking_printed = True
                    current_thinking += reasoning_content
                    self._print(reasoning_content, end="", flush=True)
                
                # 处理正常内容 - 独立的if，不是elif
                if "content" in delta:
                    content = delta["content"]
                    if content:  # 只处理非空内容
                        # 如果之前在输出思考，先结束思考输出
                        if in_thinking:
                            self._print("\n\n💭 [思考结束]\n\n", end="", flush=True)
                            in_thinking = False
                        full_response += content
                        self._print(content, end="", flush=True)
                
                # 收集工具调用 - 改进的拼接逻辑
                # 收集工具调用 - 修复JSON分片问题
                if "tool_calls" in delta:
                    for tool_call in delta["tool_calls"]:
                        tool_index = tool_call.get("index", 0)
                        
                        # 查找或创建对应索引的工具调用
                        existing_call = None
                        for existing in tool_calls:
                            if existing.get("index") == tool_index:
                                existing_call = existing
                                break
                        
                        if not existing_call and tool_call.get("id"):
                            # 创建新的工具调用
                            new_call = {
                                "id": tool_call.get("id"),
                                "index": tool_index,
                                "type": tool_call.get("type", "function"),
                                "function": {
                                    "name": tool_call.get("function", {}).get("name", ""),
                                    "arguments": ""
                                }
                            }
                            tool_calls.append(new_call)
                            existing_call = new_call
                        
                        # 安全地拼接arguments - 简单字符串拼接，不尝试JSON验证
                        if existing_call and "function" in tool_call and "arguments" in tool_call["function"]:
                            new_args = tool_call["function"]["arguments"]
                            if new_args:  # 只拼接非空内容
                                existing_call["function"]["arguments"] += new_args
            
            self._print("")  # 最终换行
            
            # 如果思考还没结束（只调用工具没有文本），手动结束
            if in_thinking:
                self._print("\n💭 [思考结束]\n")
            
            # 如果没有工具调用，说明完成了
            if not tool_calls:
                if full_response:  # 有正常回复，任务完成
                    final_response = full_response
                    break
                elif iteration == 1:  # 第一次就没有工具调用也没有内容，可能有问题
                    self._print(f"{OUTPUT_FORMATS['warning']} 模型未返回内容")
                    break
            
            # 构建助手消息 - 始终包含所有收集到的内容
            assistant_content_parts = []
            
            # 添加正式回复内容（如果有）
            if full_response:
                assistant_content_parts.append(full_response)
            
            # 添加工具调用说明
            if tool_calls:
                tool_names = [tc['function']['name'] for tc in tool_calls]
                assistant_content_parts.append(f"执行工具: {', '.join(tool_names)}")
            
            # 合并所有内容
            assistant_content = "\n".join(assistant_content_parts) if assistant_content_parts else "执行工具调用"
            
            assistant_message = {
                "role": "assistant",
                "content": assistant_content,
                "tool_calls": tool_calls
            }
            if current_thinking:
                assistant_message["reasoning_content"] = current_thinking
            messages.append(assistant_message)
            
            # 执行所有工具调用 - 使用鲁棒的参数解析
            for tool_call in tool_calls:
                function_name = tool_call["function"]["name"]
                arguments_str = tool_call["function"]["arguments"]
                
                # 使用改进的参数解析方法，增强JSON修复能力
                success, arguments, error_msg = self._safe_tool_arguments_parse(arguments_str, function_name)
                
                if not success:
                    self._print(f"{OUTPUT_FORMATS['error']} 工具参数解析失败: {error_msg}")
                    self._print(f"  工具名称: {function_name}")
                    self._print(f"  参数长度: {len(arguments_str)} 字符")
                    
                    # 返回详细的错误信息给模型
                    error_response = {
                        "success": False,
                        "error": error_msg,
                        "tool_name": function_name,
                        "arguments_length": len(arguments_str),
                        "suggestion": "请检查参数格式或减少参数长度后重试"
                    }
                    
                    # 如果参数过长，提供分块建议
                    if len(arguments_str) > 10000:
                        error_response["suggestion"] = "参数过长，建议分块处理或使用更简洁的内容"
                    
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "name": function_name,
                        "content": json.dumps(error_response, ensure_ascii=False)
                    })
                    
                    # 记录失败的调用，防止死循环检测失效
                    all_tool_results.append({
                        "tool": function_name,
                        "args": {"parse_error": error_msg, "length": len(arguments_str)},
                        "result": f"参数解析失败: {error_msg}"
                    })
                    continue
                
                self._print(f"\n{OUTPUT_FORMATS['action']} 调用工具: {function_name}")
                
                tool_result = await tool_handler(function_name, arguments)
                
                # 解析工具结果，提取关键信息
                result_data = None
                try:
                    result_data = json.loads(tool_result)
                    if function_name == "read_file":
                        tool_result_msg = self._format_read_file_result(result_data)
                    else:
                        tool_result_msg = tool_result
                except Exception:
                    tool_result_msg = tool_result

                tool_message_content = tool_result_msg
                if (
                    isinstance(result_data, dict)
                    and result_data.get("success") is not False
                ):
                    if function_name == "view_image":
                        img_path = result_data.get("path")
                        if img_path:
                            text_part = tool_result_msg if isinstance(tool_result_msg, str) else ""
                            tool_message_content = self._build_content_with_images(text_part, [img_path])
                    elif function_name == "view_video":
                        video_path = result_data.get("path")
                        if video_path:
                            text_part = tool_result_msg if isinstance(tool_result_msg, str) else ""
                            tool_message_content = self._build_content_with_images(text_part, [], [video_path])
                
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "name": function_name,
                    "content": tool_message_content
                })
                
                # 记录工具结果
                all_tool_results.append({
                    "tool": function_name,
                    "args": arguments,
                    "result": tool_result_msg
                })
            
            # 如果连续多次调用同样的工具，可能陷入循环
            if len(all_tool_results) >= 8:
                recent_tools = [r["tool"] for r in all_tool_results[-8:]]
                if len(set(recent_tools)) == 1:  # 最近8次都是同一个工具
                    self._print(f"\n{OUTPUT_FORMATS['warning']} 检测到重复操作，停止执行")
                    break
        
        if iteration >= max_iterations:
            self._print(f"\n{OUTPUT_FORMATS['warning']} 达到最大迭代次数限制")
        
        return final_response

    async def simple_chat(self, messages: List[Dict]) -> tuple:
        """
        简单对话（无工具调用）
        
        Args:
            messages: 消息列表
        
        Returns:
            (模型回答, 思考内容)
        """
        full_response = ""
        thinking_content = ""
        in_thinking = False

        try:
            async for chunk in self.chat(messages, tools=None, stream=True):
                if chunk.get("error"):
                    err = chunk["error"]
                    self.last_error_info = err
                    err_msg = err.get("error_message") or err.get("error_text") or "API调用失败"
                    status = err.get("status_code")
                    self._print(f"{OUTPUT_FORMATS['error']} 模型API错误{f'({status})' if status is not None else ''}: {err_msg}")
                    return "", ""
                
                if "choices" not in chunk:
                    continue
                    
                delta = chunk["choices"][0].get("delta", {})
                
                # 处理思考内容
                reasoning_content = self._extract_reasoning_delta(delta)
                if reasoning_content:
                    if not in_thinking:
                        self._print("💭 [正在思考]\n", end="", flush=True)
                        in_thinking = True
                    thinking_content += reasoning_content
                    self._print(reasoning_content, end="", flush=True)
                
                # 处理正常内容 - 独立的if而不是elif
                if "content" in delta:
                    content = delta["content"]
                    if content:  # 只处理非空内容
                        if in_thinking:
                            self._print("\n\n💭 [思考结束]\n\n", end="", flush=True)
                            in_thinking = False
                        full_response += content
                        self._print(content, end="", flush=True)
            
            self._print("")  # 最终换行
            
            # 如果思考还没结束（极少情况），手动结束
            if in_thinking:
                self._print("\n💭 [思考结束]\n")
            
            # 如果没有收到任何响应
            if not full_response and not thinking_content:
                self._print(f"{OUTPUT_FORMATS['error']} API未返回任何内容，请检查API密钥和模型ID")
                return "", ""
                
        except Exception as e:
            self._print(f"{OUTPUT_FORMATS['error']} API调用失败: {e}")
            return "", ""
        
        return full_response, thinking_content
