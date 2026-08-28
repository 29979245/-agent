"""OCR 引擎包：统一协议 + 三引擎 + correct_edu stub + 双引擎路由降级链。"""
from app.services.ocr.engines.base import (
    MIN_OCR_TEXT_CHARS,
    OCRDocument,
    OCRResult,
    OCREngineError,
    OCRProvider,
)
from app.services.ocr.engines.baidu_engine import BaiduOCRCallError, BaiduOCREngine
from app.services.ocr.engines.correct_edu import CorrectEduEngine
from app.services.ocr.engines.mineru_engine import (
    MinerUEngine,
    MinerUUnavailableError,
    check_available,
)
from app.services.ocr.engines.router import (
    IMAGE_EXTS,
    PDF_EXTS,
    build_chain,
    classify_document,
    extract_document,
)
from app.services.ocr.engines.vlm_fallback import VLMFallbackEngine, VLMError

__all__ = [
    "MIN_OCR_TEXT_CHARS",
    "OCRDocument",
    "OCRResult",
    "OCREngineError",
    "OCRProvider",
    "BaiduOCRCallError",
    "BaiduOCREngine",
    "CorrectEduEngine",
    "MinerUEngine",
    "MinerUUnavailableError",
    "check_available",
    "VLMFallbackEngine",
    "VLMError",
    "IMAGE_EXTS",
    "PDF_EXTS",
    "build_chain",
    "classify_document",
    "extract_document",
]
