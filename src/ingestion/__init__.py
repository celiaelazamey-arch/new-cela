"""خط أنابيب استيعاب واستخراج الملفات الطبقي الآمن (Layered File Ingestion Pipeline v2.0)."""
from src.ingestion.pipeline import (
    PIPELINE_VERSION,
    IngestionLimits,
    IngestionPipeline,
    IngestionResult,
    IngestionStatus,
    detect_and_decode_text,
    detect_and_decode_text_with_confidence,
    detect_magic_mime,
    ingest_file,
    ingest_file_isolated,
    safe_xml_fromstring,
    sanitize_filename,
    sanitize_svg_or_html,
    scan_for_malware,
)

__all__ = [
    "PIPELINE_VERSION",
    "IngestionLimits",
    "IngestionPipeline",
    "IngestionResult",
    "IngestionStatus",
    "detect_and_decode_text",
    "detect_and_decode_text_with_confidence",
    "detect_magic_mime",
    "ingest_file",
    "ingest_file_isolated",
    "safe_xml_fromstring",
    "sanitize_filename",
    "sanitize_svg_or_html",
    "scan_for_malware",
]
