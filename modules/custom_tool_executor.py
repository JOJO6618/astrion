"""自定义工具执行器（仅 Python 低代码模板）。"""

from __future__ import annotations

import shlex
import string
from typing import Dict, Any, Optional

from modules.custom_tool_registry import CustomToolRegistry

from modules.i18n import tr

# 默认超时时间（秒）
DEFAULT_CUSTOM_TOOL_TIMEOUT = 30


class SafeFormatter(string.Formatter):
    """防止缺失键时报错，便于友好提示。"""

    def __init__(self, args: Dict[str, Any]):
        super().__init__()
        self.args = args

    def get_value(self, key, args, kwargs):
        if isinstance(key, str):
            if key in kwargs:
                return kwargs[key]
            if key in self.args:
                return self.args[key]
        return super().get_value(key, args, kwargs)


class CustomToolExecutor:
    """根据 registry 定义运行 Python 代码模板。"""

    def __init__(self, registry: CustomToolRegistry, terminal_ops):
        self.registry = registry
        self.terminal_ops = terminal_ops

    async def run(self, tool_id: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        tool = self.registry.get_tool(tool_id)
        if not tool:
            return {"success": False, "error": tr("custom_tool.not_found", tool_id=tool_id)}
        exec_conf = tool.get("execution") or {}
        if exec_conf.get("type") not in {None, "python"}:
            return {"success": False, "error": tr("custom_tool.unsupported_type")}

        code_template = exec_conf.get("code_template") or tool.get("execution_code") or tool.get("code_template")
        if not code_template:
            return {"success": False, "error": tr("custom_tool.missing_code_template")}

        timeout = exec_conf.get("timeout") or tool.get("timeout") or DEFAULT_CUSTOM_TOOL_TIMEOUT

        # 用 string.Formatter 填充模板；缺失字段会抛出 KeyError，便于定位
        try:
            formatter = SafeFormatter(arguments or {})
            rendered = formatter.format(code_template, **(arguments or {}))
        except KeyError as exc:
            return {
                "success": False,
                "error": tr("custom_tool.missing_params", error=str(exc)),
                "missing": str(exc),
                "tool_id": tool_id,
            }
        except Exception as exc:
            return {"success": False, "error": tr("custom_tool.render_failed", error=str(exc)), "tool_id": tool_id}

        # 通过统一的 run_command 链路执行 Python，复用同一套解释器探测、沙箱与审批语义。
        result = await self.terminal_ops.run_command(
            f"python3 -u -c {shlex.quote(rendered)}",
            timeout=timeout,
        )
        result["custom_tool"] = True
        result["tool_id"] = tool_id
        result["code_rendered"] = rendered
        result.setdefault("message", result.get("output") or tr("custom_tool.executed"))

        # 返回层（可选）
        return_conf = tool.get("return") or tool.get("return_config") or {}
        result = self._apply_return_layer(result, return_conf)
        return result

    @staticmethod
    def _apply_return_layer(result: Dict[str, Any], return_conf: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(return_conf, dict) or not return_conf:
            return result
        output = result.get("output") or ""
        stderr = result.get("stderr") or ""
        return_code = result.get("return_code")
        # 截断
        trunc_limit = return_conf.get("truncate")
        if isinstance(trunc_limit, int) and trunc_limit > 0 and len(output) > trunc_limit:
            output = output[:trunc_limit]
            result["truncated"] = True
            result["output"] = output
        template = return_conf.get("template")
        if isinstance(template, str) and template.strip():
            try:
                msg = template.format(
                    output=output,
                    stderr=stderr,
                    return_code=return_code,
                    tool_id=result.get("tool_id"),
                )
                result["message"] = msg
            except Exception:
                pass
        return result
