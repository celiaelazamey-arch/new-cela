"""
مشغّل لوحة المتابعة
===================
    python -m src.dashboard                 # http://0.0.0.0:8501
    python -m src.dashboard --port 8600     # منفذ مخصص
    python -m src.dashboard --runs-dir .agent_runs/demo --memory .agent_memory/demo/memory.jsonl

لماذا تعطيل CORS/XSRF؟ لأن اللوحة **للقراءة فقط** وتُعرَض غالباً خلف وسيط عكسي
(بيئة معاينة/سيرفر داخلي) لا يمرّر الكوكيز. لا تُضِف أي عملية كتابة لهذه الواجهة
دون إعادة تفعيل الحمايتين.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def build_command(argv: list[str] | None = None) -> tuple[list[str], argparse.Namespace]:
    """يبني وسائط Streamlit دون تشغيلها — قابل للاختبار."""
    parser = argparse.ArgumentParser(
        prog="python -m src.dashboard",
        description="لوحة متابعة new-cela (قراءة فقط)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", "8501")),
        help="منفذ الخدمة (افتراضياً 8501)",
    )
    parser.add_argument(
        "--runs-dir",
        default=None,
        help="مجلد سجل التشغيلات (يُضبط كـ AGENT_RUNS_PATH)",
    )
    parser.add_argument(
        "--memory",
        default=None,
        help="ملف الذاكرة الاستراتيجية (يُضبط كـ AGENT_MEMORY_PATH)",
    )
    parser.add_argument(
        "--address",
        default="0.0.0.0",
        help="عنوان الاستماع (افتراضياً 0.0.0.0)",
    )
    args = parser.parse_args(argv)

    # الإعدادات تُقرأ داخل عملية Streamlit نفسها، فمتغيرات البيئة هي القناة
    if args.runs_dir:
        os.environ["AGENT_RUNS_PATH"] = args.runs_dir
    if args.memory:
        os.environ["AGENT_MEMORY_PATH"] = args.memory

    app_path = Path(__file__).with_name("app.py")
    command = [
        "streamlit",
        "run",
        str(app_path),
        f"--server.port={args.port}",
        f"--server.address={args.address}",
        "--server.headless=true",
        "--server.enableCORS=false",
        "--server.enableXsrfProtection=false",
        "--server.enableWebsocketCompression=false",
        "--browser.gatherUsageStats=false",
    ]
    return command, args


def main(argv: list[str] | None = None) -> int:
    command, args = build_command(argv)
    try:
        from streamlit.web import cli as streamlit_cli
    except ImportError:
        print(
            "⚠️ Streamlit غير مثبت. نفّذ: pip install streamlit>=1.40,<2",
            file=sys.stderr,
        )
        return 1

    print(f"🛰️  لوحة المتابعة: http://{args.address}:{args.port}")
    print(f"    سجل التشغيلات: {os.environ.get('AGENT_RUNS_PATH', '.agent_runs')}")
    print(f"    الذاكرة: {os.environ.get('AGENT_MEMORY_PATH', '.agent_memory/memory.jsonl')}")
    sys.argv = command
    streamlit_cli.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
