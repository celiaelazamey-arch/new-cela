"""
واجهة OCR سطرية
===============
    python -m src.ocr.cli health
    python -m src.ocr.cli extract scan.png --lang ara+eng
    python -m src.ocr.cli extract docs/*.pdf --ingest .agent_knowledge/corpus.jsonl
    python -m src.ocr.cli extract scan.png --json

أكواد الخروج: 0 نجاح • 1 فشل استخراج • 2 محرّك غير متاح.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from src.ocr.engine import (
    DEFAULT_LANG,
    OcrError,
    OcrUnavailable,
    extract_many,
)
from src.ocr.pipeline import health, ingest_results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.ocr.cli",
        description="استخراج نص من الصور والمستندات الممسوحة (Tesseract مجاني)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    health_cmd = sub.add_parser("health", help="حالة محرّكات OCR")
    health_cmd.add_argument("--json", action="store_true")

    extract = sub.add_parser("extract", help="استخراج نص من ملفات")
    extract.add_argument("paths", nargs="+", help="ملفات صور/PDF/نص")
    extract.add_argument("--lang", default=DEFAULT_LANG, help="لغة Tesseract (مثال ara+eng)")
    extract.add_argument("--ingest", default=None, help="إضافة النص إلى فهرس JSONL")
    extract.add_argument("--json", action="store_true", help="إخراج JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    if args.command == "health":
        info = health()
        if args.json:
            print(json.dumps(info, ensure_ascii=False, indent=2))
            return 0
        print("محرّكات OCR:" if info["ready"] else "⚠️ لا محرّك OCR جاهز:")
        for item in info["backends"]:
            mark = "✅" if item["available"] else "⛔"
            print(f"  {mark} {item['name']} — {item['install']}")
        if not info["ready"]:
            print(f"\n{info['note']}")
        return 0

    try:
        results, failures = extract_many(args.paths, lang=args.lang)
    except OcrUnavailable as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 2
    except OcrError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(
            json.dumps(
                {
                    "results": [result.model_dump() for result in results],
                    "failures": failures,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        for result in results:
            print(f"=== {result.source} ({result.backend}) ===")
            print(result.text[:4000] or "(لا نص)")
            for warning in result.warnings:
                print(f"⚠️ {warning}")
            print()

    for path, reason in failures.items():
        print(f"❌ {path}: {reason}", file=sys.stderr)

    if args.ingest:
        from src.knowledge.corpus import CorpusIndex

        corpus = CorpusIndex(args.ingest)
        report = ingest_results(results, corpus)
        print(f"{report.summary()} — {args.ingest}")
        for path, reason in report.failures.items():
            print(f"  ! {path}: {reason}")

    if not results:
        # التمييز بين «محرّك غائب» و«ملف تالف» مهم للمستخدم
        if any("Unavailable" in reason for reason in failures.values()):
            return 2
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
