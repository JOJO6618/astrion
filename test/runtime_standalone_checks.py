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
    saw_events = False
    final_status = None
    while time.time() < deadline:
        events, _next_offset, err, _meta = rs.get_task_events("gw_smoke_user", task_id, 0)
        assert err is None, f"get_task_events 应可读: {err}"
        if events:
            saw_events = True
        rec_now = rs.get_task("gw_smoke_user", task_id)
        status = getattr(rec_now, "status", None)
        if status in {"succeeded", "failed", "stopped", "canceled", "cancel_requested"}:
            final_status = status
            break
        # 装配完成后主动取消（避免真实模型调用慢等；装配此时已真实发生）
        if saw_events:
            rs.cancel_task("gw_smoke_user", task_id)
        time.sleep(0.5)

    assert saw_events, "任务线程应真实启动并产生事件（装配真实发生）"

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

    # 门闸最终释放（对话应可受理下一任务）
    import server.context.resources as resources

    term_key = "host::gwsmoke::" + str(getattr(rec_now, "conversation_id", "") or "")
    terminal = resources.state.user_terminals.get(term_key)
    if terminal is not None:
        from server.main_task_gate import is_main_task_gate_busy

        assert not is_main_task_gate_busy(terminal), "任务终态后门闸必须释放（否则下一任务无法受理）"

    # 全程无 Flask app 初始化（socketio 未绑定 app）
    assert not has_app_context(), "验收结束仍应无 Flask app 上下文"
    import server.extensions as ext

    assert getattr(ext.socketio, "server", None) is None, "socketio 不应初始化 server（无 Web 应用装配）"


def check_chain():
    """第 2 步验收：极简协议客户端视角全链路（工作区→会话→运行→停止→历史→审批）。"""
    from server.runtime import runtime_service

    # 1. 第一个 Run：受理 → 终态（模型失败属预期；等 api_request_start 确保历史已写）
    rec1 = runtime_service.create_task(_make_ctx())
    rec1 = _wait_terminal_state(rec1.task_id, cancel_on_event="api_request_start")
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

    # 6. 审批语义：create → list_pending → decide → 重复 decide 返回现状（单次裁决）
    from server.state import tool_approval_manager

    item = tool_approval_manager.create_request(
        username="gw_smoke_user", conversation_id=conv_id, task_id=rec2.task_id,
        tool_call_id="tc_smoke", tool_name="run_command",
        arguments={"command": "echo hi"}, preview={},
    )
    pending = tool_approval_manager.list_pending("gw_smoke_user", conv_id)
    assert any(p.get("approval_id") == item["approval_id"] for p in pending), "审批应入 pending 列表"
    first = tool_approval_manager.decide(item["approval_id"], "gw_smoke_user", "approved")
    assert first.get("status") == "approved"
    second = tool_approval_manager.decide(item["approval_id"], "gw_smoke_user", "rejected")
    assert second.get("status") == "approved", "重复裁决必须返回现状（单次裁决）"
    # 越权防护：他人裁决应拒绝
    item2 = tool_approval_manager.create_request(
        username="gw_smoke_user", conversation_id=conv_id, task_id=None,
        tool_call_id="tc_smoke2", tool_name="write_file", arguments={}, preview={},
    )
    try:
        tool_approval_manager.decide(item2["approval_id"], "other_user", "approved")
    except PermissionError:
        pass
    else:
        raise AssertionError("越权裁决必须抛 PermissionError")


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


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "lifecycle":
        check_lifecycle()
    elif mode == "chain":
        check_chain()
    elif mode == "fake_exec":
        check_fake_exec()
    else:
        print("usage: runtime_standalone_checks.py <lifecycle|chain|fake_exec>", file=sys.stderr)
        return 2
    print(f"CHECK_OK {mode}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
