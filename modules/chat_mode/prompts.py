"""Chat prompt assembly contains no workspace, execution or Agent policy data."""
from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from pathlib import Path

from modules.personalization_manager import build_personalization_prompt, load_personalization_config

PROMPTS = Path(__file__).resolve().parents[2] / "prompts"


@lru_cache(maxsize=8)
def template(name):
    return (PROMPTS / name).read_text(encoding="utf-8")


def build_system(runtime):
    preferences = load_personalization_config(runtime.preferences_dir)
    document = runtime.storage.read(runtime.conversation_id)
    memories = runtime.memory_manager.snapshot()
    profile = runtime.model_profile or {}
    model_name = profile.get("display_name") or profile.get("name") or runtime.model_key
    parts = [template("chat_system.txt"), f"当前模型：{model_name}"]
    parts += [template("show_ui.txt"), template("show_html.txt")]
    personal = build_personalization_prompt(preferences)
    if personal:
        parts.append(personal)
    if preferences.get("chat_instructions"):
        parts.append("## Chat 专属个人指令\n" + preferences["chat_instructions"])
    if memories["global"]:
        parts.append("## Chat 全局记忆\n" + "\n".join(
            f"{i}. {entry}" for i, entry in enumerate(memories["global"], 1)
        ))
    if memories["topics"]:
        parts.append("## Chat 专题记忆索引\n" + "\n".join(
            f"{name}：{topic['description']}" for name, topic in memories["topics"].items()
        ))
    now = datetime.now().astimezone()
    network = "开启" if document["metadata"]["network_enabled"] else "关闭"
    parts.append(f"当前时间：{now.isoformat(timespec='minutes')}；时区：{now.tzname()}；联网：{network}。")
    return "\n\n".join(parts)
