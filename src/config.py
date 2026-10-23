"""
الإعدادات المركزية لنظام new-cela
==================================
تُقرأ تلقائياً من متغيرات البيئة أو من ملف .env (انظر .env.example).
قاعدة أمان: لا تُخزَّن أي مفاتيح داخل الكود — العزل أولاً.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """إعدادات الوكيل — قابلة للتهيئة عبر البيئة أو ملف .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- المفاتيح (تُضبط خارج الكود فقط) ----
    gemini_api_key: str | None = None
    groq_api_key: str | None = None
    openrouter_api_key: str | None = None

    # ---- ضبط النماذج والسلوك ----
    agent_primary_model: str = "gemini-2.5-flash"
    agent_fallback_model: str = "llama-3.3-70b-versatile"
    agent_max_steps: int = 15
    agent_timeout: int = 60

    @property
    def has_any_provider(self) -> bool:
        """هل يوجد مزود واحد على الأقل مهيأ؟"""
        return bool(self.gemini_api_key or self.groq_api_key)


settings = Settings()
