"""工作流（Workflow）存储管理：WORKFLOW.md 的加载、保存、列举、删除。

存储布局（对齐 skills 的双源合并模式）：
- 内置示例：源码树 ``workflows/<name>/WORKFLOW.md``（只读种子）
- 用户库：host 模式 ``<runtime_root>/host/workflows/``；web/docker 模式
  ``users/<user>/personal/workflows/``（从 workspace data_dir 推断）

文件格式：YAML frontmatter（snake_case 结构）+ markdown 正文（body）。
API 层传输使用 camelCase dict，本模块负责双向转换。
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from config.paths import CUSTOM_SKILLS_DIR, IS_HOST_MODE

# 内置示例种子目录（源码树 workflows/）
BUILTIN_WORKFLOWS_DIR = Path(__file__).resolve().parent.parent / "workflows"

WORKFLOW_FILENAME = "WORKFLOW.md"

# host 模式用户库：与 CUSTOM_SKILLS_DIR 平级（<runtime_root>/<mode>/workflows）
CUSTOM_WORKFLOWS_DIR = str(Path(CUSTOM_SKILLS_DIR).parent / "workflows")

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


# ---------------------------------------------------------------- 路径推断


def infer_user_workflows_dir(data_dir: str | Path | None) -> Optional[Path]:
    """从 workspace data_dir 推断用户工作流库目录（对齐 infer_private_skills_dir）。

    host 模式：统一运行态根下的 workflows/，不按用户拆分。
    web/docker：users/<user>/personal/workflows/。
    """
    if IS_HOST_MODE:
        root = Path(CUSTOM_WORKFLOWS_DIR).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root
    if not data_dir:
        return None
    try:
        data_path = Path(data_dir).expanduser().resolve()
        if data_path.name == "data" and data_path.parent.parent.name == "workspaces":
            root = (data_path.parent.parent.parent / "workflows").resolve()
            root.mkdir(parents=True, exist_ok=True)
            return root
        if data_path.name == "data" and data_path.parent.parent.name in ("projects", "project"):
            user_root = data_path.parent.parent.parent
            root = (user_root / "personal" / "workflows").resolve()
            root.mkdir(parents=True, exist_ok=True)
            return root
    except Exception:
        return None
    return None


def _workflow_file(root: Path, name: str) -> Path:
    return (root / name / WORKFLOW_FILENAME).resolve()


def _safe_name(name: str) -> str:
    """校验并返回合法的工作流目录名（slug）。"""
    cleaned = (name or "").strip()
    if not _NAME_RE.match(cleaned):
        raise ValueError(f"工作流名称不合法：{cleaned!r}（仅限小写字母/数字/连字符，3-64 字符）")
    return cleaned


# ---------------------------------------------------------------- camelCase ↔ snake_case 转换

_NODE_KINDS = ("start", "end", "stage", "review", "branch")


def _node_to_yaml(node: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"id": node.get("id"), "kind": node.get("kind")}
    if node.get("name"):
        out["name"] = node["name"]
    kind = node.get("kind")
    if kind == "stage":
        out["goal"] = node.get("goal", "")
        out["instructions"] = node.get("instructions", "")
        out["next"] = node.get("next")
    elif kind == "review":
        out["prompt"] = node.get("prompt", "")
        out["next"] = node.get("next")
        out["reject_to"] = node.get("rejectTo")
        out["max_rejects"] = node.get("maxRejects", 3)
    elif kind == "branch":
        out["next"] = [
            {"target": r.get("target"), "condition": r.get("condition", "")}
            for r in node.get("next", [])
        ]
    elif kind == "start":
        out["next"] = node.get("next")
    if node.get("position"):
        out["position"] = {"x": round(node["position"].get("x", 0)), "y": round(node["position"].get("y", 0))}
    return out


def _node_from_yaml(data: Dict[str, Any]) -> Dict[str, Any]:
    kind = data.get("kind")
    node: Dict[str, Any] = {
        "id": str(data.get("id") or ""),
        "kind": kind if kind in _NODE_KINDS else "stage",
        "name": str(data.get("name") or data.get("id") or ""),
    }
    if node["kind"] == "stage":
        node["goal"] = str(data.get("goal") or "")
        node["instructions"] = str(data.get("instructions") or "")
        node["next"] = data.get("next") or None
    elif node["kind"] == "review":
        node["prompt"] = str(data.get("prompt") or "")
        node["next"] = data.get("next") or None
        node["rejectTo"] = data.get("reject_to") or None
        node["maxRejects"] = int(data.get("max_rejects") or 3)
    elif node["kind"] == "branch":
        routes = []
        for r in data.get("next") or []:
            if isinstance(r, dict) and r.get("target"):
                routes.append({"target": str(r["target"]), "condition": str(r.get("condition") or "")})
        node["next"] = routes
    elif node["kind"] == "start":
        node["next"] = data.get("next") or None
    pos = data.get("position")
    if isinstance(pos, dict) and isinstance(pos.get("x"), (int, float)):
        node["position"] = {"x": float(pos["x"]), "y": float(pos.get("y") or 0)}
    return node


def workflow_to_markdown(wf: Dict[str, Any]) -> str:
    """camelCase dict → WORKFLOW.md 文本。"""
    meta = {
        "name": wf.get("name"),
        "description": wf.get("description", ""),
        "review_mode": wf.get("reviewMode", "active"),
        "max_stage_rounds": int(wf.get("maxStageRounds", 20)),
        "end_conditions": wf.get("endConditions", ""),
        "updated_at": wf.get("updatedAt") or datetime.now().strftime("%Y-%m-%d %H:%M"),
        "nodes": [_node_to_yaml(n) for n in wf.get("nodes", [])],
    }
    frontmatter = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()
    body = (wf.get("body") or "").strip()
    return f"---\n{frontmatter}\n---\n\n{body}\n"


def workflow_from_markdown(text: str, source: str) -> Dict[str, Any]:
    """WORKFLOW.md 文本 → camelCase dict。"""
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.DOTALL)
    if not match:
        raise ValueError("WORKFLOW.md 缺少 YAML frontmatter")
    meta = yaml.safe_load(match.group(1)) or {}
    body = match.group(2).strip()
    return {
        "name": str(meta.get("name") or ""),
        "description": str(meta.get("description") or ""),
        "reviewMode": meta.get("review_mode") if meta.get("review_mode") in ("readonly", "active") else "active",
        "maxStageRounds": int(meta.get("max_stage_rounds") or 20),
        "endConditions": str(meta.get("end_conditions") or ""),
        "updatedAt": str(meta.get("updated_at") or ""),
        "body": body,
        "source": source,
        "nodes": [_node_from_yaml(n) for n in meta.get("nodes") or [] if isinstance(n, dict)],
    }


# ---------------------------------------------------------------- 结构校验（保存时强制 error 级）


def validate_structure(wf: Dict[str, Any]) -> List[str]:
    """返回 error 级问题列表（空 = 可保存）。规则与前端 validateWorkflow 对齐。"""
    errors: List[str] = []
    if not str(wf.get("name") or "").strip():
        errors.append("工作流缺少 name")
    nodes = wf.get("nodes") or []
    if not nodes:
        errors.append("至少需要一个开始节点和一个结束节点")
        return errors
    by_id: Dict[str, Dict[str, Any]] = {}
    for n in nodes:
        nid = n.get("id")
        if nid in by_id:
            errors.append(f"节点 id 重复：{nid}")
        by_id[nid] = n
    starts = [n for n in nodes if n.get("kind") == "start"]
    ends = [n for n in nodes if n.get("kind") == "end"]
    if len(starts) == 0:
        errors.append("缺少开始节点")
    elif len(starts) > 1:
        errors.append(f"开始节点只能有一个（当前 {len(starts)} 个）")
    if not ends:
        errors.append("缺少结束节点")

    def check_ref(owner: Dict[str, Any], target: Any, label: str) -> None:
        if not target:
            errors.append(f"{label}未连接")
        elif target not in by_id:
            errors.append(f"{label}指向不存在的节点：{target}")
        elif by_id[target].get("kind") == "start":
            errors.append(f"{label}不能指向开始节点")

    for n in nodes:
        kind = n.get("kind")
        name = n.get("name") or n.get("id")
        if kind == "start":
            check_ref(n, n.get("next"), "开始节点")
        elif kind == "stage":
            check_ref(n, n.get("next"), f"阶段「{name}」")
        elif kind == "review":
            check_ref(n, n.get("next"), f"审核「{name}」的通过路由")
            check_ref(n, n.get("rejectTo"), f"审核「{name}」的驳回路由")
            if not isinstance(n.get("maxRejects"), int) or n["maxRejects"] < 1:
                errors.append(f"审核「{name}」驳回上限必须 ≥ 1")
        elif kind == "branch":
            for r in n.get("next") or []:
                check_ref(n, r.get("target"), f"分支「{name}」的出线")
    return errors


# ---------------------------------------------------------------- CRUD


def _iter_workflow_dirs(root: Path) -> List[Tuple[str, Path]]:
    if not root.exists() or not root.is_dir():
        return []
    out = []
    for child in sorted(root.iterdir()):
        wf_file = child / WORKFLOW_FILENAME
        if child.is_dir() and wf_file.exists():
            out.append((child.name, wf_file))
    return out


def list_workflows(data_dir: str | Path | None) -> List[Dict[str, Any]]:
    """双源合并列举（用户库同名覆盖内置）。"""
    merged: Dict[str, Dict[str, Any]] = {}
    for source, root in (("builtin", BUILTIN_WORKFLOWS_DIR), ("user", infer_user_workflows_dir(data_dir))):
        if not root:
            continue
        for name, wf_file in _iter_workflow_dirs(Path(root)):
            try:
                wf = workflow_from_markdown(wf_file.read_text(encoding="utf-8"), source)
                merged[wf["name"] or name] = {
                    "name": wf["name"] or name,
                    "description": wf["description"],
                    "source": source,
                    "updatedAt": wf["updatedAt"],
                    "nodeCount": len(wf["nodes"]),
                }
            except Exception:
                # 损坏文件不拖垮列表
                merged[name] = {
                    "name": name,
                    "description": "（文件解析失败）",
                    "source": source,
                    "updatedAt": "",
                    "nodeCount": 0,
                }
    return sorted(merged.values(), key=lambda x: x["name"])


def load_workflow(name: str, data_dir: str | Path | None) -> Dict[str, Any]:
    """加载完整工作流（用户库优先，其次内置）。返回 camelCase dict。"""
    _safe_name(name)
    user_root = infer_user_workflows_dir(data_dir)
    if user_root:
        user_file = _workflow_file(user_root, name)
        if user_file.exists():
            return workflow_from_markdown(user_file.read_text(encoding="utf-8"), "user")
    builtin_file = _workflow_file(BUILTIN_WORKFLOWS_DIR, name)
    if builtin_file.exists():
        return workflow_from_markdown(builtin_file.read_text(encoding="utf-8"), "builtin")
    raise FileNotFoundError(f"工作流不存在：{name}")


def save_workflow(wf: Dict[str, Any], data_dir: str | Path | None) -> Path:
    """保存到用户库（原子写）。结构 error 时 raise ValueError。"""
    name = _safe_name(str(wf.get("name") or ""))
    errors = validate_structure(wf)
    if errors:
        raise ValueError("工作流结构校验未通过：" + "；".join(errors))
    root = infer_user_workflows_dir(data_dir)
    if not root:
        raise ValueError("无法确定用户工作流库目录")
    wf = dict(wf)
    wf["name"] = name
    wf["updatedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    target_dir = (root / name).resolve()
    if not str(target_dir).startswith(str(root.resolve())):
        raise ValueError("非法路径")
    target_dir.mkdir(parents=True, exist_ok=True)
    content = workflow_to_markdown(wf)
    fd, tmp_path = tempfile.mkstemp(dir=str(target_dir), prefix=".WORKFLOW.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, target_dir / WORKFLOW_FILENAME)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
    return target_dir / WORKFLOW_FILENAME


def delete_workflow(name: str, data_dir: str | Path | None) -> None:
    """删除用户库中的工作流（内置示例不可删）。"""
    _safe_name(name)
    root = infer_user_workflows_dir(data_dir)
    if not root:
        raise ValueError("无法确定用户工作流库目录")
    target_dir = (root / name).resolve()
    if not str(target_dir).startswith(str(root.resolve())):
        raise ValueError("非法路径")
    if not target_dir.exists():
        builtin_file = _workflow_file(BUILTIN_WORKFLOWS_DIR, name)
        if builtin_file.exists():
            raise ValueError("内置示例不可删除（可复制为用户工作流后删除副本）")
        raise FileNotFoundError(f"工作流不存在：{name}")
    shutil.rmtree(target_dir)


def read_workflow_markdown(name: str, data_dir: str | Path | None) -> str:
    """读取工作流 WORKFLOW.md 原文（用户库优先，其次内置）。供 list_workflows 工具 name 形态。"""
    _safe_name(name)
    user_root = infer_user_workflows_dir(data_dir)
    candidates: List[Path] = []
    if user_root:
        candidates.append(_workflow_file(user_root, name))
    candidates.append(_workflow_file(BUILTIN_WORKFLOWS_DIR, name))
    for wf_file in candidates:
        if wf_file.exists():
            return wf_file.read_text(encoding="utf-8")
    raise FileNotFoundError(f"工作流不存在：{name}")


def archive_workflow_directory(
    source_dir: str | Path,
    data_dir: str | Path | None,
    *,
    overwrite: bool = False,
) -> Dict[str, Any]:
    """把含有 WORKFLOW.md 的目录校验并归档到用户工作流库（对齐 archive_skill_directory）。

    规则（与 save_workflow 工具设计定稿一致）：
    - 目录名必须与 frontmatter 的 name 字段一致
    - overwrite=false 时：用户库已存在同名 → 报错；与内置同名 → 报错（提示将遮蔽内置）
    - 覆盖前先备份旧目录，移动失败自动恢复
    - 成功后源目录随 move 移除
    """
    source = Path(source_dir).expanduser().resolve()
    if not source.exists() or not source.is_dir():
        return {"success": False, "error": "source_dir 不是目录"}
    wf_file = source / WORKFLOW_FILENAME
    if not wf_file.exists() or not wf_file.is_file():
        return {"success": False, "error": f"目录中缺少 {WORKFLOW_FILENAME}"}

    try:
        wf = workflow_from_markdown(wf_file.read_text(encoding="utf-8"), "user")
    except Exception as exc:
        return {"success": False, "error": f"WORKFLOW.md 解析失败：{exc}"}

    raw_name = str(wf.get("name") or "").strip()
    try:
        name = _safe_name(raw_name)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    if source.name != name:
        return {
            "success": False,
            "error": f"目录名（{source.name}）必须与 WORKFLOW.md 的 name 字段（{name}）一致",
        }

    errors = validate_structure(wf)
    if errors:
        return {
            "success": False,
            "error": "结构校验未通过：" + "；".join(errors),
            "validation_errors": errors,
            "workflow_name": name,
        }

    root = infer_user_workflows_dir(data_dir)
    if not root:
        return {"success": False, "error": "无法确定用户工作流库目录"}
    root = root.resolve()
    target = (root / name).resolve()
    if not str(target).startswith(str(root)):
        return {"success": False, "error": "非法路径"}

    existed_user = target.exists()
    existed_builtin = _workflow_file(BUILTIN_WORKFLOWS_DIR, name).exists()
    if not overwrite:
        if existed_user:
            return {
                "success": False,
                "error": f"工作流「{name}」已存在。确认覆盖请设 overwrite=true。",
                "already_exists": True,
                "workflow_name": name,
            }
        if existed_builtin:
            return {
                "success": False,
                "error": (
                    f"与内置工作流「{name}」同名。归档后将创建用户副本遮蔽内置版本，"
                    "确认请设 overwrite=true。"
                ),
                "builtin_conflict": True,
                "workflow_name": name,
            }

    backup: Optional[Path] = None
    if existed_user:
        backup = root / f".{name}.backup-{int(datetime.now().timestamp())}"
        try:
            target.rename(backup)
        except Exception as exc:
            return {"success": False, "error": f"覆盖前备份旧版本失败：{exc}"}
    try:
        shutil.move(str(source), str(target))
    except Exception as exc:
        if backup is not None and backup.exists() and not target.exists():
            try:
                backup.rename(target)
            except Exception:
                pass
        return {"success": False, "error": f"归档移动失败：{exc}", "workflow_name": name}
    if backup is not None and backup.exists():
        try:
            shutil.rmtree(backup)
        except Exception:
            pass

    return {
        "success": True,
        "workflow_name": name,
        "node_count": len(wf.get("nodes") or []),
        "overwritten": existed_user,
        "shadows_builtin": (not existed_user) and existed_builtin,
    }
