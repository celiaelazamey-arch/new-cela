"""
نقطة الدخول الرئيسية لنظام new-cela
====================================
الاستخدام المحلي:
    python -m src.main --task "اكتب دالة Fibonacci في Python" --mode reason
    python -m src.main --task "ابحث عن سعر الذهب اليوم" --mode browser
    python -m src.main --task "python --version" --mode code

الذاكرة الاستراتيجية مفعّلة افتراضياً في النمط reason:
    python -m src.main --task "..." --memory-path .agent_memory/memory.jsonl
    python -m src.main --task "..." --no-memory        # تعطيل كامل للقراءة والكتابة

كل تشغيل يُسجَّل في .agent_runs/ (يقرأه لوحة المتابعة):
    python -m src.dashboard                             # فتح اللوحة على 8501

في GitHub Actions: تُقرأ المهمة من الوسيط --task الذي يمرره سير العمل.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from src.browser.browser_engine import BrowserEngine
from src.config import settings
from src.core.memory import MemoryRetriever, MemoryStore
from src.core.planner import Planner
from src.core.telemetry import RunArchive
from src.execution.sandbox import SandboxExecutor
from src.providers.llm_router import LLMRouter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("new-cela")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="new-cela",
        description="نظام وكلاء ذكي مستقل — تشغيل مجاني بالكامل",
    )
    parser.add_argument("--task", "-t", required=True, help="المهمة المطلوبة من الوكيل")
    parser.add_argument(
        "--mode",
        "-m",
        choices=["reason", "browser", "code"],
        default="reason",
        help="نمط التنفيذ: reason (تفكير) | browser (تصفح) | code (تنفيذ أوامر)",
    )
    parser.add_argument("--max-steps", type=int, default=None, help="الحد الأقصى لخطوات الخطة")
    parser.add_argument(
        "--max-retries",
        type=int,
        default=2,
        help="حد إعادة محاولة كل خطوة عند رفضها من المقيّم (Critic)",
    )
    parser.add_argument(
        "--no-memory",
        action="store_true",
        help="تعطيل الذاكرة الاستراتيجية لهذه المهمة (لا قراءة ولا كتابة)",
    )
    parser.add_argument(
        "--memory-path",
        default=None,
        help="مسار ملف الذاكرة (JSONL) — الافتراضي من الإعدادات",
    )
    parser.add_argument(
        "--memory-top-k",
        type=int,
        default=None,
        help="عدد الذكريات المسترجعة لكل استعلام (الافتراضي 4)",
    )
    parser.add_argument(
        "--runs-dir",
        default=None,
        help="مجلد سجل التشغيلات الذي تقرأه لوحة المتابعة",
    )
    parser.add_argument(
        "--no-telemetry",
        action="store_true",
        help="عدم كتابة تقرير تشغيل (لا سجل ولا مراقبة)",
    )
    return parser


def build_memory(args: argparse.Namespace) -> MemoryRetriever | None:
    """ينشئ طبقة الذاكرة الاستراتيجية — أو None عند التعطيل الصريح."""
    if args.no_memory:
        logger.info("الذاكرة الاستراتيجية معطّلة لهذه المهمة (--no-memory).")
        return None

    store = MemoryStore.from_settings(settings, path=args.memory_path)
    logger.info("الذاكرة الاستراتيجية: %d ذكرى محمّلة من %s", len(store), store.path)
    return MemoryRetriever(
        store,
        top_k=args.memory_top_k or settings.agent_memory_top_k,
        min_score=settings.agent_memory_min_score,
    )


def build_telemetry(args: argparse.Namespace) -> RunArchive | None:
    """ينشئ أرشيف التشغيلات — أو None عند التعطيل الصريح."""
    if args.no_telemetry:
        logger.info("سجل التشغيلات معطّل لهذه المهمة (--no-telemetry).")
        return None

    archive = RunArchive(
        path=args.runs_dir or settings.agent_runs_path,
        keep=settings.agent_runs_keep,
    )
    logger.info("سجل التشغيلات: %s", archive.path)
    return archive


async def run_agent(args: argparse.Namespace) -> str:
    """يوجّه المهمة إلى الوحدة المناسبة حسب النمط المختار."""
    # نمط code لا يحتاج نموذجاً لغوياً — يُنفّذ مباشرة في البيئة المعزولة
    if args.mode == "code":
        result = await SandboxExecutor(timeout=settings.agent_timeout).run(args.task)
        return result.summary()

    router = LLMRouter()
    router.require_any_provider()

    if args.mode == "browser":
        result = await BrowserEngine(router).run_task(args.task, max_steps=args.max_steps)
        return str(result)

    planner = Planner(
        router,
        max_steps=args.max_steps,
        max_retries=args.max_retries,
        memory=build_memory(args),
        telemetry=build_telemetry(args),
    )
    return await planner.solve(args.task)


def main() -> int:
    args = build_parser().parse_args()
    try:
        output = asyncio.run(run_agent(args))
    except Exception as exc:  # noqa: BLE001 — مقصود: حاجز أخير لواجهة CLI
        logger.error("فشل تنفيذ المهمة: %s", exc)
        print(f"❌ {exc}", file=sys.stderr)
        return 1

    print("\n==================== نتيجة الوكيل ====================")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
