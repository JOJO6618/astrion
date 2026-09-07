"""Gateway 独立进程验收检查体（由 test_runtime_standalone_lifecycle 以子进程方式调用）。

为什么必须是独立进程：config/paths.py 的 DATA_DIR / DEPLOY_CONFIG_DIR 等是
import 时固化的模块级常量；在 unittest discover 全量运行时，排在前面的测试模块
会先 import config 使常量固化，本文件顶部再设环境变量已无效——且被测代码会
落到用户真实运行态目录执行任务（不可接受）。子进程内 import 顺序完全可控，
隔离 100% 可靠。

用法：python test/runtime_standalone_checks.py <lifecycle|chain>
退出码 0 = 通过；断言失败/traceback 走 stderr，返回码非 0。
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

# ---- 隔离环境必须在 import 任何项目模块之前设置（config 在 import 时解析路径）----
_SMOKE_ROOT = Path(tempfile.mkdtemp(prefix="astrion_gw_smoke_"))
_SMOKE_WORKSPACE = _SMOKE_ROOT / "workspace"
_SMOKE_WORKSPACE.mkdir(parents=True, exist_ok=True)
(_SMOKE_ROOT / "config").mkdir(parents=True, exist_ok=True)

os.environ["ASTRION_DATA_ROOT"] = str(_SMOKE_ROOT)
os.environ["DEPLOY_CONFIG_DIR"] = str(_SMOKE_ROOT / "config")
os.environ["TERMINAL_SANDBOX_MODE"] = "host"
# 隔离逃生门（审核 F4）：仓库根 .env 对 ASTRION_DATA_ROOT 有「.env 优先」的刻意覆盖，
# 测试进程必须显式禁用它，否则隔离目录会被 .env 值穿透（静默落到真实/clone 数据根）。
os.environ["ASTRION_IGNORE_DOTENV"] = "1"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

(_SMOKE_ROOT / "config" / "host_workspaces.json").write_text(
    json.dumps(
        {
            "default_workspace_id": "gwsmoke",
            "workspaces": [
                {"workspace_id": "gwsmoke", "label": "gwsmoke", "path": str(_SMOKE_WORKSPACE)}
            ],
        },
        ensure_ascii=False,
        indent=2,
    ),
    encoding="utf-8",
)

# 自包含模型配置（审核 F4）：装配需要至少一个已注册模型；指向 127.0.0.1:9（discard
# 端口，几乎必无监听）使模型调用以「连接拒绝」快速失败——不依赖 DNS/外部网络，
# 也不需要真实凭证。验收对象是装配与生命周期，不是模型响应。
(_SMOKE_ROOT / "config" / "custom_models.json").write_text(
    json.dumps(
        {
            "models": [
                {
                    "model_name": "fake-smoke-model",
                    "visible": True,
                    "url": "http://127.0.0.1:9",
                    "apikey": "fake-smoke-key",
                    "reasoning_capability": "fast,thinking",
                    "thinkmode_status": {"type": "param_toggle", "model_id": "fake-smoke-model"},
                }
            ]
        },
        ensure_ascii=False,
        indent=2,
    ),
    encoding="utf-8",
)

# 隔离生效断言（审核 F4）：config import 后路径常量必须落在隔离根内，
# 否则说明隔离被穿透（如 .env 覆盖）——宁可明确失败，绝不静默使用真实目录。
import config as _smoke_config  # noqa: E402

_assert_root = str(_SMOKE_ROOT.resolve())
assert str(Path(_smoke_config.DATA_DIR).resolve()).startswith(_assert_root), (
    f"隔离失败：DATA_DIR={_smoke_config.DATA_DIR} 不在 {_assert_root} 下"
)
assert str(Path(_smoke_config.DEPLOY_CONFIG_DIR).resolve()).startswith(_assert_root), (
    f"隔离失败：DEPLOY_CONFIG_DIR={_smoke_config.DEPLOY_CONFIG_DIR} 不在 {_assert_root} 下"
)


def _make_ctx(conversation_id=None, message="协议链路验收 ping"):
    from server.runtime import (
        InternalDirectives, RuntimeContext, TaskParams, TrustedPrincipal,
    )

    return RuntimeContext(
        principal=TrustedPrincipal(
            username="gw_smoke_user", workspace_id="gwsmoke", role="admin",
            host_mode=True, host_workspace_id="gwsmoke",
        ),
        params=TaskParams(message=message, conversation_id=conversation_id, run_mode="fast"),
        directives=InternalDirectives(),
    )


def _wait_terminal_state(task_id, timeout=30, cancel_on_event=None):
    """等待终态。事件流出现指定事件（默认首个事件）后主动取消——
    无网络环境下模型重试链路太长，取消让任务快速到达终态。
    cancel_on_event="api_request_start" 可确保 user 消息已写入历史后再取消。
    """
    from server.runtime import runtime_service

    deadline = time.time() + timeout
    cancelled = False
    while time.time() < deadline:
        rec = runtime_service.get_task("gw_smoke_user", task_id)
        if getattr(rec, "status", None) in {"succeeded", "failed", "stopped", "canceled"}:
            return rec
        if not cancelled:
            events, _, _, _meta = runtime_service.get_task_events("gw_smoke_user", task_id, 0)
            if events and (cancel_on_event is None or any(e.get("type") == cancel_on_event for e in events)):
                runtime_service.cancel_task("gw_smoke_user", task_id)
                cancelled = True
        time.sleep(0.5)
    return runtime_service.get_task("gw_smoke_user", task_id)


def check_lifecycle():
    """第 1 步验收：无 Flask app 的独立 Gateway 生命周期（G4/G13）。"""
    from flask import has_app_context

    assert not has_app_context(), "验收前提：无 Flask app 上下文"

    from server.runtime import runtime_service

    rec = runtime_service.create_task(
        _make_ctx(message="ping（独立生命周期验收，模型调用失败属预期）")
    )
    assert getattr(rec, "task_id", None), "create_task 应返回 task_id"
    task_id = rec.task_id

    from server.runtime import runtime_service as rs  # 单例别名，语义强调

    deadline = time.time() + 30
    saw_assembly = False  # 装配证据：api_request_start 事件（历史/请求构造完成，模型调用前）
    final_status = None
    assembly_error = None
    while time.time() < deadline:
        events, _next_offset, err, _meta = rs.get_task_events("gw_smoke_user", task_id, 0)
        assert err is None, f"get_task_events 应可读: {err}"
        event_types = {e.get("type") for e in (events or [])}
        # 装配失败（资源/会话/请求构造）会以 error 事件终结任务，必须与模型失败区分：
        # api_request_start 之前出现 error = 装配失败，验收必须明确失败（审核 F4 假通过修复）
        if "api_request_start" not in event_types and "error" in event_types:
            assembly_error = [e for e in events if e.get("type") == "error"][-1]
            break
        if "api_request_start" in event_types:
            saw_assembly = True
        rec_now = rs.get_task("gw_smoke_user", task_id)
        status = getattr(rec_now, "status", None)
        if status in {"succeeded", "failed", "stopped", "canceled", "cancel_requested"}:
            final_status = status
            break
        # 装配完成后主动取消（避免真实模型调用慢等；装配此时已真实发生）
        if saw_assembly:
            rs.cancel_task("gw_smoke_user", task_id)
        time.sleep(0.5)

    assert assembly_error is None, (
        f"装配阶段失败（非模型调用）：{assembly_error}"
    )
    assert saw_assembly, "任务线程应真实完成装配（出现 api_request_start 事件）"

    deadline = time.time() + 20
    while time.time() < deadline:
        rec_now = rs.get_task("gw_smoke_user", task_id)
        final_status = getattr(rec_now, "status", None)
        if final_status in {"succeeded", "failed", "stopped", "canceled"}:
            break
        time.sleep(0.5)
    assert final_status in {"succeeded", "failed", "stopped", "canceled"}, (
        f"任务应到达终态（模型失败属预期），实际: {final_status}"
    )

    # 门闸最终释放（对话应可受理下一任务）；terminal 必须存在——
    # 跳过检查会掩盖装配失败（审核 F4 假通过修复）
    import server.context.resources as resources

    term_key = "host::gwsmoke::" + str(getattr(rec_now, "conversation_id", "") or "")
    terminal = resources.state.user_terminals.get(term_key)
    assert terminal is not None, "装配成功后对话级 terminal 必须存在（否则装配未真实发生）"
    from server.main_task_gate import is_main_task_gate_busy

    assert not is_main_task_gate_busy(terminal), "任务终态后门闸必须释放（否则下一任务无法受理）"

    # 全程无 Flask app 初始化（socketio 未绑定 app）
    assert not has_app_context(), "验收结束仍应无 Flask app 上下文"
    import server.extensions as ext

    assert getattr(ext.socketio, "server", None) is None, "socketio 不应初始化 server（无 Web 应用装配）"


def check_chain():
    """第 2 步验收：极简协议客户端视角全链路（工作区→会话→运行→停止→历史→审批）。"""
    from server.runtime import runtime_service

    # 1. 双客户端场景（审核 F1 验收）：A 受理 Run；B 不持有 task_id，仅凭身份+
    # 工作区经公共入口发现 A 的活动 Run → 观察事件流 → 取消；A 的 Run 到终态。
    rec1 = runtime_service.create_task(_make_ctx())

    # B 视角：轮询 list_runs 直到发现该 Run（按 message 识别，不读 A 侧变量）
    discovered = None
    deadline = time.time() + 20
    while time.time() < deadline:
        active = runtime_service.list_runs("gw_smoke_user", "gwsmoke", status="active")
        found = [r for r in active if "协议链路验收 ping" in (r.get("message") or "")]
        if found:
            discovered = found[0]
            break
        if getattr(rec1, "status", None) in {"succeeded", "failed", "stopped", "canceled"}:
            break
        time.sleep(0.4)
    assert discovered, "B 应能经 list_runs 发现 A 发起的活动 Run（F1）"
    assert discovered["task_id"] == rec1.task_id, "发现结果应指向同一 Run"

    # B 观察事件流（用发现的 task_id），等 user_message 落盘证据后取消
    deadline = time.time() + 20
    saw_user_msg = False
    while time.time() < deadline:
        ev_b, _, err_b, _meta_b = runtime_service.get_task_events("gw_smoke_user", discovered["task_id"], 0)
        assert err_b is None, "B 应可读 A 的事件流"
        if any(e.get("type") == "user_message" for e in (ev_b or [])):
            saw_user_msg = True
            break
        if any(e.get("type") == "error" for e in (ev_b or [])):
            raise AssertionError(f"装配阶段失败（非模型调用）: {ev_b[-1] if ev_b else None}")
        time.sleep(0.4)
    assert saw_user_msg, "B 应观察到 A 的 user_message 事件（历史已落盘）"

    # B 取消 A 的 Run（跨端操作），任务到终态
    assert runtime_service.cancel_task("gw_smoke_user", discovered["task_id"]), "B 应可取消 A 的 Run"
    rec1 = _wait_terminal_state(discovered["task_id"])
    assert rec1.status in {"succeeded", "failed", "stopped", "canceled"}, f"Run1 应达终态: {rec1.status}"
    conv_id = rec1.conversation_id
    assert conv_id, "Run 应建立会话 id（服务层补建对话，conversation_id 同步受理返回）"

    # 2. 会话历史已持久化（隔离数据目录下的对话 JSON 含 user 消息）
    from config import DATA_DIR

    conv_dir = Path(DATA_DIR) / "conversations"
    candidates = list(conv_dir.rglob(f"*{conv_id}*.json")) if conv_dir.exists() else []
    assert candidates, f"会话 JSON 应已持久化（{conv_id}）"
    content = candidates[0].read_text(encoding="utf-8", errors="replace")
    assert "协议链路验收 ping" in content, "会话历史应包含本次 user 消息"

    # 3. 事件流 offset 续读：分段读取，idx 连续、无重复；窗口水位可用于缺口检测
    events_all, _, err, meta = runtime_service.get_task_events("gw_smoke_user", rec1.task_id, 0)
    assert err is None
    assert events_all, "Run 应产生事件"
    assert meta and meta.get("window_start") == 0, "未裁剪时窗口水位应为 0"
    assert meta["window_start"] <= events_all[0]["idx"], "水位不超过首个返回事件 idx"
    mid = events_all[len(events_all) // 2]["idx"]
    tail, _, err2, meta2 = runtime_service.get_task_events("gw_smoke_user", rec1.task_id, mid + 1)
    assert err2 is None
    assert meta2 and meta2.get("window_start") == 0
    idxs = [e["idx"] for e in events_all]
    assert idxs == sorted(idxs), "idx 应单调递增"
    assert len(set(idxs)) == len(idxs), "idx 应无重复"
    assert [e["idx"] for e in tail] == [i for i in idxs if i > mid], "offset 续读语义"

    # 4. 同会话第二个 Run：门闸已释放，可连续受理
    rec2 = runtime_service.create_task(_make_ctx(conversation_id=conv_id, message="第二个 Run"))
    assert rec2.conversation_id == conv_id, "第二个 Run 应复用同一会话"
    rec2 = _wait_terminal_state(rec2.task_id)
    assert rec2.status in {"succeeded", "failed", "stopped", "canceled"}, f"Run2 应达终态: {rec2.status}"

    # 5.5 会话查询公共入口（CLI/非 Web 调用方不依赖 Web 路由）
    from server.runtime import TrustedPrincipal as _TP

    listing = runtime_service.list_sessions(
        "gw_smoke_user", "gwsmoke",
        principal=_TP(username="gw_smoke_user", workspace_id="gwsmoke", role="admin",
                      host_mode=True, host_workspace_id="gwsmoke"),
    )
    items = listing.get("items") or listing.get("conversations") or []
    assert any((it.get("conversation_id") or it.get("id")) == conv_id for it in items), (
        f"会话列表应含本次会话 {conv_id}（keys={list(listing.keys())}）"
    )
    history = runtime_service.get_session_history(
        "gw_smoke_user", "gwsmoke", conv_id,
        principal=_TP(username="gw_smoke_user", workspace_id="gwsmoke", role="admin",
                      host_mode=True, host_workspace_id="gwsmoke"),
    )
    assert history and "协议链路验收 ping" in json.dumps(history, ensure_ascii=False), \
        "会话历史公共入口应可读且含 user 消息"
    try:
        runtime_service.get_session_history(
            "gw_smoke_user", "gwsmoke", conv_id,
            principal=_TP(username="mallory", workspace_id="gwsmoke"),
        )
    except PermissionError:
        pass
    else:
        raise AssertionError("principal 与查询目标不一致必须抛 PermissionError")
    # F3：principal 声明的工作区与查询目标不一致也必须拒绝（同名用户跨工作区）
    try:
        runtime_service.get_session_history(
            "gw_smoke_user", "gwsmoke", conv_id,
            principal=_TP(username="gw_smoke_user", workspace_id="other_ws"),
        )
    except PermissionError:
        pass
    else:
        raise AssertionError("principal 工作区与查询目标不一致必须抛 PermissionError")

    # 5.6 Run 发现公共入口（F1）：仅凭身份+工作区可列出活动/全部 Run
    runs_all = runtime_service.list_runs("gw_smoke_user", "gwsmoke")
    run_ids = {r["task_id"] for r in runs_all}
    assert {rec1.task_id, rec2.task_id} <= run_ids, "list_runs 应含两个 Run"
    assert all(r["username"] == "gw_smoke_user" for r in runs_all), "归属过滤"
    runs_conv = runtime_service.list_runs("gw_smoke_user", "gwsmoke", conversation_id=conv_id)
    assert {r["task_id"] for r in runs_conv} == {rec1.task_id, rec2.task_id}, "会话筛选"
    runs_done = runtime_service.list_runs("gw_smoke_user", "gwsmoke", status="active")
    assert not any(r["task_id"] in run_ids for r in runs_done), "终态后 active 筛选应为空"
    payload_keys = set(runs_all[0].keys())
    assert {"task_id", "status", "conversation_id", "task_type", "created_at"} <= payload_keys

    # 6. 审批语义：公共入口 list_pending_approvals / resolve_approval
    # （manager 层语义已有覆盖，这里验收公共入口路由与错误语义透传）
    from server.state import tool_approval_manager

    item = tool_approval_manager.create_request(
        username="gw_smoke_user", conversation_id=conv_id, task_id=rec2.task_id,
        tool_call_id="tc_smoke", tool_name="run_command",
        arguments={"command": "echo hi"}, preview={},
    )
    pending = runtime_service.list_pending_approvals("gw_smoke_user", conv_id)
    assert any(p.get("approval_id") == item["approval_id"] for p in pending.get("tool", [])), \
        "公共入口审批列表应含该项"
    first = runtime_service.resolve_approval(
        "tool", username="gw_smoke_user", item_id=item["approval_id"], decision="approved"
    )
    assert first.get("status") == "approved"
    second = runtime_service.resolve_approval(
        "tool", username="gw_smoke_user", item_id=item["approval_id"], decision="rejected"
    )
    assert second.get("status") == "approved", "重复裁决必须返回现状（单次裁决）"
    # 越权防护：他人裁决应拒绝
    item2 = tool_approval_manager.create_request(
        username="gw_smoke_user", conversation_id=conv_id, task_id=None,
        tool_call_id="tc_smoke2", tool_name="write_file", arguments={}, preview={},
    )
    try:
        runtime_service.resolve_approval(
            "tool", username="other_user", item_id=item2["approval_id"], decision="approved"
        )
    except PermissionError:
        pass
    else:
        raise AssertionError("越权裁决必须抛 PermissionError")
    # 未知审批类型应拒绝
    try:
        runtime_service.resolve_approval(
            "bogus", username="gw_smoke_user", item_id="x", decision="approved"
        )
    except ValueError:
        pass
    else:
        raise AssertionError("未知审批类型必须抛 ValueError")


def check_fake_exec():
    """第 4 步验收：Runtime 工具编排层 + 替身执行器可独立测试（无真实副作用）。

    不经模型调用（外部依赖非验收对象），直接驱动 Runtime 真实工具入口
    handle_tool_call，验证 E1-E4 分支注入语义：
    - 替身收到全部调用；结果结构被编排层正常消费（与真实后端同构）；
    - 全程不起子进程、不写真实磁盘（替身内存文件系统）。
    """
    import asyncio

    from modules.execution_plane import FakeExecutionBackend
    from server.context import RuntimeIdentity, get_user_resources

    identity = RuntimeIdentity(
        host_mode=True, host_workspace_id="gwsmoke", is_api_user=False, role="admin"
    )
    terminal, workspace = get_user_resources(
        "gw_smoke_user", workspace_id="gwsmoke", update_session=False, identity=identity
    )
    assert terminal is not None and workspace is not None, "资源装配应成功"
    backend = FakeExecutionBackend()
    terminal.execution_backend = backend

    def call(tool, args):
        out = asyncio.run(terminal.handle_tool_call(tool, args))
        return json.loads(out) if isinstance(out, str) else out

    # E1 前台命令：替身接收，不起真实子进程
    r = call("run_command", {"command": "echo hello-fake", "timeout": 5})
    assert r.get("success"), f"替身 run_command 应成功: {r}"
    assert "[fake-exec]" in str(r.get("output", "")), f"输出应来自替身: {r}"

    # E2 后台命令：替身登记，不起真实后台线程
    r = call("run_command", {"command": "sleep 1", "timeout": 60, "run_in_background": True})
    assert r.get("success"), f"替身后台命令应成功: {r}"
    assert str(r.get("command_id", "")).startswith("fake_bg_"), f"command_id 应来自替身: {r}"

    # E3 文件写（新文件，避开先读后写拦截）：替身内存文件系统接收
    r = call("write_file", {"file_path": "fake_probe.txt", "content": "line-a\n"})
    assert r.get("success"), f"替身 write_file 应成功: {r}"
    assert backend.files.get("fake_probe.txt") == "line-a\n", "写入应落替身内存"
    assert not (_SMOKE_WORKSPACE / "fake_probe.txt").exists(), "替身路径不得写真实磁盘"

    # E4 文件编辑（write 后已标记已读，guard 放行）：替身替换生效
    r = call("edit_file", {"file_path": "fake_probe.txt", "replacements": [
        {"old_string": "line-a", "new_string": "line-b"}
    ]})
    assert r.get("success"), f"替身 edit_file 应成功: {r}"
    assert backend.files.get("fake_probe.txt") == "line-b\n", "编辑应作用于替身内存"

    # 调用记录完整（E1-E4 各一次）
    ops = [c["op"] for c in backend.calls]
    assert ops == ["run_command", "run_command_background", "write_file", "edit_file"], ops

    # 默认路径回归：新建对话级 terminal 的 execution_backend 应为 None（真实链路）
    cm = terminal.context_manager.conversation_manager
    conv_id2 = cm.create_conversation(
        project_path=str(workspace.project_path), run_mode="fast",
        thinking_mode=False, model_key=None,
    )
    terminal2, _ws2 = get_user_resources(
        "gw_smoke_user", workspace_id="gwsmoke", update_session=False,
        conversation_id=conv_id2, identity=identity,
    )
    assert getattr(terminal2, "execution_backend", "MISSING") is None, "默认应为 None（真实链路）"


def check_approval_wait():
    """审核 F4 交互覆盖：执行中审批等待 → 另一调用方经公共入口回答 → 执行继续。

    驱动真实工具编排层（_execute_tool_calls_impl + approval 权限模式），不经模型
    （模型只是 tool_calls 的生产者，与等待/裁决/继续语义无关）；执行环境为替身
    （零真实副作用）。覆盖：审批创建事件发出 → 公共入口可发现 → 批准 → 工具继续执行。
    """
    import asyncio
    import threading

    from modules.execution_plane import FakeExecutionBackend
    from server.chat_flow_tool_loop import _execute_tool_calls_impl
    from server.context import RuntimeIdentity, get_user_resources
    from server.runtime import runtime_service

    identity = RuntimeIdentity(host_mode=True, host_workspace_id="gwsmoke", is_api_user=False, role="admin")
    ws_term, workspace = get_user_resources(
        "gw_smoke_user", workspace_id="gwsmoke", update_session=False, identity=identity
    )
    conv_id = ws_term.context_manager.conversation_manager.create_conversation(
        project_path=str(workspace.project_path), run_mode="fast",
        thinking_mode=False, model_key="fake-smoke-model",
    )
    terminal, _ = get_user_resources(
        "gw_smoke_user", workspace_id="gwsmoke", update_session=False,
        conversation_id=conv_id, identity=identity,
    )
    backend = FakeExecutionBackend()
    terminal.execution_backend = backend
    # 默认 work_mode=plan 会把权限锁为只读（AGENTS.md §10.6）；审批链路验收需 approval 档
    terminal.set_work_mode("execute", persist=False, conversation_id=conv_id)
    terminal.set_permission_mode("approval", persist=False, conversation_id=conv_id)

    sent_events = []

    def sender(event_type, data):
        sent_events.append((event_type, data))

    tool_calls = [{
        "id": "tc_wait_1",
        "type": "function",
        "function": {"name": "run_command", "arguments": json.dumps({"command": "echo approved-cmd", "timeout": 5})},
    }]
    loop_result = {}

    async def _noop_process(**_kwargs):
        return None

    def _get_stop_flag(*_args, **_kwargs):
        return None

    def _clear_stop_flag(*_args, **_kwargs):
        return None

    def run_loop():
        async def _main():
            return await _execute_tool_calls_impl(
                web_terminal=terminal,
                tool_calls=tool_calls,
                sender=sender,
                messages=[],
                client_sid="approval_wait_sid",
                username="gw_smoke_user",
                iteration=1,
                conversation_id=conv_id,
                last_tool_call_time=time.time(),
                process_sub_agent_updates=_noop_process,
                process_background_command_updates=_noop_process,
                get_stop_flag=_get_stop_flag,
                clear_stop_flag=_clear_stop_flag,
                workspace=workspace,
            )
        try:
            loop_result["value"] = asyncio.run(_main())
        except Exception as exc:
            loop_result["error"] = exc

    th = threading.Thread(target=run_loop, daemon=True)
    th.start()

    # 主线程扮演「另一调用方」：经公共入口发现待决审批并批准
    deadline = time.time() + 20
    approved = False
    while time.time() < deadline:
        pending = runtime_service.list_pending_approvals("gw_smoke_user", conv_id).get("tool", [])
        if pending:
            runtime_service.resolve_approval(
                "tool", username="gw_smoke_user",
                item_id=pending[0]["approval_id"], decision="approved",
            )
            approved = True
            break
        if "error" in loop_result:
            break
        time.sleep(0.3)
    th.join(timeout=20)

    assert "error" not in loop_result, f"工具循环异常: {loop_result.get('error')}"
    assert approved, "执行中的任务应产生待决审批（另一调用方可经公共入口发现）"
    assert any(t == "tool_approval_required" for t, _ in sent_events), "应发出 tool_approval_required 事件"
    assert any(c["op"] == "run_command" for c in backend.calls), "批准后工具应继续执行（替身收到命令）"
    assert not th.is_alive(), "批准后工具循环应退出"


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "lifecycle":
        check_lifecycle()
    elif mode == "chain":
        check_chain()
    elif mode == "fake_exec":
        check_fake_exec()
    elif mode == "approval_wait":
        check_approval_wait()
    else:
        print("usage: runtime_standalone_checks.py <lifecycle|chain|fake_exec|approval_wait>", file=sys.stderr)
        return 2
    print(f"CHECK_OK {mode}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
