"""خط أنابيب استيعاب واستخراج الملفات الطبقي الآمن (Layered File Ingestion Pipeline)."""
from src.ingestion.pipeline import (
    IngestionLimits,
    IngestionPipeline,
    IngestionResult,
    IngestionStatus,
    detect_and_decode_text,
    detect_magic_mime,
    ingest_file,
    sanitize_filename,
    sanitize_svg_or_html,
)

__all__ = [
    "IngestionLimits",
    "IngestionPipeline",
    "IngestionResult",
    "IngestionStatus",
    "detect_and_decode_text",
    "detect_magic_mime",
    "ingest_file",
    "sanitize_filename",
    "sanitize_svg_or_html",
]
