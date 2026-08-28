"""彩蛋触发管理器。

负责根据工具参数返回彩蛋元数据，供前端渲染对应特效。
"""

from __future__ import annotations

from typing import Dict, List

from modules.i18n import tr


class EasterEggManager:
    """管理隐藏彩蛋效果的触发逻辑。"""

    def __init__(self) -> None:
        # 预置彩蛋效果元数据，新增特效可在此扩展
        self.effects: Dict[str, Dict[str, object]] = {
            "flood": {
                "label": "灌水",
                "aliases": ["flood", "water", "wave", "灌水", "注水"],
                "message": tr("easter_egg.flood_message"),
                "duration_seconds": 45,
                "intensity_range": (0.87, 0.93),
                "notes": "特效为半透明覆盖层，不会阻挡交互。",
            },
            "snake": {
                "label": "贪吃蛇",
                "aliases": ["snake", "贪吃蛇", "snakegame", "snake_game"],
                "message": tr("easter_egg.snake_message"),
                "duration_seconds": 200,
                "notes": "动画为独立 Canvas，不影响页面点击。",
                "apples_target": 20,
                "initial_apples": 3,
            },
        }

    def trigger_effect(self, effect: str) -> Dict[str, object]:
        """
        根据传入的 effect 名称查找彩蛋。

        Args:
            effect: 彩蛋标识或别名。

        Returns:
            dict: 包含触发状态与前端所需的特效参数。
        """
        effect_key = (effect or "").strip().lower()
        if not effect_key:
            return self._build_error(tr("easter_egg.missing_effect"))

        for effect_id, metadata in self.effects.items():
            aliases = metadata.get("aliases", [])
            if effect_key == effect_id or effect_key in aliases:
                payload = {
                    "success": True,
                    "effect": effect_id,
                    "display_name": metadata.get("label", effect_id),
                }
                payload.setdefault("duration_seconds", 30)
                for key, value in metadata.items():
                    if key in {"label", "aliases"}:
                        continue
                    payload[key] = value
                return payload

        return self._build_error(tr("easter_egg.unknown_effect", effect=effect_key))

    def _build_error(self, message: str) -> Dict[str, object]:
        """返回格式化的错误信息。"""
        return {
            "success": False,
            "error": message,
            "available_effects": self.available_effects,
        }

    @property
    def available_effects(self) -> List[str]:
        """返回可用彩蛋 ID 列表。"""
        return list(self.effects.keys())
