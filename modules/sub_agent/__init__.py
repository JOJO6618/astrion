"""子智能体（Sub-Agent）模块包。

子智能体现在作为主进程内的 asyncio.Task 运行，所有实际工具调用都通过
主 WebTerminal 执行，因此自然复用主进程的宿主机沙箱 / Docker 容器链路。
"""

from modules.sub_agent.manager import SubAgentManager, TERMINAL_STATUSES

__all__ = ["SubAgentManager", "TERMINAL_STATUSES"]
