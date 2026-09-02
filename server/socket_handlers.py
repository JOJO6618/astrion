from __future__ import annotations
import asyncio, time, json, re
from typing import Dict, Any
from flask import request
from flask_socketio import emit, join_room, leave_room, disconnect

from .extensions import socketio
from modules.i18n import tr
from .auth_helpers import get_current_username, resolve_admin_policy
from .context import (
    get_terminal_for_sid,
    ensure_conversation_loaded,
    reset_system_state,
    get_user_resources,
)
from .utils_common import debug_log, log_frontend_chunk, log_streaming_debug_entry
from .state import connection_users, stop_flags, terminal_rooms, pending_socket_tokens, user_manager, get_stop_flag, set_stop_flag, clear_stop_flag
from .usage import record_user_activity
from .chat_flow import start_chat_task
from .security import consume_socket_token, prune_socket_tokens
from config import OUTPUT_FORMATS, AGENT_VERSION
from config.model_profiles import model_supports_image, model_supports_video

@socketio.on('connect')
def handle_connect(auth):
    """客户端连接"""
    debug_log(f"[WebSocket] 客户端连接: {request.sid}")
    username = get_current_username()
    token_value = (auth or {}).get('socket_token') if isinstance(auth, dict) else None
    if not username or not consume_socket_token(token_value, username):
        emit('error', {'message': tr("socket.not_logged_in")})
        disconnect()
        return
    
    emit('connected', {'status': 'Connected to server'})
    connection_users[request.sid] = username
    
    # 清理可能存在的停止标志和状态
    stop_flags.pop(request.sid, None)
    # 将旧的 username 级别任务映射到新的 sid，便于重新停止
    user_entry = get_stop_flag(None, username)
    if user_entry:
        set_stop_flag(request.sid, username, user_entry)
    
    join_room(f"user_{username}")
    join_room(f"user_{username}_terminal")
    if request.sid not in terminal_rooms:
        terminal_rooms[request.sid] = set()
    terminal_rooms[request.sid].update({f"user_{username}", f"user_{username}_terminal"})
    
    terminal, workspace = get_user_resources(username)
    if terminal:
        reset_system_state(terminal)
        emit('system_ready', {
            'project_path': str(workspace.project_path),
            'thinking_mode': bool(getattr(terminal, "thinking_mode", False)),
            'version': AGENT_VERSION
        }, room=request.sid)
        
        if terminal.terminal_manager:
            terminals = terminal.terminal_manager.get_terminal_list()
            emit('terminal_list_update', {
                'terminals': terminals,
                'active': terminal.terminal_manager.active_terminal
            }, room=request.sid)
            
            if terminal.terminal_manager.active_terminal:
                for name, term in terminal.terminal_manager.terminals.items():
                    emit('terminal_started', {
                        'session': name,
                        'working_dir': str(term.working_dir),
                        'shell': term.shell_command,
                        'time': term.start_time.isoformat() if term.start_time else None
                    }, room=request.sid)

@socketio.on('disconnect')
def handle_disconnect():
    """客户端断开"""
    debug_log(f"[WebSocket] 客户端断开: {request.sid}")
    username = connection_users.pop(request.sid, None)
    # 若同一用户仍有其他活跃连接，不因断开而停止任务
    has_other_connection = False
    if username:
        for sid, user in connection_users.items():
            if user == username:
                has_other_connection = True
                break

    # 检查是否有通过 REST API 创建的运行中任务
    # 如果有，说明使用轮询模式，不应该停止任务
    has_rest_api_task = False
    if username and not has_other_connection:
        try:
            from .tasks import task_manager
            running_tasks = [t for t in task_manager.list_tasks(username) if t.status == "running"]
            if running_tasks:
                has_rest_api_task = True
                debug_log(f"[WebSocket] 用户 {username} 有运行中的 REST API 任务，不停止")
        except Exception as e:
            debug_log(f"[WebSocket] 检查 REST API 任务失败: {e}")

    task_info = get_stop_flag(request.sid, username)
    # 只有在没有其他连接且没有 REST API 任务时才停止
    if isinstance(task_info, dict) and not has_other_connection and not has_rest_api_task:
        task_info['stop'] = True
        pending_task = task_info.get('task')
        if pending_task and not pending_task.done():
            debug_log(f"disconnect: cancel task for {request.sid}")
            pending_task.cancel()
        terminal = task_info.get('terminal')
        if terminal:
            reset_system_state(terminal)

    # 清理停止标志（只清理 sid 级别的，不清理 user 级别的）
    if request.sid in stop_flags:
        stop_flags.pop(request.sid, None)

    # 从所有房间移除
    for room in list(terminal_rooms.get(request.sid, [])):
        leave_room(room)
    if request.sid in terminal_rooms:
        del terminal_rooms[request.sid]

    if username:
        leave_room(f"user_{username}")
        leave_room(f"user_{username}_terminal")

@socketio.on('stop_task')
def handle_stop_task():
    """处理停止任务请求"""
    debug_log(f"[停止] 收到停止请求: {request.sid}")
    username = connection_users.get(request.sid)
    task_info = get_stop_flag(request.sid, username)
    if not isinstance(task_info, dict):
        task_info = {'stop': False, 'task': None, 'terminal': None}
    # 标记停止标志，让任务内部检测并优雅停止
    task_info['stop'] = True
    # 注释掉直接取消任务，改为通过停止标志让任务内部处理
    # pending_task = task_info.get('task')
    # if pending_task and not pending_task.done():
    #     debug_log(f"正在取消任务: {request.sid}")
    #     pending_task.cancel()
    debug_log(f"设置停止标志: {request.sid}")
    if task_info.get('terminal'):
        reset_system_state(task_info['terminal'])
    set_stop_flag(request.sid, username, task_info)

    emit('stop_requested', {
        'message': tr("socket.stop_received")
    })

@socketio.on('terminal_subscribe')
def handle_terminal_subscribe(data):
    """订阅终端事件"""
    session_name = data.get('session')
    subscribe_all = data.get('all', False)

    conv_id = (data.get('conversation_id') or '').strip() or None
    username, terminal, _ = get_terminal_for_sid(request.sid, conversation_id=conv_id)
    if not username or not terminal or not terminal.terminal_manager:
        emit('error', {'message': 'Terminal system not initialized'})
        return
    policy = resolve_admin_policy(user_manager.get_user(username))
    if policy.get("ui_blocks", {}).get("block_realtime_terminal"):
        emit('error', {'message': tr("socket.terminal_disabled")})
        return
    
    if request.sid not in terminal_rooms:
        terminal_rooms[request.sid] = set()
    
    if subscribe_all:
        # 订阅所有终端事件
        room_name = f"user_{username}_terminal"
        join_room(room_name)
        terminal_rooms[request.sid].add(room_name)
        debug_log(f"[Terminal] {request.sid} 订阅所有终端事件")
        
        # 发送当前终端状态
        emit('terminal_subscribed', {
            'type': 'all',
            'terminals': terminal.terminal_manager.get_terminal_list()
        })
    elif session_name:
        # 订阅特定终端会话
        room_name = f'user_{username}_terminal_{session_name}'
        join_room(room_name)
        terminal_rooms[request.sid].add(room_name)
        debug_log(f"[Terminal] {request.sid} 订阅终端: {session_name}")
        
        # 发送该终端的当前输出
        output_result = terminal.terminal_manager.get_terminal_output(session_name, 100)
        if output_result['success']:
            emit('terminal_history', {
                'session': session_name,
                'output': output_result['output']
            })

@socketio.on('terminal_unsubscribe')
def handle_terminal_unsubscribe(data):
    """取消订阅终端事件"""
    session_name = data.get('session')
    username = connection_users.get(request.sid)
    
    if session_name:
        room_name = f'user_{username}_terminal_{session_name}' if username else f'terminal_{session_name}'
        leave_room(room_name)
        if request.sid in terminal_rooms:
            terminal_rooms[request.sid].discard(room_name)
        debug_log(f"[Terminal] {request.sid} 取消订阅终端: {session_name}")

@socketio.on('get_terminal_output')
def handle_get_terminal_output(data):
    """获取终端输出历史"""
    session_name = data.get('session')
    lines = data.get('lines', 50)

    conv_id = (data.get('conversation_id') or '').strip() or None
    username, terminal, _ = get_terminal_for_sid(request.sid, conversation_id=conv_id)
    if not terminal or not terminal.terminal_manager:
        emit('error', {'message': 'Terminal system not initialized'})
        return
    policy = resolve_admin_policy(user_manager.get_user(username))
    if policy.get("ui_blocks", {}).get("block_realtime_terminal"):
        emit('error', {'message': tr("socket.terminal_disabled")})
        return
    
    result = terminal.terminal_manager.get_terminal_output(session_name, lines)
    
    if result['success']:
        emit('terminal_output_history', {
            'session': session_name,
            'output': result['output'],
            'is_interactive': result.get('is_interactive', False),
            'last_command': result.get('last_command', ''),
            'last_event_time': result.get('last_event_time')
        })
    else:
        emit('error', {'message': result['error']})

@socketio.on('send_message')
def handle_message(data):
    """
    【已废弃】WebSocket 聊天入口，已由 REST API (/api/tasks) 替代

    保留此处理器用于向后兼容，但建议前端使用 REST API 轮询模式。
    新的架构优势：
    - 支持分布式部署（任务存储可扩展为 Redis）
    - 更好的错误恢复（页面刷新后可恢复任务状态）
    - 减少 WebSocket 连接压力
    """
    username, terminal, workspace = get_terminal_for_sid(request.sid)
    if not terminal:
        emit('error', {'message': 'System not initialized'})
        return

    # 返回废弃提示
    emit('error', {
        'message': tr("socket.ws_chat_deprecated"),
        'code': 'DEPRECATED',
        'migration_guide': tr("socket.ws_migration_guide")
    })
    return

    # 以下代码保留用于紧急回退
    # ========================================
    message = (data.get('message') or '').strip()
    images = data.get('images') or []
    videos = data.get('videos') or []
    if not message and not images and not videos:
        emit('error', {'message': tr("socket.empty_message")})
        return
    current_model = getattr(terminal, "model_key", None) or ""
    if images and not model_supports_image(current_model):
        emit('error', {'message': tr("socket.image_not_supported")})
        return
    if videos and not model_supports_video(current_model):
        emit('error', {'message': tr("socket.video_not_supported")})
        return
    if images and videos:
        emit('error', {'message': tr("socket.image_video_separate")})
        return

    debug_log(f"[WebSocket] 收到消息: {message}")
    debug_log(f"\n{'='*80}\n新任务开始: {message}\n{'='*80}")
    record_user_activity(username)

    requested_conversation_id = data.get('conversation_id')
    try:
        conversation_id, created_new = ensure_conversation_loaded(terminal, requested_conversation_id)
    except RuntimeError as exc:
        emit('error', {'message': str(exc)})
        return
    try:
        conv_data = terminal.context_manager._get_conversation_manager_for_id(conversation_id).load_conversation(conversation_id) or {}
    except Exception:
        conv_data = {}
    title = conv_data.get('title', tr("conversation.default_title"))

    socketio.emit('conversation_resolved', {
        'conversation_id': conversation_id,
        'title': title,
        'created': created_new
    }, room=request.sid)

    if created_new:
        socketio.emit('conversation_list_update', {
            'action': 'created',
            'conversation_id': conversation_id
        }, room=f"user_{username}")
        socketio.emit('conversation_changed', {
            'conversation_id': conversation_id,
            'title': title
        }, room=request.sid)

    client_sid = request.sid

    def send_to_client(event_type, data):
        """发送消息到客户端"""
        socketio.emit(event_type, data, room=client_sid)

    # 模型活动事件：用于刷新"在线"心跳（回复/工具调用都算活动）
    activity_events = {
        'ai_message_start', 'thinking_start', 'thinking_chunk', 'thinking_end',
        'text_start', 'text_chunk', 'text_end',
        'tool_preparing', 'tool_start', 'update_action',
        'append_payload', 'modify_payload', 'system_message',
        'task_complete'
    }
    last_model_activity = 0.0

    def send_with_activity(event_type, data):
        """模型产生输出或调用工具时刷新活跃时间，防止长回复被误判下线。"""
        nonlocal last_model_activity
        if event_type in activity_events:
            now = time.time()
            # 轻量节流：1 秒内多次事件只记一次
            if now - last_model_activity >= 1.0:
                record_user_activity(username)
                last_model_activity = now
        send_to_client(event_type, data)

    # 传递客户端ID
    images = data.get('images') or []
    videos = data.get('videos') or []
    start_chat_task(terminal, message, images, send_with_activity, client_sid, workspace, username, videos)


# WS 客户端日志事件的轻量限流（防已认证用户洪泛写盘；username -> 时间戳滑窗）
_WS_CLIENT_LOG_LIMIT = 30  # 每 60 秒最多 30 条
_WS_CLIENT_LOG_WINDOW = 60.0
_ws_client_log_buckets: Dict[str, list] = {}
_WS_CLIENT_LOG_MAX_KEYS = 10000


def _ws_client_log_allowed(username: str) -> bool:
    now = time.time()
    if len(_ws_client_log_buckets) > _WS_CLIENT_LOG_MAX_KEYS:
        # 全局回收：清空过老桶（简单策略，防止桶表无限膨胀）
        for key in [k for k, v in _ws_client_log_buckets.items() if not v or now - v[-1] > _WS_CLIENT_LOG_WINDOW]:
            _ws_client_log_buckets.pop(key, None)
        if len(_ws_client_log_buckets) > _WS_CLIENT_LOG_MAX_KEYS:
            _ws_client_log_buckets.clear()
    bucket = _ws_client_log_buckets.setdefault(username, [])
    while bucket and now - bucket[0] > _WS_CLIENT_LOG_WINDOW:
        bucket.pop(0)
    if len(bucket) >= _WS_CLIENT_LOG_LIMIT:
        return False
    bucket.append(now)
    return True


@socketio.on('client_chunk_log')
def handle_client_chunk_log(data):
    """前端chunk日志上报"""
    username = connection_users.get(request.sid)
    if not username or not _ws_client_log_allowed(username):
        return
    conversation_id = data.get('conversation_id')
    chunk_index = int(data.get('index') or data.get('chunk_index') or 0)
    elapsed = float(data.get('elapsed') or 0.0)
    length = int(data.get('length') or len(data.get('content') or ""))
    client_ts = float(data.get('ts') or 0.0)
    log_frontend_chunk(conversation_id, chunk_index, elapsed, length, client_ts)


@socketio.on('client_stream_debug_log')
def handle_client_stream_debug_log(data):
    """前端流式调试日志"""
    username = connection_users.get(request.sid)
    if not username or not _ws_client_log_allowed(username):
        return
    if not isinstance(data, dict):
        return
    entry = dict(data)
    entry.setdefault('server_ts', time.time())
    log_streaming_debug_entry(entry)

# 在 web_server.py 中添加以下对话管理API接口
# 添加在现有路由之后，@socketio 事件处理之前

# ==========================================
# 对话管理API接口
# ==========================================


# conversation routes moved to server/conversation.py
