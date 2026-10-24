"""اختبارات خطة الربح والتسعير وحماية الهامش (src/core/pricing.py)."""
from __future__ import annotations

import json

import pytest

from src.core.pricing import (
    PLAN_CATALOG,
    BillingCycle,
    PlanTier,
    TaskComplexity,
    build_plan_guard,
    calculate_unit_economics,
    classify_task_complexity,
    evaluate_usage_alert,
    get_plan,
    next_upgrade_tier,
    render_pricing_table,
)
from src.core.quota import ProviderNotAllowed, TrustLevel
from src.main import build_parser, build_quota, main


def test_all_plans_have_explicit_caps_no_unlimited_variable_cost() -> None:
    """البند 4 و13: لا توجد خطة «غير محدودة» على تكلفة الـ AI المتغيرة."""
    assert len(PLAN_CATALOG) == 5
    for tier, spec in PLAN_CATALOG.items():
        assert spec.tier is tier
        assert spec.monthly_tasks_quota > 0
        assert spec.max_model_calls_per_task > 0
        assert spec.max_tool_calls_per_task > 0
        assert spec.included_seats >= 1
        assert len(spec.highlights) >= 2


def test_free_vs_paid_watermark_and_pricing() -> None:
    """البند 3: Free بعلامة مائية وسعر صفر؛ الخطط المدفوعة بلا علامة ترويجية."""
    free = get_plan("free")
    pro = get_plan(PlanTier.PRO)
    team = get_plan("TEAM")

    assert free.monthly_price_egp == 0
    assert free.monthly_price_usd == 0
    assert free.watermark_reports is True

    assert pro.monthly_price_egp == 490
    assert pro.monthly_price_usd == 12
    assert pro.watermark_reports is False
    assert 15 <= pro.annual_discount_pct <= 20

    assert team.monthly_price_egp == 1990
    assert team.monthly_price_usd == 49
    assert team.included_seats == 5
    assert team.annual_discount_pct == 20


def test_annual_discount_and_per_seat_billing() -> None:
    """البند 4: خصم سنوي 15-20% + تسعير للمقعد الإضافي في خطط الفرق."""
    team = get_plan(PlanTier.TEAM)

    # السعر الأساسي لـ 5 مقاعد
    assert team.effective_monthly_egp(BillingCycle.MONTHLY, seats=5) == 1990
    assert team.effective_monthly_usd(BillingCycle.MONTHLY, seats=5) == 49

    # إضافة مقعدين إضافيين (إجمالي 7 مقاعد)
    assert team.effective_monthly_egp(BillingCycle.MONTHLY, seats=7) == 1990 + (2 * 350)
    assert team.effective_monthly_usd(BillingCycle.MONTHLY, seats=7) == 49 + (2 * 9)

    # الاشتراك السنوي يمنح خصم 20%
    annual_monthly_egp = team.effective_monthly_egp(BillingCycle.ANNUAL, seats=5)
    assert annual_monthly_egp == round(1990 * 0.80)
    assert team.annual_total_egp(seats=5) == annual_monthly_egp * 12
    assert team.annual_total_usd(seats=5) == round(49 * 0.80) * 12


def test_build_plan_guard_preserves_zero_cost_red_line() -> None:
    """الخط الأحمر (CLAUDE.md §5): allow_paid يبقى False افتراضياً حتى في الخطط الأعلى."""
    for tier in PlanTier:
        guard = build_plan_guard(tier)
        spec = get_plan(tier)
        assert guard.max_model_calls == spec.max_model_calls_per_task
        assert guard.max_tool_calls == spec.max_tool_calls_per_task
        assert guard.allow_paid is False
        assert guard.allow_unverified is False
        with pytest.raises(ProviderNotAllowed):
            guard.check_provider("paid-llm", TrustLevel.PAID)


def test_usage_alert_at_ok_80_percent_and_paywall() -> None:
    """البند 7: تنبيه عند الوصول لـ 80% من الحد + Paywall عند 100% مع اقتراح الخطة الأعلى."""
    guard = build_plan_guard(PlanTier.FREE)  # 40 نموذج / 20 أداة

    # 1) استهلاك 50% (20/40) ← سليم بلا تنبيه
    for _ in range(20):
        guard.consume_model("gemini", TrustLevel.VERIFIED_FREE)
    alert_ok = evaluate_usage_alert(guard, PlanTier.FREE)
    assert alert_ok.status == "ok"
    assert alert_ok.near_limit is False
    assert alert_ok.paywall_triggered is False
    assert alert_ok.messages == ()

    # 2) استهلاك 80% بالضبط (32/40) ← تنبيه 80% مع التوصية بخطة Pro
    for _ in range(12):
        guard.consume_model("gemini", TrustLevel.VERIFIED_FREE)
    alert_80 = evaluate_usage_alert(guard, PlanTier.FREE)
    assert alert_80.status == "warning_80"
    assert alert_80.near_limit is True
    assert alert_80.paywall_triggered is False
    assert alert_80.recommended_tier is PlanTier.PRO
    assert any("80%" in msg for msg in alert_80.messages)
    assert any("المحترف (Pro)" in msg for msg in alert_80.messages)

    # 3) استهلاك 100% (40/40) ← تفعيل Paywall وتجربة 14 يوماً
    for _ in range(8):
        guard.consume_model("gemini", TrustLevel.VERIFIED_FREE)
    alert_paywall = evaluate_usage_alert(guard, PlanTier.FREE)
    assert alert_paywall.status == "paywall"
    assert alert_paywall.near_limit is True
    assert alert_paywall.paywall_triggered is True
    assert alert_paywall.recommended_tier is PlanTier.PRO
    assert any("100%" in msg for msg in alert_paywall.messages)
    assert any("تجربة 14 يوماً" in msg for msg in alert_paywall.messages)
    assert alert_paywall.to_dict()["paywall_triggered"] is True


def test_usage_alert_on_enterprise_has_no_higher_tier() -> None:
    """خطة Enterprise هي الأعلى فلا تقترح خطة أعلى منها."""
    assert next_upgrade_tier(PlanTier.ENTERPRISE) is None
    guard = build_plan_guard(PlanTier.ENTERPRISE)
    guard.usage.tool_calls = guard.max_tool_calls
    alert = evaluate_usage_alert(guard, PlanTier.ENTERPRISE)
    assert alert.paywall_triggered is True
    assert alert.recommended_tier is None


def test_usage_alert_rejects_invalid_threshold() -> None:
    guard = build_plan_guard(PlanTier.FREE)
    with pytest.raises(ValueError):
        evaluate_usage_alert(guard, PlanTier.FREE, warn_threshold=0.0)
    with pytest.raises(ValueError):
        evaluate_usage_alert(guard, PlanTier.FREE, warn_threshold=1.5)


def test_classify_task_complexity_routes_appropriately() -> None:
    """البند 5: نموذج رخيص للمهام البسيطة وقوي للمهام المعقدة."""
    assert classify_task_complexity("ما هو تاريخ اليوم؟") is TaskComplexity.SIMPLE
    assert (
        classify_task_complexity("لخّص هذا الملف", require_structured=True)
        is TaskComplexity.MODERATE
    )
    assert (
        classify_task_complexity("أصلح ثغرة CodeQL في معمارية المشروع مع خطة امتثال")
        is TaskComplexity.COMPLEX
    )
    assert classify_task_complexity("مهمة قصيرة", tool_count=4) is TaskComplexity.COMPLEX


def test_unit_economics_protects_gross_margin_and_ltv_cac() -> None:
    """البندان 5 و11: هامش ربح > 80% و LTV > 3x CAC في الخطط القياسية."""
    pro_econ = calculate_unit_economics(PlanTier.PRO, avg_tasks_used=120, cac_usd=25.0)
    assert pro_econ.arpu_usd == 12.0
    assert pro_econ.gross_margin_pct >= 80.0
    assert pro_econ.margin_healthy is True
    assert pro_econ.ltv_to_cac >= 3.0
    assert pro_econ.ltv_cac_healthy is True

    team_econ = calculate_unit_economics(
        PlanTier.TEAM,
        avg_tasks_used=600,
        avg_model_calls_per_task=18,
        support_cost_usd=2.50,
        cac_usd=80.0,
    )
    assert team_econ.arpu_usd == 49.0
    assert team_econ.gross_margin_pct >= 80.0
    assert team_econ.margin_healthy is True
    assert team_econ.ltv_cac_healthy is True

    # لو ارتفعت تكلفة النموذج بشكل مفرط يهبط الهامش دون 70% فيُكتشف الخطر
    unhealthy = calculate_unit_economics(
        PlanTier.PRO,
        avg_tasks_used=300,
        avg_model_calls_per_task=120,
        cost_per_1k_model_calls_usd=0.50,
    )
    assert unhealthy.margin_healthy is False
    assert unhealthy.to_dict()["margin_healthy"] is False


def test_render_pricing_table_and_cli_plans_flag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """البند 7: مقارنة واضحة بين الخطط في صفحة واحدة عبر الدالة وعبر CLI (--plans)."""
    table = render_pricing_table()
    assert "المجانية (Free)" in table
    assert "المحترف (Pro)" in table
    assert "الفرق والشركات الصغيرة (Team)" in table
    assert "490 ج.م" in table
    assert "1,990 ج.م" in table

    # CLI: --plans نصي
    monkeypatch.setattr("sys.argv", ["new-cela", "--plans"])
    assert main() == 0
    out_text = capsys.readouterr().out
    assert "المحترف (Pro)" in out_text

    # CLI: --plans --json
    monkeypatch.setattr("sys.argv", ["new-cela", "--plans", "--json"])
    assert main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["pro"]["monthly_price_egp"] == 490
    assert payload["team"]["included_seats"] == 5

    # CLI: --plan pro يضبط سقف QuotaGuard تلقائياً على 120 نموذج / 60 أداة
    args = build_parser().parse_args(["--task", "اختبار", "--plan", "pro"])
    guard = build_quota(args)
    assert guard.max_model_calls == 120
    assert guard.max_tool_calls == 60
    assert guard.allow_paid is False
