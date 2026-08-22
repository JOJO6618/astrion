# utils/logger.py - 日志系统

import logging
import os
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
try:
    from config import LOGS_DIR, LOG_LEVEL, LOG_FORMAT
except ImportError:
    import sys
    from pathlib import Path
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from config import LOGS_DIR, LOG_LEVEL, LOG_FORMAT

from utils.log_rotation import DEFAULT_MAX_BYTES, DEFAULT_BACKUPS

def _env_flag(name: str, default: bool = False) -> bool:
    value = str(os.environ.get(name, "")).strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


_FILE_LOG_ENABLED = _env_flag("AGENT_FILE_LOG_ENABLED", default=False)

def setup_logger(name: str, log_file: str = None) -> logging.Logger:
    """
    设置日志记录器
    
    Args:
        name: 日志记录器名称
        log_file: 日志文件路径（可选）
    
    Returns:
        配置好的日志记录器
    """
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, LOG_LEVEL))
    
    # 清除已有的处理器
    logger.handlers.clear()
    
    # 创建格式化器
    formatter = logging.Formatter(LOG_FORMAT)
    
    # 控制台处理器（与全局 LOG_LEVEL 保持一致，便于实时查看）
    console_handler = logging.StreamHandler()
    console_handler.setLevel(getattr(logging, LOG_LEVEL))
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # 文件处理器（默认关闭，避免在工作区生成 agent_debug/agent_*.log）
    if _FILE_LOG_ENABLED:
        if log_file:
            file_path = Path(LOGS_DIR) / log_file
        else:
            # 默认日志文件
            today = datetime.now().strftime("%Y%m%d")
            file_path = Path(LOGS_DIR) / f"agent_{today}.log"

        # 确保日志目录存在
        file_path.parent.mkdir(parents=True, exist_ok=True)

        file_handler = RotatingFileHandler(
            file_path,
            maxBytes=DEFAULT_MAX_BYTES,
            backupCount=DEFAULT_BACKUPS,
            encoding='utf-8',
        )
        file_handler.setLevel(getattr(logging, LOG_LEVEL))
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger

class TaskLogger:
    """任务专用日志记录器"""
    
    def __init__(self, task_id: str):
        self.task_id = task_id
        self.log_file = Path(LOGS_DIR) / "tasks" / f"{task_id}.log"
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        
        self.logger = setup_logger(f"task_{task_id}", str(self.log_file))
    
    def log_action(self, action: str, details: dict = None):
        """记录操作"""
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "details": details or {}
        }
        self.logger.info(f"ACTION: {log_entry}")
    
    def log_result(self, success: bool, message: str, data: dict = None):
        """记录结果"""
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "success": success,
            "message": message,
            "data": data or {}
        }
        
        if success:
            self.logger.info(f"RESULT: {log_entry}")
        else:
            self.logger.error(f"RESULT: {log_entry}")
    
    def log_error(self, error: Exception, context: str = ""):
        """记录错误"""
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "error": str(error),
            "type": type(error).__name__,
            "context": context
        }
        self.logger.error(f"ERROR: {log_entry}", exc_info=True)
    
    def get_log_content(self) -> str:
        """获取日志内容"""
        if self.log_file.exists():
            with open(self.log_file, 'r', encoding='utf-8') as f:
                return f.read()
        return ""

class ErrorLogger:
    """错误专用日志记录器"""
    
    @staticmethod
    def log_error(module: str, error: Exception, context: dict = None):
        """记录错误到错误日志"""
        today = datetime.now().strftime("%Y%m%d")
        error_file = Path(LOGS_DIR) / "errors" / f"errors_{today}.log"
        error_file.parent.mkdir(parents=True, exist_ok=True)
        
        logger = setup_logger(f"error_{module}", str(error_file))
        
        error_entry = {
            "timestamp": datetime.now().isoformat(),
            "module": module,
            "error": str(error),
            "type": type(error).__name__,
            "context": context or {}
        }
        
        logger.error(f"ERROR: {error_entry}", exc_info=True)
