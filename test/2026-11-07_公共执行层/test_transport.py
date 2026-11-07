#!/usr/bin/env python3
"""test_transport.py —— 跨进程 stdio 执行层可行性实验（控制侧）。

它验证什么
----------
1. 一个独立子进程能承载 ``LocalExecutionBackend`` + ``ExecutionResources``；
2. 控制侧（本文件所在进程）与执行侧工作区**是两个不同目录**，
   文件读写与命令执行只落在执行侧；
3. 请求/响应严格按 JSON-RPC 帧序列化，``id`` 回显、错误码可判定、
   环境信息带协议版本；
4. 二进制显式 base64 往返；
5. 方法分派是白名单，``eval`` / ``__import__`` / ``os.system`` 不可达；
6. 前台命令可取消。

它不验证什么（刻意的）
----------------------
* 不是公共协议：没有版本协商矩阵、没有兼容性承诺、没有 Relay/加密/认证；
* 不验证后台命令与 terminals（``run_command_background`` / ``backend.terminals``）：
  它们依赖 ``background_command_manager`` 的 pid 握手，本轮不覆盖；
* 不验证多客户端并发、断线重连、流控。

运行
----
    ASTRION_IGNORE_DOTENV=1 python3 test/2026-11-07_公共执行层/test_transport.py

退出码：0 = 全通过或按预期跳过；1 = 存在失败。
"""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from transport_harness import (
    BACKEND_UNAVAILABLE,
    CONTRACT_MISSING,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    Ctx,
    SkipCase,
    expect,
    expect_ok,
)

# ROOT/PROBE、错误码与客户端都在 transport_harness 里；本文件只放用例与运行器。


# --------------------------------------------------------------------------
# 用例
# --------------------------------------------------------------------------

def case_handshake(ctx: Ctx) -> None:
    ready = ctx.ready["params"]
    expect(ready["protocol_version"].startswith("astrion-exec-probe/"), "ready 缺协议版本")
    expect(ready["pid"] == ctx.client.proc.pid, "ready.pid 与子进程 pid 不一致")

    result = expect_ok(ctx.info, "env.info")
    expect(result["protocol_version"] == ready["protocol_version"], "协议版本在 ready/env.info 间不一致")
    expect(result["server"]["pid"] == ctx.client.proc.pid, "env.info 报告的 pid 不是子进程")
    expect(result["server"]["nonce"] == ready["nonce"], "nonce 在 ready/env.info 间不一致")
    expect(isinstance(result["capabilities"], list) and "exec.run" in result["capabilities"],
           "capabilities 缺 exec.run")

    # 请求 id 必须原样回显（int 与 str 各一次）
    rid = ctx.client.send("ping", {})
    resp = ctx.client.wait(rid)
    expect(resp.get("id") == rid, "int id 未回显")
    str_id = "id-str-1"
    ctx.client.send_raw((json.dumps({"jsonrpc": "2.0", "id": str_id, "method": "ping", "params": {}}) + "\n").encode())
    frame = None
    deadline = time.time() + 10
    while time.time() < deadline:
        candidate = ctx.client._frames.get(timeout=5)
        if candidate and candidate.get("id") == str_id:
            frame = candidate
            break
        if candidate:
            ctx.client._frames.put(candidate)
    expect(frame is not None and "result" in frame, "str id 未回显")


def case_allowlist(ctx: Ctx) -> None:
    before = ctx.control_listing()
    for method in ("eval", "__import__", "os.system", "exec", "fs.read_text.__class__", "backend.run_command"):
        resp = ctx.client.request(method, {"code": "print(1)"})
        expect("error" in resp, "%s 竟然被接受（白名单失效）" % method)
        expect(resp["error"]["code"] == METHOD_NOT_FOUND,
               "%s 错误码应为 -32601，实际 %s" % (method, resp["error"]["code"]))
    expect(ctx.control_listing() == before, "未知方法调用产生了副作用")


def case_parse_error_recovery(ctx: Ctx) -> None:
    ctx.client.send_raw(b"{not json}\n")
    frame = ctx.client._frames.get(timeout=10)
    expect(frame is not None and "error" in frame, "非法 JSON 未返回解析错误")
    expect(frame["error"]["code"] == PARSE_ERROR, "解析错误码应为 -32700，实际 %s" % frame["error"]["code"])
    expect(frame.get("id") is None, "解析错误不应携带 id")

    # 连接必须存活
    expect_ok(ctx.client.request("ping"), "解析错误后 ping")


def case_invalid_params(ctx: Ctx) -> None:
    resp = ctx.client.request("fs.read_text", {})
    expect("error" in resp and resp["error"]["code"] == INVALID_PARAMS,
           "缺 path 应返回 -32602，实际 %s" % json.dumps(resp, ensure_ascii=False))

    resp = ctx.client.request("exec.run", {"command": 123})
    expect("error" in resp and resp["error"]["code"] == INVALID_PARAMS,
           "非字符串 command 应返回 -32602，实际 %s" % json.dumps(resp, ensure_ascii=False))

    ctx.client.send_raw((json.dumps({"jsonrpc": "2.0", "id": 99, "method": "ping", "params": []}) + "\n").encode())
    frame = ctx.client._frames.get(timeout=10)
    expect(frame is not None and "error" in frame and frame["error"]["code"] == INVALID_REQUEST,
           "params 为数组应返回 -32600")


def case_workspace_isolation(ctx: Ctx) -> None:
    ctx.require_backend()
    info = expect_ok(ctx.client.request("env.info"), "env.info")
    expect(Path(info["workspace_root"]) == ctx.workspace.resolve(),
           "workspace_root 不是预期执行侧目录：%s" % info["workspace_root"])
    expect(Path(info["cwd"]) == ctx.control.resolve(),
           "子进程 cwd 应停留在控制侧目录，实际 %s" % info["cwd"])

    result = expect_ok(ctx.client.request("fs.write_text", {"path": "hello.txt", "content": "alpha\n"}), "fs.write_text")
    expect(result.get("success") is True, "写入未成功：%s" % result)
    expect((ctx.workspace / "hello.txt").exists(), "文件没有落在执行侧 workspace")
    expect(not (ctx.control / "hello.txt").exists(), "文件落到了控制侧目录")
    expect(ctx.control_listing() == [], "控制侧目录被污染：%s" % ctx.control_listing())

    read = expect_ok(ctx.client.request("fs.read_text", {"path": "hello.txt"}), "fs.read_text")
    expect(read.get("success") is True and read.get("content") == "alpha\n", "回读内容不一致：%s" % read)
    expect(read.get("encoding") == "utf-8", "文本响应缺少显式 encoding 字段")


def case_exec_environment(ctx: Ctx) -> str:
    """探测本机是否允许在沙箱内再套一层 seatbelt/bwrap。

    这只描述**测试宿主**的限制，不是产品行为。
    """
    ctx.require_backend()
    resp = ctx.client.request("exec.run", {
        "command": "true", "timeout": 15,
        "sandbox_write_access": True, "network_permission": "restricted",
    }, timeout=25)
    if "error" in resp:
        raise AssertionError("exec.run 返回协议错误：%s" % json.dumps(resp["error"], ensure_ascii=False))
    result = resp["result"]
    output = (result.get("output") or "") + (result.get("stderr") or "")
    blocked = (
        result.get("return_code") == 71
        and ("sandbox_apply" in output or "Operation not permitted" in output)
    )
    ctx.sandbox_exec_blocked = blocked
    ctx.sandbox_exec_detail = output.strip()
    if blocked:
        return "宿主禁止嵌套 seatbelt（本测试进程已在沙箱内）；命令类用例改走 direct 探针"
    return "sandbox 模式可执行命令"


def case_binary_base64(ctx: Ctx) -> None:
    ctx.require_backend()
    client = ctx.exec_client
    script = "open('blob.bin','wb').write(bytes(range(256)))"
    run = expect_ok(client.request("exec.run", {
        "command": "%s -c \"%s\"" % (sys.executable, script),
        "timeout": 30,
        "sandbox_write_access": True,
        "network_permission": "restricted",
    }, timeout=40), "exec.run(生成二进制)")
    expect(run.get("success") is True, "生成二进制失败：%s" % run)

    payload = expect_ok(client.request("fs.read_binary", {"path": "blob.bin"}), "fs.read_binary")
    expect(payload.get("encoding") == "base64", "二进制响应必须显式声明 encoding=base64")
    decoded = base64.b64decode(payload["data"], validate=True)
    expect(decoded == bytes(range(256)), "base64 往返内容不一致")
    expect(payload.get("size") == 256, "size 字段应为 256，实际 %s" % payload.get("size"))


def case_read_segment(ctx: Ctx) -> None:
    ctx.require_backend()
    content = "l1\nl2\nl3\nl4\nl5\n"
    expect_ok(ctx.client.request("fs.write_text", {"path": "lines.txt", "content": content}), "写入 lines.txt")
    seg = expect_ok(ctx.client.request("fs.read_segment", {
        "path": "lines.txt", "start_line": 2, "end_line": 3, "size_limit": None,
    }), "fs.read_segment")
    expect(seg.get("success") is True, "分段读取失败：%s" % seg)
    expect(seg.get("content") == "l2\nl3\n", "行号语义不符（应为 1-based 闭区间）：%r" % seg.get("content"))
    expect(seg.get("total_lines") == 5, "total_lines 应为 5，实际 %s" % seg.get("total_lines"))


def case_search_extract(ctx: Ctx) -> None:
    ctx.require_backend()
    content = "alpha\nneedle here\nbeta\nneedle again\n"
    expect_ok(ctx.client.request("fs.write_text", {"path": "search.txt", "content": content}), "写入 search.txt")

    found = expect_ok(ctx.client.request("fs.search", {
        "path": "search.txt", "query": "needle", "max_matches": 10,
        "context_before": 0, "context_after": 0, "case_sensitive": False,
    }), "fs.search")
    expect(found.get("success") is True, "搜索失败：%s" % found)
    expect("needle" in json.dumps(found, ensure_ascii=False), "搜索结果不含命中文本")

    extracted = expect_ok(ctx.client.request("fs.extract_segments", {
        "path": "search.txt",
        "segments": [{"start_line": 1, "end_line": 2}],
        "size_limit": None,
    }), "fs.extract_segments")
    expect(extracted.get("success") is True, "多段提取失败：%s" % extracted)
    expect("alpha" in json.dumps(extracted, ensure_ascii=False), "提取结果不含第 1 行内容")


def case_command_isolation(ctx: Ctx) -> None:
    ctx.require_backend()
    client = ctx.exec_client
    probe_py = "%s -c \"import os;print(os.getcwd())\"" % sys.executable
    run = expect_ok(client.request("exec.run", {
        "command": probe_py, "timeout": 30,
        "sandbox_write_access": True, "network_permission": "restricted",
    }, timeout=40), "exec.run(pwd)")
    expect(run.get("return_code") == 0, "命令返回码非 0：%s" % run)
    expect(str(ctx.workspace.resolve()) in (run.get("output") or ""),
           "命令 cwd 不是执行侧 workspace：%r" % run.get("output"))

    write = expect_ok(client.request("exec.run", {
        "command": "echo hi > cmd_out.txt", "timeout": 30,
        "sandbox_write_access": True, "network_permission": "restricted",
    }, timeout=40), "exec.run(写文件)")
    expect(write.get("success") is True, "命令写文件失败：%s" % write)
    expect((ctx.workspace / "cmd_out.txt").exists(), "命令产物未落在执行侧")
    expect(not (ctx.control / "cmd_out.txt").exists(), "命令产物落到了控制侧")


def case_path_escape(ctx: Ctx) -> None:
    ctx.require_backend()
    rel = expect_ok(ctx.client.request("fs.write_text", {"path": "../control/evil_rel.txt", "content": "x"}), "越界相对路径")
    expect(rel.get("success") is False, "越界相对路径未被拒绝：%s" % rel)

    abs_target = ctx.control / "evil_abs.txt"
    absolute = expect_ok(ctx.client.request("fs.write_text", {"path": str(abs_target), "content": "x"}), "越界绝对路径")
    expect(absolute.get("success") is False, "越界绝对路径未被拒绝：%s" % absolute)

    expect(not (ctx.control / "evil_rel.txt").exists(), "相对路径越界写成功（边界失效）")
    expect(not abs_target.exists(), "绝对路径越界写成功（边界失效）")


def case_cancel(ctx: Ctx) -> None:
    ctx.require_backend()
    client = ctx.exec_client
    started = time.time()
    target = client.send("exec.run", {
        "command": "sleep 5", "timeout": 30,
        "sandbox_write_access": True, "network_permission": "restricted",
    })
    time.sleep(0.4)
    ack = client.request("cancel", {"request_id": target}, timeout=10)
    ack_result = expect_ok(ack, "cancel")
    expect(ack_result.get("cancelled") is True, "cancel 未生效：%s" % ack_result)

    resp = client.wait(target, timeout=10)
    result = expect_ok(resp, "exec.run(被取消)")
    expect(result.get("status") == "cancelled", "取消后 status 应为 cancelled，实际 %s" % result.get("status"))
    elapsed = time.time() - started
    expect(elapsed < 4.5, "取消未及时生效，耗时 %.2fs" % elapsed)


def case_contract_inspect_path(ctx: Ctx) -> None:
    ctx.require_backend()
    resp = ctx.client.request("fs.inspect", {"path": "hello.txt"})
    if "error" in resp and resp["error"]["code"] == CONTRACT_MISSING:
        raise AssertionError("契约偏差：backend.files.inspect_path 不存在（%s）" % resp["error"].get("data"))
    result = expect_ok(resp, "fs.inspect")
    expect(result.get("success") is not False, "inspect 返回失败：%s" % result)


CASES: List[Tuple[str, Any]] = [
    ("宿主执行能力探测", case_exec_environment),
    ("握手/环境信息/ID 回显", case_handshake),
    ("方法白名单（无 eval/import）", case_allowlist),
    ("解析错误与连接存活", case_parse_error_recovery),
    ("参数校验错误码", case_invalid_params),
    ("工作区隔离（写文件不落控制侧）", case_workspace_isolation),
    ("二进制 base64 往返", case_binary_base64),
    ("按行分段读取", case_read_segment),
    ("搜索与多段提取", case_search_extract),
    ("命令执行与产物隔离", case_command_isolation),
    ("路径越界拒绝", case_path_escape),
    ("前台命令取消", case_cancel),
    ("契约：files.inspect_path", case_contract_inspect_path),
]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="astrion-exec-probe-") as tmp:
        ctx = Ctx(Path(tmp))
        rows: List[Tuple[str, str, str]] = []
        try:
            for name, fn in CASES:
                started = time.time()
                try:
                    note = fn(ctx)
                    detail = note if isinstance(note, str) and note else "%.2fs" % (time.time() - started)
                    rows.append((name, "PASS", detail))
                except SkipCase as exc:
                    rows.append((name, "SKIP", str(exc)))
                except Exception as exc:
                    detail = "%s: %s" % (type(exc).__name__, exc)
                    rows.append((name, "FAIL", detail))
                    if os.environ.get("PROBE_VERBOSE"):
                        print(traceback.format_exc(), file=sys.stderr)
        finally:
            stderr_tail = ctx.client.stderr_text[-1500:]
            ctx.close()

        print("=" * 78)
        print("跨进程 stdio 执行层夹具 · 可行性实验")
        print("  执行侧 workspace : %s" % ctx.workspace)
        print("  执行侧 data_dir  : %s" % ctx.data_dir)
        print("  控制侧 cwd       : %s" % ctx.control)
        print("  后端可用         : %s" % ctx.backend_available)
        print("  命令执行探针     : %s" % ("direct（宿主禁止嵌套沙箱）" if ctx.sandbox_exec_blocked else "sandbox"))
        print("=" * 78)
        width = max(len(n) for n, _, _ in rows)
        for name, status, detail in rows:
            print("%-*s  %-4s  %s" % (width, name, status, detail))
        print("-" * 78)
        passed = sum(1 for _, s, _ in rows if s == "PASS")
        skipped = sum(1 for _, s, _ in rows if s == "SKIP")
        failed = sum(1 for _, s, _ in rows if s == "FAIL")
        print("PASS %d / SKIP %d / FAIL %d" % (passed, skipped, failed))
        if failed:
            print("\n--- 子进程 stderr（末尾）---")
            print(stderr_tail or "(empty)")
        return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
