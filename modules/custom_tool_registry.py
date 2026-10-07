"""自定义工具注册与存储。

- 仅支持全局管理员可见/可用。
- 每个工具一个文件夹，三层各自独立文件：
  definition.json / execution.py / return.json (+ meta.json 备注)。
- 目前执行层仅支持 python 代码模板；Node/HTTP 暂未启用。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Any
import re

from config import (
    CUSTOM_TOOL_DIR,
    CUSTOM_TOOLS_ENABLED,
    CUSTOM_TOOL_DEFINITION_FILE,
    CUSTOM_TOOL_EXECUTION_FILE,
    CUSTOM_TOOL_RETURN_FILE,
    CUSTOM_TOOL_META_FILE,
)

from modules.i18n import tr
from core.tool_config import TOOL_CATEGORIES


_RESERVED_TOOLS = frozenset(
    name for category in TOOL_CATEGORIES.values() for name in category.tools
) | frozenset({
    "load_tools", "ask_user", "submit_plan", "view_video", "read_mediafile",
    "recall_project_memory", "search_project_memory", "update_project_memory",
    "stop_sub_agent", "send_message_to_sub_agent", "answer_sub_agent_question",
    "create_custom_agent", "list_agents", "list_active_sub_agents", "ask_master",
    "ask_other_agent", "answer_other_agent", "activate_workflow", "report_workflow_stage",
    "choose_workflow_branch", "get_workflow_status", "deactivate_workflow",
    "list_workflows", "save_workflow", "ocr_image", "manage_personalization",
})


def is_reserved_tool_name(name: str) -> bool:
    return name in _RESERVED_TOOLS or name.startswith("mcp__")


class CustomToolRegistry:
    """文件式 registry，扫描目录结构提供增删改查。"""

    ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")

    def __init__(self, root: str = CUSTOM_TOOL_DIR, enabled: bool = CUSTOM_TOOLS_ENABLED):
        self.root = Path(root).expanduser().resolve()
        self.enabled = bool(enabled)
        self.root.mkdir(parents=True, exist_ok=True)
        self._cache: List[Dict[str, Any]] = []
        if self.enabled:
            self._cache = self._load_all()

    @classmethod
    def _is_valid_tool_id(cls, tool_id: str) -> bool:
        """工具 ID 规则：以字母开头，可包含字母、数字、下划线、短横线。"""
        return bool(tool_id and cls.ID_PATTERN.match(tool_id))

    # ------------------------------------------------------------------
    # 加载与持久化
    # ------------------------------------------------------------------
    def _load_tool_dir(self, tool_dir: Path) -> Optional[Dict[str, Any]]:
        try:
            if not tool_dir.is_dir():
                return None
            def_path = tool_dir / CUSTOM_TOOL_DEFINITION_FILE
            exec_path = tool_dir / CUSTOM_TOOL_EXECUTION_FILE
            if not def_path.exists() or not exec_path.exists():
                return None
            definition = json.loads(def_path.read_text(encoding="utf-8"))
            if not isinstance(definition, dict):
                return None
            tool_id = definition.get("id") or tool_dir.name
            if not isinstance(tool_id, str) or is_reserved_tool_name(tool_id):
                return None

            execution_code = exec_path.read_text(encoding="utf-8")
            timeout = definition.get("timeout")
            execution = {
                "type": "python",
                "timeout": timeout,
                "code_template": execution_code,
                "file": str(exec_path),
            }
            return_conf = {}
            ret_path = tool_dir / CUSTOM_TOOL_RETURN_FILE
            if ret_path.exists():
                try:
                    data = json.loads(ret_path.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        return_conf = data
                except Exception:
                    pass
            meta = {}
            meta_path = tool_dir / CUSTOM_TOOL_META_FILE
            if meta_path.exists():
                try:
                    data = json.loads(meta_path.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        meta = data
                except Exception:
                    pass

            is_valid_id = self._is_valid_tool_id(tool_id)

            return {
                "id": tool_id,
                "description": definition.get("description") or f"自定义工具 {tool_id}",
                "parameters": definition.get("parameters") or {"type": "object", "properties": {}},
                "required": definition.get("required") or [],
                "category": definition.get("category") or "custom",
                "icon": definition.get("icon") or meta.get("icon"),
                "execution": execution,
                "execution_code": execution_code,
                "return": return_conf,
                "meta": meta,
                "invalid_id": not is_valid_id,
                "validation_error": None if is_valid_id else "工具ID需以字母开头，可含字母/数字/_/-",
            }
        except Exception:
            return None

    def _load_all(self) -> List[Dict[str, Any]]:
        tools: List[Dict[str, Any]] = []
        for child in sorted(self.root.iterdir()):
            item = self._load_tool_dir(child)
            if item:
                tools.append(item)
        return tools

    def reload(self) -> List[Dict[str, Any]]:
        self._cache = self._load_all()
        return list(self._cache)

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    def list_tools(self) -> List[Dict[str, Any]]:
        return list(self._cache)

    def get_tool(self, tool_id: str) -> Optional[Dict[str, Any]]:
        for item in self._cache:
            if item.get("id") == tool_id:
                return item
        return None

    def upsert_tool(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """插入或更新单个工具定义（不做深度校验，满足私有化需求）。"""
        tool_id = (payload.get("id") or "").strip()
        if not tool_id:
            raise ValueError(tr("custom_tool_reg.id_required"))
        if not self._is_valid_tool_id(tool_id):
            raise ValueError(tr("custom_tool_reg.id_invalid"))
        if is_reserved_tool_name(tool_id):
            raise ValueError("自定义工具不能覆盖内置工具名称。")
        tool_dir = self.root / tool_id
        tool_dir.mkdir(parents=True, exist_ok=True)

        # definition
        definition = {
            "id": tool_id,
            "description": payload.get("description"),
            "parameters": payload.get("parameters"),
            "required": payload.get("required"),
            "category": payload.get("category") or "custom",
            "icon": payload.get("icon"),
            "timeout": payload.get("timeout"),
        }
        (tool_dir / CUSTOM_TOOL_DEFINITION_FILE).write_text(
            json.dumps(definition, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # execution code
        exec_code = payload.get("execution_code") or (
            (payload.get("execution") or {}).get("code_template")
        )
        if not exec_code:
            exec_code = "# add python code here\n"
        (tool_dir / CUSTOM_TOOL_EXECUTION_FILE).write_text(exec_code, encoding="utf-8")

        # return layer
        return_conf = payload.get("return") or payload.get("return_config") or {}
        if return_conf:
            (tool_dir / CUSTOM_TOOL_RETURN_FILE).write_text(
                json.dumps(return_conf, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        elif (tool_dir / CUSTOM_TOOL_RETURN_FILE).exists():
            (tool_dir / CUSTOM_TOOL_RETURN_FILE).unlink()

        # meta
        meta = payload.get("meta") or payload.get("notes") or {}
        if meta:
            (tool_dir / CUSTOM_TOOL_META_FILE).write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        elif (tool_dir / CUSTOM_TOOL_META_FILE).exists():
            (tool_dir / CUSTOM_TOOL_META_FILE).unlink()

        self.reload()
        return self.get_tool(tool_id) or {}

    def delete_tool(self, tool_id: str) -> bool:
        tool_dir = self.root / tool_id
        if tool_dir.exists() and tool_dir.is_dir():
            shutil.rmtree(tool_dir)
            self.reload()
            return True
        return False


def build_default_tool_category() -> Dict[str, Any]:
    """生成自定义工具的默认类别定义，用于前端和终端展示。"""
    return {
        "id": "custom",
        "label": "自定义工具",
        "tools": [],
        "default_enabled": True,
        "silent_when_disabled": False,
    }
