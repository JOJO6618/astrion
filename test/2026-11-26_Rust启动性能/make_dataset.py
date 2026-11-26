#!/usr/bin/env python3.11
"""为启动基准生成隔离的「旧数据」目录（合成，不含任何真实对话）。

构造内容
--------
- host_workspaces.json：1 个合成工作区（id=synthetic，path=<root>/ws）
- data/conversations/synthetic/conv_*.json：N 个对话，每个 M 条消息
- data/conversation_index 由 ConversationManager 自己维护（走产品代码，不手写 schema）
- config/ 下放最小策略文件，使两个入口都不落到仓库真实配置

不使用真实数据目录，不读取 ~/.astrion。

用法：
  python3.11 make_dataset.py --root /path/to/data-root --conversations 240 --messages 60
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def build_registry(root: Path, workspace: Path) -> Path:
    config_dir = root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    registry = config_dir / "host_workspaces.json"
    registry.write_text(
        json.dumps(
            {
                "default_workspace_id": "synthetic",
                "workspaces": [
                    {"workspace_id": "synthetic", "label": "synthetic", "path": str(workspace)}
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return registry


def make_messages(count: int) -> list[dict]:
    messages: list[dict] = []
    for i in range(count):
        if i % 2 == 0:
            messages.append(
                {
                    "role": "user",
                    "content": f"第 {i} 条合成消息：检查启动耗时与索引构建，不包含任何真实内容。",
                }
            )
        else:
            messages.append(
                {
                    "role": "assistant",
                    "content": (
                        f"第 {i} 条合成回复：这是用于启动基准的历史对话正文，"
                        "长度按真实对话量级填充。" + "填充文本。" * 12
                    ),
                }
            )
    return messages


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, help="隔离数据根目录")
    parser.add_argument("--conversations", type=int, default=120)
    parser.add_argument("--messages", type=int, default=60)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    workspace = root / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    data_dir = root / "host" / "data"
    logs_dir = root / "host" / "logs"
    config_dir = root / "config"
    for path in (data_dir, logs_dir, config_dir):
        path.mkdir(parents=True, exist_ok=True)
    registry = build_registry(root, workspace)

    # 产品代码在 import 时读取这些环境变量，必须先设置
    os.environ.update(
        DATA_DIR=str(data_dir),
        LOGS_DIR=str(logs_dir),
        DEPLOY_CONFIG_DIR=str(config_dir),
        HOST_WORKSPACES_FILE=str(registry),
        ASTRION_DATA_ROOT=str(root),
        ASTRION_IGNORE_DOTENV="1",
    )
    sys.path.insert(0, str(REPO))

    from utils.conversation_manager import ConversationManager

    manager = ConversationManager(base_dir=str(data_dir), project_path=str(workspace))
    messages = make_messages(args.messages)
    created = 0
    for index in range(args.conversations):
        manager.create_conversation(
            project_path=str(workspace),
            thinking_mode=False,
            run_mode="fast",
            initial_messages=messages,
            model_key="synthetic-model",
            metadata_overrides={"title": f"合成旧对话 {index:03d}"},
        )
        created += 1

    conv_dir = data_dir / "conversations" / "synthetic"
    files = sorted(conv_dir.glob("conv_*.json"))
    total_bytes = sum(p.stat().st_size for p in files)
    print(
        json.dumps(
            {
                "root": str(root),
                "workspace": str(workspace),
                "conversations": created,
                "messages_each": args.messages,
                "files": len(files),
                "bytes": total_bytes,
                "mb": round(total_bytes / 1e6, 2),
                "avg_kb_per_file": round(total_bytes / max(1, len(files)) / 1024, 1),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
