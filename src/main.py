"""
نقطة الدخول الرئيسية لنظام new-cela
====================================
الاستخدام المحلي:
    python -m src.main --task "اكتب دالة Fibonacci في Python" --mode reason
    python -m src.main --task "ابحث عن سعر الذهب اليوم" --mode browser
    python -m src.main --task "python --version" --mode code

في GitHub Actions: تُقرأ المهمة من الوسيط --task الذي يمرره سير العمل.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from src.browser.browser_engine import BrowserEngine
from src.config import settings
from src.core.planner import Planner
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
    parser.add_argument("--max-steps", type=int, default=None, help="الحد الأقصى لخطوات الوكيل")
    return parser


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

    planner = Planner(router, max_steps=args.max_steps)
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
