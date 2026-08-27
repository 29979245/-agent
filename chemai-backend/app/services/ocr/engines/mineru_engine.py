"""MinerU 引擎（PDF 主路由）。

运行时可用性检测占位（ADR 0004）：try-except 导入 + check_available()。
模型未下载 / 磁盘不足 / 未安装 → 标记不可用，抛 MinerUUnavailableError 供路由
降级到百度 OCR + VLM。测试环境 MinerU 未安装，check_available() 恒 False。
"""
from __future__ import annotations

from app.services.ocr.engines.base import OCRDocument, OCRResult, OCREngineError

try:
    import mineru  # type: ignore  # MinerU 包未安装时捕获 ImportError

    _MINERU_IMPORTED = True
except Exception:  # noqa: BLE001 - 运行时可检测性：安装异常也视为不可用
    _MINERU_IMPORTED = False


class MinerUUnavailableError(OCREngineError):
    """MinerU 不可用（未安装 / 模型未下载 / 磁盘不足）。"""


def _model_ready() -> bool:
    """模型就绪检测（占位）：真实接入 MinerU 时检查模型目录与磁盘余量。"""
    if not _MINERU_IMPORTED:
        return False
    return False  # 模型未下载检测占位


def check_available() -> bool:
    """MinerU 运行时可用性：未安装/模型未就绪一律不可用。"""
    return _model_ready()


class MinerUEngine:
    name = "mineru"

    async def extract(self, document: OCRDocument) -> OCRResult:
        if not check_available():
            raise MinerUUnavailableError("MinerU 模型未下载或磁盘不足，标记不可用")
        raise MinerUUnavailableError("MinerU 解析尚未接入（协议 stub）")
