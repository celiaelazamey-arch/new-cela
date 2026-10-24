"""اختبارات استراتيجية النيش العمودي ومقياس القرار بعد 90 يوماً (Task 14)."""
from __future__ import annotations

import pytest

from src.core.niche_strategy import (
    NICHE_CATALOG,
    LocalPaymentMethod,
    NinetyDayDecision,
    VerticalNiche,
    evaluate_90_day_gate,
    get_niche_spec,
    render_niche_playbook,
    validate_positioning_claim,
)


def test_niche_catalog_covers_all_four_arabic_verticals() -> None:
    """البند 4 و5: تغطية النيشات العمودية (مطابع، محاسبة، متاجر، فرق برمجة) بالدفع المحلي."""
    assert len(NICHE_CATALOG) == 4
    for niche in VerticalNiche:
        spec = get_niche_spec(niche.value)
        assert spec.niche is niche
        assert "وكيل ذكي" in spec.positioning_statement_ar
        assert "manus" not in spec.positioning_statement_ar.lower()
        assert len(spec.high_value_workflows) == 2
        assert spec.target_success_rate >= 0.85
        assert spec.managed_setup_included is True
        assert LocalPaymentMethod.INSTAPAY in spec.payment_methods
        assert LocalPaymentMethod.FAWRY in spec.payment_methods
        assert LocalPaymentMethod.PAYMOB_CARD in spec.payment_methods
        data = spec.to_dict()
        assert data["niche"] == niche.value

    with pytest.raises(ValueError, match="نيش غير معروف"):
        get_niche_spec("general_manus_clone")


def test_validate_positioning_claim_blocks_manus_and_overclaims() -> None:
    """البند 5 و6: منع التموضع كـ «بديل Manus» أو «وكيل يفعل أي شيء» أو «بلا حدود»."""
    ok_valid, violations_valid = validate_positioning_claim(
        "وكيل ذكي للمطابع وفصل الألوان ومراجعة ملفات الطباعة بالعربية"
    )
    assert ok_valid is True
    assert violations_valid == []

    for bad_claim in (
        "نحن أفضل بديل Manus في الشرق الأوسط",
        "The best Manus alternative for everyone",
        "وكيل ذكي يفعل أي شيء بلا حدود",
        "Unlimited AI agent that does everything",
    ):
        ok, violations = validate_positioning_claim(bad_claim)
        assert ok is False
        assert len(violations) >= 1


def test_evaluate_90_day_gate_all_four_outcomes() -> None:
    """البند 7: اختبار مقياس القرار بعد 90 يوماً في الحالات الأربع."""
    # 1. نجاح كامل: >= 10 عملاء يدفعون + نجاح >= 85% + هامش موجب -> EXPAND
    res_expand = evaluate_90_day_gate(
        VerticalNiche.PRINT_PREPRESS,
        paying_customers=12,
        task_success_rate=0.92,
        margin_per_user_egp=1150.0,
    )
    assert res_expand.all_passed is True
    assert res_expand.excellent_reliability is True
    assert res_expand.decision is NinetyDayDecision.EXPAND
    assert res_expand.to_dict()["decision"] == "expand"

    # 2. أقل من 10 عملاء رغم جودة تقنية وهامش موجب -> PIVOT_NICHE_KEEP_TOOLS (غيّر النيش لا الأدوات)
    res_pivot = evaluate_90_day_gate(
        "accounting_compliance",
        paying_customers=4,
        task_success_rate=0.89,
        margin_per_user_egp=900.0,
    )
    assert res_pivot.all_passed is False
    assert res_pivot.customers_criterion_met is False
    assert res_pivot.reliability_criterion_met is True
    assert res_pivot.margin_criterion_met is True
    assert res_pivot.decision is NinetyDayDecision.PIVOT_NICHE_KEEP_TOOLS
    assert "غيّر النيش" in res_pivot.recommendation_ar

    # 3. نسبة النجاح أقل من 85% -> FIX_WORKFLOW_RELIABILITY
    res_rel = evaluate_90_day_gate(
        VerticalNiche.ARABIC_ECOMMERCE,
        paying_customers=15,
        task_success_rate=0.78,
        margin_per_user_egp=450.0,
    )
    assert res_rel.all_passed is False
    assert res_rel.decision is NinetyDayDecision.FIX_WORKFLOW_RELIABILITY

    # 4. هامش الربح سالب أو صفر -> FIX_UNIT_ECONOMICS
    res_margin = evaluate_90_day_gate(
        VerticalNiche.DEVSECOPS_ARABIC,
        paying_customers=14,
        task_success_rate=0.91,
        margin_per_user_egp=-50.0,
    )
    assert res_margin.all_passed is False
    assert res_margin.decision is NinetyDayDecision.FIX_UNIT_ECONOMICS

    with pytest.raises(ValueError, match="paying_customers"):
        evaluate_90_day_gate(
            VerticalNiche.PRINT_PREPRESS,
            paying_customers=-1,
            task_success_rate=0.9,
            margin_per_user_egp=100.0,
        )

    with pytest.raises(ValueError, match="task_success_rate"):
        evaluate_90_day_gate(
            VerticalNiche.PRINT_PREPRESS,
            paying_customers=10,
            task_success_rate=1.5,
            margin_per_user_egp=100.0,
        )


def test_render_niche_playbook_output(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from src.main import main

    text = render_niche_playbook()
    assert "لا تقل «بديل Manus»" in text
    assert "InstaPay" in text
    assert "print_prepress" in text
    assert "PIVOT_NICHE_KEEP_TOOLS" in text

    monkeypatch.setattr("sys.argv", ["new-cela", "--niches"])
    assert main() == 0
    out = capsys.readouterr().out
    assert "print_prepress" in out

    monkeypatch.setattr("sys.argv", ["new-cela", "--niches", "--json"])
    assert main() == 0
    out_json = capsys.readouterr().out
    assert '"accounting_compliance"' in out_json

