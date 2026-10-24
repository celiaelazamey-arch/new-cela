"""اختبارات التقوية المعمارية (2026-10-23).

تغطي: قاطع الدائرة، فحص الجاهزية، تنقيح المخرجات، وفصل المتصفح الاختياري.
كلها بلا شبكة وبلا مفاتيح حقيقية — مجانية 100%.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from src.config import Settings
from src.providers.llm_router import CircuitBreaker, LLMRouter


def _router(**overrides) -> LLMRouter:
    """راوتر بإعدادات اختبار صريحة (بلا قراءة .env)."""
    values: dict[str, object] = {
        "_env_file": None,
        "gemini_api_key": None,
        "groq_api_key": None,
        "openrouter_api_key": None,
    }
    values.update(overrides)
    router = LLMRouter()
    router.settings = Settings(**values)
    return router


# ================= قاطع الدائرة =================

def test_circuit_breaker_opens_after_threshold():
    cb = CircuitBreaker(threshold=3, cooldown=60.0)
    assert cb.allow("gemini") is True
    cb.record_failure("gemini")
    cb.record_failure("gemini")
    assert cb.allow("gemini") is True  # لم يبلغ العتبة بعد
    cb.record_failure("gemini")
    assert cb.allow("gemini") is False  # فُتحت الدائرة


def test_circuit_breaker_resets_on_success():
    cb = CircuitBreaker(threshold=2, cooldown=60.0)
    cb.record_failure("groq")
    cb.record_success("groq")
    cb.record_failure("groq")
    assert cb.allow("groq") is True  # النجاح صفّر العداد


def test_circuit_breaker_reopens_after_cooldown(monkeypatch):
    cb = CircuitBreaker(threshold=1, cooldown=10.0)
    cb.record_failure("gemini")
    assert cb.allow("gemini") is False
    # محاكاة مرور الوقت دون انتظار حقيقي
    import src.providers.llm_router as module

    original = module.time.monotonic
    monkeypatch.setattr(module.time, "monotonic", lambda: original() + 11.0)
    assert cb.allow("gemini") is True  # نصف مفتوح
    monkeypatch.setattr(module.time, "monotonic", original)


def test_provider_order_excludes_open_circuit():
    r = _router(gemini_api_key="x", groq_api_key="y")
    for _ in range(3):
        r.circuit_breaker.record_failure("gemini")
    assert "gemini" not in r._provider_order()
    assert "groq" in r._provider_order()


# ================= فحص الجاهزية =================

def test_health_unhealthy_without_keys():
    report = _router().health()
    assert report["healthy"] is False
    assert report["providers"] == {}


def test_health_healthy_with_key():
    report = _router(gemini_api_key="fake-key").health()
    assert report["healthy"] is True
    assert report["providers"]["gemini"]["configured"] is True
    assert report["providers"]["gemini"]["circuit_open"] is False


def test_health_reflects_open_circuit():
    r = _router(gemini_api_key="fake-key")
    for _ in range(3):
        r.circuit_breaker.record_failure("gemini")
    report = r.health()
    assert report["healthy"] is False
    assert report["providers"]["gemini"]["circuit_open"] is True


def test_health_is_json_serialisable():
    report = _router(gemini_api_key="fake-key").health()
    assert json.loads(json.dumps(report, ensure_ascii=False))["healthy"] is True


# ================= التبديل بين المزودات =================

def test_reasoning_falls_back_to_second_provider(monkeypatch):
    r = _router(gemini_api_key="x", groq_api_key="y")
    calls: list[str] = []

    async def fake_invoke(provider: str, prompt: str) -> str:
        calls.append(provider)
        if provider == "gemini":
            raise RuntimeError("rate limited")
        return "نص من Groq"

    monkeypatch.setattr(r, "_invoke_text", fake_invoke)
    result = asyncio.run(r.execute_reasoning_step("مهمة"))
    assert result == "نص من Groq"
    assert calls == ["gemini", "groq"]


def test_reasoning_raises_when_all_providers_fail(monkeypatch):
    r = _router(gemini_api_key="x", groq_api_key="y")

    async def fake_invoke(provider: str, prompt: str) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr(r, "_invoke_text", fake_invoke)
    with pytest.raises(RuntimeError, match="فشلت كل المزودات"):
        asyncio.run(r.execute_reasoning_step("مهمة"))


def test_require_any_provider_message_has_no_secret():
    with pytest.raises(RuntimeError) as exc:
        _router().require_any_provider()
    assert "GEMINI_API_KEY" in str(exc.value)


# ================= المتصفح اختياري =================

def test_get_chat_model_requires_provider():
    r = _router()
    with pytest.raises(RuntimeError, match="مفتاح API"):
        r.get_chat_model()


# ================= تنقيح المخرجات =================

def test_sanitize_redacts_secrets_and_pii():
    from scripts.sanitize_output import sanitize

    raw = ("المفتاح AIzaSyDUMMY1234567890123456789012345 "
           "والايميل user@example.com والهاتف 01012345678")
    cleaned = sanitize(raw)
    assert "AIzaSy" not in cleaned
    assert "user@example.com" not in cleaned
    assert "01012345678" not in cleaned
    assert "تم التنقيح" in cleaned


def test_sanitize_truncates_long_output():
    from scripts.sanitize_output import sanitize

    cleaned = sanitize("أ" * 5000, limit=500)
    assert len(cleaned) < 600
    assert "اقتُطع" in cleaned
