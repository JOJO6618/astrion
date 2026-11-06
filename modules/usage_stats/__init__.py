"""用量统计：从对话费用账本派生的跨对话 / 跨工作区汇总（设置页「用量统计」）。

账本 ``<data_dir>/api_costs/<conversation_id>.json`` 是唯一权威；本包只读账本、
维护可重建的小时级缓存，不写账本、不创建执行实例。
"""
from .cache import collect_ledger_dir
from .report import SourceBuckets, build_report
from .rollup import rollup_ledger
from .sources import CHAT_ID, UNASSIGNED_ID, WorkspaceRef, gather_sources, source_id

__all__ = [
    "CHAT_ID",
    "UNASSIGNED_ID",
    "SourceBuckets",
    "WorkspaceRef",
    "build_report",
    "collect_ledger_dir",
    "gather_sources",
    "rollup_ledger",
    "source_id",
]
