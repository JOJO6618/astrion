"""Gateway 第 1/2 步验收：独立任务生命周期 + 极简协议全链路（G4/G13）。

验收标准（来自 _experiments/cache_research/gateway/gateway_current_state.md §7.2，本地归档未随仓库发布）：
- 进程全程不创建 Flask app、不注册蓝图、不初始化 SocketIO 服务；
- RuntimeService 真实受理（create_task → task_id）、任务线程真实启动；
- 资源装配真实执行（get_user_resources 由 RuntimeIdentity 驱动，不重 mock）；
- 事件流按 idx/offset 协议可读；任务可取消；门闸最终释放；
- 模型调用允许失败（外部依赖非验收对象）——装配与生命周期必须真实。

隔离设计：真实检查体在 test/runtime_standalone_checks.py，由本文件以
**子进程**方式执行。原因：config/paths.py 的 DATA_DIR / DEPLOY_CONFIG_DIR
是 import 时固化的模块级常量；unittest discover 全量运行时排在前面的测试
模块会先 import config 使常量固化，进程内设环境变量已无效，被测代码会落到
非隔离目录（甚至用户真实运行态目录）执行任务。子进程内 import 顺序完全可控，
隔离（ASTRION_DATA_ROOT + DEPLOY_CONFIG_DIR 指向临时目录）100% 可靠，
单跑与全量行为一致。
"""
import subprocess
import sys
import unittest
from pathlib import Path

_CHECKS_SCRIPT = Path(__file__).resolve().parent / "runtime_standalone_checks.py"
_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run_check(mode: str) -> str:
    proc = subprocess.run(
        [sys.executable, str(_CHECKS_SCRIPT), mode],
        capture_output=True,
        text=True,
        cwd=str(_PROJECT_ROOT),
        timeout=150,
    )
    output = f"STDOUT:\n{proc.stdout[-4000:]}\nSTDERR:\n{proc.stderr[-4000:]}"
    if proc.returncode != 0:
        raise AssertionError(f"独立验收子进程失败 (mode={mode}, rc={proc.returncode}):\n{output}")
    return output


class StandaloneRuntimeLifecycleTest(unittest.TestCase):
    """无 Flask app 的独立 Gateway 生命周期验收（子进程执行）。"""

    def test_create_observe_cancel_without_web_app(self):
        _run_check("lifecycle")


class ProtocolSmokeChainTest(unittest.TestCase):
    """第 2 步验收：极简协议客户端视角的全链路（工作区→会话→运行→停止→历史→审批）。"""

    def test_full_chain_run_history_offset_approval(self):
        _run_check("chain")


class ExecutionPlaneFakeBackendTest(unittest.TestCase):
    """第 4 步验收：Runtime 工具编排层 + 替身执行器可独立测试（无真实副作用）。"""

    def test_runtime_with_fake_execution_backend(self):
        _run_check("fake_exec")


class ApprovalWaitChainTest(unittest.TestCase):
    """审核 F4 交互覆盖：执行中审批等待→公共入口回答→执行继续（真实工具编排层）。"""

    def test_approval_wait_resolve_continue(self):
        _run_check("approval_wait")


if __name__ == "__main__":
    unittest.main()
