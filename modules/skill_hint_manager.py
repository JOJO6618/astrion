# modules/skill_hint_manager.py - Skill 提示系统管理器

import json
from pathlib import Path
from typing import Dict, List, Optional, Set
from utils.logger import setup_logger

logger = setup_logger(__name__)

class SkillHintManager:
    """Skill 提示系统管理器

    检测用户输入中的关键词，并在需要时插入 system 消息提示阅读相关 skill。
    """

    def __init__(self, config_path: Optional[str] = None):
        """初始化提示系统管理器

        Args:
            config_path: 配置文件路径，默认为 config/skill_hints.json
        """
        if config_path is None:
            config_path = Path(__file__).parent.parent / "config" / "skill_hints.json"
        else:
            config_path = Path(config_path)

        self.config_path = config_path
        self.hints_config: Dict[str, Dict] = {}
        self.enabled = False  # 默认关闭

        self._load_config()

    def _load_config(self):
        """加载配置文件"""
        try:
            if self.config_path.exists():
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    self.hints_config = json.load(f)
                logger.info(f"已加载 skill hints 配置: {len(self.hints_config)} 个 skills")
            else:
                logger.warning(f"Skill hints 配置文件不存在: {self.config_path}")
        except Exception as e:
            logger.error(f"加载 skill hints 配置失败: {e}")
            self.hints_config = {}

    def reload_config(self):
        """重新加载配置文件"""
        self._load_config()

    def set_enabled(self, enabled: bool):
        """设置是否启用提示系统

        Args:
            enabled: True 启用，False 禁用
        """
        self.enabled = enabled
        logger.info(f"Skill hints 系统已{'启用' if enabled else '禁用'}")

    def is_enabled(self) -> bool:
        """检查提示系统是否启用"""
        return self.enabled

    def detect_skills(self, user_input: str) -> List[str]:
        """检测用户输入中匹配的 skills

        Args:
            user_input: 用户输入文本

        Returns:
            匹配的 skill 名称列表
        """
        if not self.enabled or not user_input:
            return []

        user_input_lower = user_input.lower()
        matched_skills = []

        for skill_name, config in self.hints_config.items():
            keywords = config.get("keywords", [])
            for keyword in keywords:
                if keyword.lower() in user_input_lower:
                    matched_skills.append(skill_name)
                    break  # 一个 skill 只需匹配一次

        return matched_skills

    def get_hint_message(self, skill_name: str) -> Optional[str]:
        """获取指定 skill 的提示消息

        Args:
            skill_name: skill 名称

        Returns:
            提示消息，如果 skill 不存在则返回 None
        """
        config = self.hints_config.get(skill_name)
        if config:
            return config.get("hint")
        return None

    def build_hint_messages(self, user_input: str) -> List[Dict]:
        """根据用户输入构建提示消息列表

        Args:
            user_input: 用户输入文本

        Returns:
            system 消息列表（如果有多个匹配的 skill，会合并为一条消息）
        """
        if not self.enabled:
            return []

        matched_skills = self.detect_skills(user_input)
        if not matched_skills:
            return []

        # 收集所有提示内容（去掉开头的标识）
        hint_contents = []
        for skill_name in matched_skills:
            hint = self.get_hint_message(skill_name)
            if hint:
                # 去掉 [系统自动插入的提示信息] 标识（如果存在）
                clean_hint = hint.replace("[系统自动插入的提示信息] ", "").strip()
                hint_contents.append(clean_hint)
                logger.info(f"为用户输入添加 skill hint: {skill_name}")

        if not hint_contents:
            return []

        # 合并所有提示为一条消息，开头加上统一的标识
        combined_hint = "[系统自动插入的提示信息] " + "\n".join(hint_contents)

        return [{
            "role": "system",
            "content": combined_hint
        }]

    def add_skill_hint(self, skill_name: str, keywords: List[str], hint: str):
        """动态添加或更新 skill hint 配置

        Args:
            skill_name: skill 名称
            keywords: 关键词列表
            hint: 提示消息
        """
        self.hints_config[skill_name] = {
            "keywords": keywords,
            "hint": hint
        }
        logger.info(f"已添加/更新 skill hint: {skill_name}")

    def remove_skill_hint(self, skill_name: str):
        """移除指定的 skill hint 配置

        Args:
            skill_name: skill 名称
        """
        if skill_name in self.hints_config:
            del self.hints_config[skill_name]
            logger.info(f"已移除 skill hint: {skill_name}")

    def save_config(self):
        """保存配置到文件"""
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.hints_config, f, ensure_ascii=False, indent=2)
            logger.info(f"已保存 skill hints 配置到: {self.config_path}")
        except Exception as e:
            logger.error(f"保存 skill hints 配置失败: {e}")
