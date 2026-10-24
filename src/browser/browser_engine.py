"""
محرك أتمتة المتصفح (Manus-like Browser Engine)
=============================================
يبني على browser-use + Playwright للتحكم في متصفح Chromium
وتنفيذ المهام الحية على الويب: تصفح، بحث، استخراج بيانات، تعبئة نماذج.

عقد الاستخدام مُتحقَّق منه مقابل browser-use 0.9.x:
    Agent(task=..., llm=...) ثم await agent.run(max_steps=...)
حيث llm من نوع browser_use.llm.base.BaseChatModel (ChatGoogle أو ChatGroq).

⚠️ هذه **ميزة اختيارية**: `browser-use` لم يعد في المتطلبات الأساسية.
ثبّته عبر `pip install -r requirements-browser.txt` عند الحاجة فقط.
"""
from __future__ import annotations

import logging
from typing import Any

from src.providers.llm_router import LLMRouter

logger = logging.getLogger("new-cela.browser")


class BrowserEngine:
    """وحدة أتمتة وتصفح الويب المتقدمة للوكيل."""

    def __init__(self, router: LLMRouter | None = None, headless: bool = True) -> None:
        self.router = router or LLMRouter()
        self.headless = headless

    async def run_task(self, task_description: str, max_steps: int | None = None) -> Any:
        """
        ينفّذ مهمة تصفح حية.

        مثال:
            result = await BrowserEngine().run_task("ابحث عن سعر الذهب اليوم في القاهرة")
        """
        try:
            from browser_use import Agent
        except ImportError as exc:
            raise RuntimeError(
                "مكتبة browser-use غير مثبتة. نفّذ: pip install -r requirements.txt"
            ) from exc

        llm = self.router.get_chat_model()
        logger.info("تشغيل مهمة التصفح: %s", task_description[:80])

        agent = Agent(
            task=task_description,
            llm=llm,
        )
        result = await agent.run(max_steps=max_steps or self.router.settings.agent_max_steps)
        logger.info("انتهت مهمة التصفح.")
        return result
