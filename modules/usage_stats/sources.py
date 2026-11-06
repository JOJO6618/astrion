"""把工作区 / Chat 的账本目录归集为统计来源。

- 每个工作区的账本在 ``<data_dir>/api_costs/``；Chat 在 ``<chat root>/api_costs/``。
- host 模式所有工作区共用同一个 DATA_DIR（账本目录共享），按各工作区对话目录里的
  ``conv_*.json`` 文件名归属；找不到归属的账本（对话或工作区已删除）归入「其它对话」，
  不丢弃费用，也不猜测归属。
"""
from __future__ import annotations

import os
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .cache import collect_ledger_dir
from .report import SourceBuckets

UNASSIGNED_ID = "ws:__unassigned__"
CHAT_ID = "chat"


@dataclass(frozen=True)
class WorkspaceRef:
    workspace_id: str
    label: str
    data_dir: Path
    conversations_dir: Path


def source_id(workspace_id: str) -> str:
    return f"ws:{workspace_id}"


def _conversation_ids(directory: Path) -> set:
    try:
        return {
            entry.name[:-5] for entry in os.scandir(directory)
            if entry.name.startswith("conv_") and entry.name.endswith(".json")
        }
    except OSError:
        return set()


def gather_sources(workspaces: List[WorkspaceRef], chat_root: Optional[Path], *,
                   shared_ledger: bool) -> List[SourceBuckets]:
    """返回全部来源（含空来源，供筛选下拉展示）；顺序 = 工作区原顺序 → 其它对话 → Chat。"""
    groups: "OrderedDict[str, List[WorkspaceRef]]" = OrderedDict()
    for ref in workspaces:
        key = str((Path(ref.data_dir) / "api_costs").resolve())
        groups.setdefault(key, []).append(ref)

    out: Dict[str, SourceBuckets] = OrderedDict()
    for ref in workspaces:
        out[source_id(ref.workspace_id)] = SourceBuckets(source_id(ref.workspace_id), ref.label, "agent")
    unassigned = SourceBuckets(UNASSIGNED_ID, "", "agent")

    for ledger_dir, refs in groups.items():
        conversations = collect_ledger_dir(Path(ledger_dir))
        if not conversations:
            continue
        if len(refs) == 1 and not shared_ledger:
            out[source_id(refs[0].workspace_id)].conversations.update(conversations)
            continue
        owner: Dict[str, str] = {}
        for ref in refs:
            for conv_id in _conversation_ids(ref.conversations_dir):
                owner.setdefault(conv_id, source_id(ref.workspace_id))
        for conv_id, buckets in conversations.items():
            target = out.get(owner.get(conv_id, "")) or unassigned
            target.conversations[conv_id] = buckets

    result = list(out.values())
    if unassigned.conversations:
        result.append(unassigned)
    chat = SourceBuckets(CHAT_ID, "", "chat")
    if chat_root is not None:
        chat.conversations = collect_ledger_dir(Path(chat_root) / "api_costs")
    result.append(chat)
    return result
