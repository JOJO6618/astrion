"""视觉语言模型客户端（主智能体专用）。"""

import base64
import mimetypes
from pathlib import Path
from typing import Dict, List

import httpx
from openai import OpenAI

from config import OCR_API_BASE_URL, OCR_API_KEY, OCR_MODEL_ID, OCR_MAX_TOKENS
from modules.file_manager import FileManager


class OCRClient:
    """封装外部 VLM 调用逻辑。"""

    def __init__(self, project_path: str, file_manager: FileManager):
        self.project_path = Path(project_path).resolve()
        self.file_manager = file_manager

        # 懒加载：httpx.Client() 构造会加载 CA 证书并初始化 SSL 上下文，
        # 在 Windows（Defender 实时扫描）下可耗时数秒；每个对话级 terminal
        # 都会构造 OCRClient，因此推迟到首次真正调用 VLM 时再创建。
        self.http_client = None
        self.client = None
        self._client_ready = False
        self.model = OCR_MODEL_ID
        self.max_tokens = OCR_MAX_TOKENS or 4096

        # 默认大小上限（10MB），超出则警告并拒绝
        self.max_image_size = 10 * 1024 * 1024

    def _ensure_client(self):
        """首次使用时创建 httpx / OpenAI 客户端（线程安全由 GIL 保证最坏情况重复创建一次）。"""
        if self._client_ready:
            return
        # 补全 base_url，兼容是否包含 /v1
        base_url = (OCR_API_BASE_URL or "").rstrip("/")
        if base_url and not base_url.endswith("/v1"):
            base_url = f"{base_url}/v1"

        # httpx 0.28 起不再支持 proxies 参数，显式传入 http_client 以避免默认封装报错
        self.http_client = httpx.Client()
        if OCR_API_KEY:
            self.client = OpenAI(
                api_key=OCR_API_KEY,
                base_url=base_url or None,
                http_client=self.http_client,
            )
        self._client_ready = True

    def _validate_image_path(self, path: str):
        """复用 FileManager 的路径校验，确保在项目内。"""
        valid, error, full_path = self.file_manager._validate_path(path)
        if not valid:
            return False, error, None
        if not full_path.exists():
            return False, "文件不存在", None
        if not full_path.is_file():
            return False, "不是文件", None
        return True, "", full_path

    def vlm_analyze(self, path: str, prompt: str) -> Dict:
        """使用大参数视觉语言模型分析图片：文字、物体、布局等。"""
        warnings: List[str] = []

        valid, error, full_path = self._validate_image_path(path)
        if not valid:
            return {"success": False, "error": error, "warnings": warnings}

        if not prompt or not str(prompt).strip():
            return {"success": False, "error": "prompt 不能为空", "warnings": warnings}
        if not OCR_API_KEY or not OCR_API_BASE_URL or not self.model:
            return {"success": False, "error": "VLM 配置缺失，请设置 OCR_API_BASE_URL / OCR_API_KEY / OCR_MODEL_ID", "warnings": warnings}
        self._ensure_client()
        if not self.client:
            return {"success": False, "error": "VLM 客户端初始化失败", "warnings": warnings}

        try:
            data = full_path.read_bytes()
        except Exception as exc:
            return {"success": False, "error": f"读取文件失败: {exc}", "warnings": warnings}

        size = len(data)
        if size <= 0:
            return {"success": False, "error": "文件为空，无法识别", "warnings": warnings}

        if size > self.max_image_size:
            return {
                "success": False,
                "error": f"图片过大({size}字节)，上限为{self.max_image_size}字节",
                "warnings": warnings,
            }

        mime_type, _ = mimetypes.guess_type(str(full_path))
        if not mime_type or not mime_type.startswith("image/"):
            warnings.append("无法确定图片类型，已按 JPEG 处理")
            mime_type = "image/jpeg"

        base64_image = base64.b64encode(data).decode("utf-8")
        data_url = f"data:{mime_type};base64,{base64_image}"

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": data_url}},
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
                max_tokens=self.max_tokens,
                temperature=0,
            )
            content = response.choices[0].message.content if response.choices else ""
            return {"success": True, "content": content or "", "warnings": warnings}
        except Exception as exc:
            return {"success": False, "error": f"VLM 调用失败: {exc}", "warnings": warnings}

    def ocr_image(self, path: str, prompt: str) -> Dict:
        """兼容旧名，转发到 vlm_analyze。"""
        return self.vlm_analyze(path, prompt)
