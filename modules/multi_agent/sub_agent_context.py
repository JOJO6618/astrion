"""多智能体子智能体动态上下文构建。

在子智能体创建时，为其系统提示词注入工作区相关信息：
- 执行环境（宿主机/docker + 沙箱/直接）
- AGENTS.md 项目规范
- 工作区信息（项目名称、运行环境、当前时间，不含 file_tree / recent_uploads）
- .astrion/ 目录结构说明（skills / memory / review / user_upload）
- 可用 skill 列表
- 项目记忆索引

所有信息在子智能体创建那一刻快照写入 system_prompt.txt，后续冻结不更新。
"""
from __future__ import annotations

import platform
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from config import (
        PROMPTS_DIR,
        WORKSPACE_SKILLS_DIRNAME,
        WORKSPACE_MEMORY_DIRNAME,
        WORKSPACE_REVIEW_DIRNAME,
        TERMINAL_SANDBOX_MODE,
    )
    from config.paths import IS_HOST_MODE
except ImportError:
    import sys

    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from config import (
        PROMPTS_DIR,
        WORKSPACE_SKILLS_DIRNAME,
        WORKSPACE_MEMORY_DIRNAME,
        WORKSPACE_REVIEW_DIRNAME,
        TERMINAL_SANDBOX_MODE,
    )
    from config.paths import IS_HOST_MODE


def _load_agents_md(workspace_path: str) -> Optional[str]:
    """加载工作区根目录的 AGENTS.md 文件内容。"""
    try:
        project_path = Path(workspace_path)
        if not project_path.exists():
            return None
        agents_md_files = list(project_path.rglob("AGENTS.md"))
        if not agents_md_files:
            return None
        latest_file = max(agents_md_files, key=lambda p: p.stat().st_mtime)
        content = latest_file.read_text(encoding="utf-8")
        return content.strip() if content else None
    except Exception:
        return None


def _build_execution_env_section(workspace_path: str, *, sandbox_mode: str = "") -> str:
    """构建执行环境信息段。

    宿主机模式：复用子智能体共享环境文本（平台细分：Windows 下 sandbox=WSL2 bash、
    direct=cmd，mac/Linux 同为 POSIX shell），创建时快照，切换由运行期通知补充。
    Docker 模式：保持容器语义描述。
    """
    mode = (sandbox_mode or TERMINAL_SANDBOX_MODE or "").lower()
    if IS_HOST_MODE:
        from modules.execution_env_text import build_sub_agent_env_section

        return build_sub_agent_env_section(workspace_path, mode)
    return (
        "## 执行环境\n\n"
        "- 运行环境：Docker 容器模式\n"
        "- 执行方式：容器内执行\n\n"
        "### 当前规则\n"
        "- 所有命令在 Docker 容器内执行\n"
        "- 工作区挂载在容器内 /workspace 路径下\n"
        "- 网络可能受限，仅允许 localhost\n"
    )


def _build_workspace_section(workspace_path: str) -> str:
    """构建工作区信息段（精简版，不含 file_tree 和 recent_uploads）。"""
    project_path = Path(workspace_path)
    project_name = project_path.name or "未命名项目"
    system_info = f"{platform.system()} {platform.release()}"
    current_time = datetime.now().strftime("%Y年%m月%d日 %H点（24小时制）")

    if IS_HOST_MODE:
        resource_limit = "宿主机模式无限制"
    else:
        resource_limit = "容器模式，受 Docker 资源限制"

    return (
        f"## 工作区信息\n\n"
        f"- 项目名称：{project_name}\n"
        f"- 运行环境：{system_info}\n"
        f"- 资源限制：{resource_limit}\n"
        f"- 当前时间：{current_time}\n"
    )


def _build_astrion_dir_section(workspace_path: str) -> str:
    """构建 .astrion/ 目录结构说明段。"""
    return (
        f"## 工作区数据目录（.astrion/）\n\n"
        f"工作区内的 `.astrion/` 目录存储以下内容：\n"
        f"- `{WORKSPACE_SKILLS_DIRNAME}/`：可用技能（skill）目录，每个 skill 含 SKILL.md 说明\n"
        f"- `{WORKSPACE_MEMORY_DIRNAME}/`：项目记忆，以 Markdown 文件形式存放项目约定/决策/调试坑\n"
        f"- `{WORKSPACE_REVIEW_DIRNAME}/`：对话回顾文件\n"
        f"- `user_upload/`：用户上传的文件\n\n"
        f"用户上传的文件位于 `.astrion/user_upload/` 目录下。"
        f"项目记忆以文件形式存放在 `{WORKSPACE_MEMORY_DIRNAME}/` 下。"
        f"技能（skill）存放在 `{WORKSPACE_SKILLS_DIRNAME}/` 下。\n"
    )


def _build_agents_md_section(workspace_path: str) -> str:
    """构建 AGENTS.md 项目规范段。"""
    content = _load_agents_md(workspace_path)
    if not content:
        return ""
    return (
        f"## AGENTS.md 项目规范\n\n"
        f"> 以下是工作区根目录的 AGENTS.md 文件内容，请在回答时参考这些项目规范：\n\n"
        f"{content}\n\n"
        f"---\n"
        f"注意：以上规范来自工作区根目录的 AGENTS.md 文件，若与未来代码冲突，以实际代码为准。\n"
    )


def _build_skills_section(workspace_path: str, data_dir: str = "") -> str:
    """构建可用 skill 列表段。"""
    try:
        from modules.skills_manager import (
            get_skills_catalog,
            build_skills_list,
            infer_private_skills_dir,
        )

        private_dir = infer_private_skills_dir(data_dir) if data_dir else None
        catalog = get_skills_catalog(private_dir=private_dir)
        # 子智能体不区分 enabled/disabled，列出全部可用 skill
        skills_list = build_skills_list(catalog, enabled=None)
        if not skills_list:
            return ""
        return (
            f"## 可用 AgentSkill\n\n"
            f"agent skills 系统已启用，以下是可用的 skills（含简要说明）：\n\n"
            f"{skills_list}\n\n"
            f"使用技能时，优先用 read_skill 通过技能名读取 SKILL.md；"
            f"需要阅读 skill 目录中的其他文件时再使用 read_file\n"
        )
    except Exception:
        return ""


def _build_project_memory_section(workspace_path: str) -> str:
    """构建项目记忆索引段（只列索引，不展开内容）。"""
    try:
        memory_dir = Path(workspace_path) / WORKSPACE_MEMORY_DIRNAME
        if not memory_dir.exists() or not memory_dir.is_dir():
            return ""
        entries: List[str] = []
        for md_file in sorted(memory_dir.glob("*.md")):
            try:
                text = md_file.read_text(encoding="utf-8")
                name = None
                description = None
                if text.startswith("---"):
                    end = text.find("---", 3)
                    if end > 0:
                        frontmatter = text[3:end]
                        for line in frontmatter.strip().split("\n"):
                            ls = line.strip()
                            if ls.startswith("name:"):
                                name = ls.split(":", 1)[1].strip()
                            elif ls.startswith("description:"):
                                description = ls.split(":", 1)[1].strip()
                file_name = md_file.name
                display_name = name or md_file.stem
                desc = description or ""
                if desc:
                    entries.append(f".astrion/memory/{file_name}：{desc}")
                else:
                    entries.append(f".astrion/memory/{file_name}：当需要{display_name}时，应该索引本记忆")
            except Exception:
                pass
        if not entries:
            return ""
        return (
            f"## 项目记忆\n\n"
            f"项目记忆以文件形式存放在 `.astrion/memory/` 下。\n"
            f"开始涉及项目约定、历史决策或已知问题的任务前，先检查项目记忆索引，"
            f"不确定时先用 search_project_memory 检索正文，定位后用 recall_project_memory 读取。\n\n"
            f"### 项目记忆索引\n\n"
            + "\n".join(entries)
            + "\n"
        )
    except Exception:
        return ""


def build_sub_agent_dynamic_context(
    workspace_path: str,
    *,
    data_dir: str = "",
    sandbox_mode: str = "",
) -> str:
    """构建子智能体的动态上下文信息（拼接到 system prompt 末尾）。

    所有信息在子智能体创建那一刻快照，后续冻结不更新。

    Args:
        workspace_path: 工作区路径
        data_dir: 数据目录路径（用于推断 private skills 目录）
        sandbox_mode: 沙箱模式（sandbox/direct/host/docker）
    """
    sections: List[str] = []

    # 1. 执行环境
    sections.append(_build_execution_env_section(workspace_path, sandbox_mode=sandbox_mode))

    # 2. 工作区信息（精简版）
    sections.append(_build_workspace_section(workspace_path))

    # 3. .astrion/ 目录说明
    sections.append(_build_astrion_dir_section(workspace_path))

    # 4. AGENTS.md
    agents_md = _build_agents_md_section(workspace_path)
    if agents_md:
        sections.append(agents_md)

    # 5. 可用 skill
    skills = _build_skills_section(workspace_path, data_dir=data_dir)
    if skills:
        sections.append(skills)

    # 6. 项目记忆索引
    memory = _build_project_memory_section(workspace_path)
    if memory:
        sections.append(memory)

    return "\n".join(sections)
