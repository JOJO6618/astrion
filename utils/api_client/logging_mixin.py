# ========== api_client.py ==========
# utils/api_client.py - OpenAI-compatible API 客户端（支持Web模式）

import httpx
import json
import asyncio
import base64
import mimetypes
import os
from typing import List, Dict, Optional, AsyncGenerator, Any
from pathlib import Path
from datetime import datetime
from pathlib import Path
from typing import Tuple
try:
    from config import (
        OUTPUT_FORMATS,
        DEFAULT_RESPONSE_MAX_TOKENS,
        LOGS_DIR,
    )
except ImportError:
    import sys
    from pathlib import Path
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from config import (
        OUTPUT_FORMATS,
        DEFAULT_RESPONSE_MAX_TOKENS,
        LOGS_DIR,
    )

from utils.log_rotation import append_line, prune_dir



from utils.api_client.utils import _api_dump_enabled

class APIClientLoggingMixin:
    def _debug_log(self, payload: Dict[str, Any]) -> None:
        if not _api_dump_enabled():
            return
        try:
            entry = {
                "ts": datetime.now().isoformat(),
                **payload
            }
            append_line(self.debug_log_path, json.dumps(entry, ensure_ascii=False))
        except Exception:
            pass

    def _dump_request_payload(self, payload: Dict, api_config: Dict, headers: Dict) -> Optional[Path]:
        """
        将本次请求的payload、headers、配置落盘，便于排查400等错误。
        返回写入的文件路径；落盘开关关闭（默认）时返回 None。
        """
        if not _api_dump_enabled():
            return None
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"req_{timestamp}.json"
        path = self.request_dump_dir / filename
        try:
            self.request_dump_dir.mkdir(parents=True, exist_ok=True)
            headers_sanitized = {}
            for k, v in headers.items():
                headers_sanitized[k] = "***" if k.lower() == "authorization" else v
            data = {
                "timestamp": datetime.now().isoformat(),
                "api_config": {k: api_config.get(k) for k in ["base_url", "model_id"]},
                "headers": headers_sanitized,
                "payload": payload
            }
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            # 按份保留：只留最近 N 个请求落盘文件
            prune_dir(self.request_dump_dir, pattern="req_*.json")
        except Exception as exc:
            self._print(f"{OUTPUT_FORMATS['warning']} 请求体落盘失败: {exc}")
        return path

    def _mark_request_error(self, dump_path: Path, status_code: int = None, error_text: str = None):
        """
        在已有请求文件中追加错误标记，便于快速定位。
        """
        if not dump_path or not dump_path.exists():
            return
        try:
            data = json.loads(dump_path.read_text(encoding="utf-8"))
            data["error"] = {
                "status_code": status_code,
                "message": error_text,
                "marked_at": datetime.now().isoformat()
            }
            dump_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            self._print(f"{OUTPUT_FORMATS['warning']} 标记请求错误失败: {exc}")
