"""اختبارات دستور التشغيل (18 قاعدة) والقاعدة الذهبية وتجربة استعادة النسخة الاحتياطية."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.core.playbook import (
    ProductStage,
    ProjectHealthAction,
    audit_18_point_playbook,
    create_verified_backup,
    evaluate_30_day_activity_gate,
    evaluate_golden_rule_stage,
    restore_and_verify_backup,
)
from src.screen_print import FabricShade, FilmArchiveStore, FilmSetRecord


def test_golden_rule_enforces_all_five_stages_in_order() -> None:
    """القاعدة الأهم: بسيط ← يعمل ← يُقاس ← يُباع ← ثم يتوسع."""
    # 1. بسيط (لم تجتز الاختبارات بعد)
    s1 = evaluate_golden_rule_stage(
        tests_and_ci_passing=False,
        has_success_and_cost_metrics=False,
        manual_paying_customers=0,
        task_success_rate=0.0,
        margin_per_user_egp=0.0,
    )
    assert s1.current_stage is ProductStage.SIMPLE
    assert s1.next_stage is ProductStage.WORKS
    assert s1.ready_for_payment_gateway is False
    assert s1.ready_to_scale is False

    # 2. يعمل (الاختبارات خضراء لكن لم يُفعَّل قياس نسبة النجاح والتكلفة)
    s2 = evaluate_golden_rule_stage(
        tests_and_ci_passing=True,
        has_success_and_cost_metrics=False,
        manual_paying_customers=0,
        task_success_rate=0.9,
        margin_per_user_egp=500.0,
    )
    assert s2.current_stage is ProductStage.WORKS
    assert s2.next_stage is ProductStage.MEASURED

    # 3. يُقاس (أقل من 3 عملاء يدفعون يدوياً -> لا تبنِ بوابة دفع بعد!)
    s3 = evaluate_golden_rule_stage(
        tests_and_ci_passing=True,
        has_success_and_cost_metrics=True,
        manual_paying_customers=2,
        task_success_rate=0.92,
        margin_per_user_egp=800.0,
    )
    assert s3.current_stage is ProductStage.MEASURED
    assert s3.next_stage is ProductStage.SOLD
    assert s3.ready_for_payment_gateway is False

    # 4. يُباع (5 عملاء يدفعون يدوياً -> جاهز لبوابة دفع، لكن ينتظر 10 عملاء للتوسع)
    s4 = evaluate_golden_rule_stage(
        tests_and_ci_passing=True,
        has_success_and_cost_metrics=True,
        manual_paying_customers=5,
        task_success_rate=0.91,
        margin_per_user_egp=950.0,
    )
    assert s4.current_stage is ProductStage.SOLD
    assert s4.next_stage is ProductStage.SCALES
    assert s4.ready_for_payment_gateway is True
    assert s4.ready_to_scale is False

    # 5. يتوسع (>= 10 عملاء + نجاح >= 85% + هامش موجب)
    s5 = evaluate_golden_rule_stage(
        tests_and_ci_passing=True,
        has_success_and_cost_metrics=True,
        manual_paying_customers=11,
        task_success_rate=0.92,
        margin_per_user_egp=1200.0,
    )
    assert s5.current_stage is ProductStage.SCALES
    assert s5.next_stage is None
    assert s5.ready_to_scale is True
    assert s5.to_dict()["current_stage"] == "scales"

    with pytest.raises(ValueError, match="manual_paying_customers"):
        evaluate_golden_rule_stage(
            tests_and_ci_passing=True,
            has_success_and_cost_metrics=True,
            manual_paying_customers=-1,
            task_success_rate=0.9,
            margin_per_user_egp=100.0,
        )


def test_backup_and_real_restore_drill_on_film_archive(tmp_path: Path) -> None:
    """البند 6 و16: backup قابل للاستعادة (جرّب الاستعادة فعلياً) على أرشيف أفلام المطبعة."""
    archive_path = tmp_path / "films.jsonl"
    store = FilmArchiveStore(archive_path, allowed_root=tmp_path)
    store.add_film_set(
        FilmSetRecord(
            film_id="FLM-100",
            design_name="تيشيرت نسر أبيض",
            client_name="مطبعة السلك سكرين",
            garment_type="تيشيرت",
            fabric_shade=FabricShade.DARK,
            colors_count=2,
            color_layers=("أرضية بيضاء", "أحمر"),
            mesh_counts_t=(55, 62),
            storage_location="درج 1 - حافظة A1",
        )
    )

    backup_dir = tmp_path / "backups"
    manifest = create_verified_backup(archive_path, backup_dir, allowed_root=tmp_path)
    assert manifest.source_name == "films.jsonl"
    assert manifest.size_bytes > 0
    assert (backup_dir / "films.jsonl.manifest.json").is_file()

    # محاكاة تلف أو فقدان الملف الأصلي ثم تجربة الاستعادة الفعلية
    archive_path.unlink()
    assert not archive_path.exists()

    restored_ok = restore_and_verify_backup(manifest, archive_path, allowed_root=tmp_path)
    assert restored_ok is True
    assert archive_path.is_file()

    restored_store = FilmArchiveStore(archive_path, allowed_root=tmp_path)
    films = restored_store.list_all()
    assert len(films) == 1
    assert films[0].film_id == "FLM-100"


def test_evaluate_30_day_activity_gate_and_4_week_review() -> None:
    """البند 18: مهمة واحدة يومياً، مراجعة كل 4 أسابيع، وتجميد بعد 30 يوماً بلا تقدم."""
    active = evaluate_30_day_activity_gate(2, current_week=1)
    assert active.should_freeze is False
    assert active.is_review_week is False
    assert active.action is ProjectHealthAction.CONTINUE_DAILY_ONE_TASK

    review = evaluate_30_day_activity_gate(5, current_week=4)
    assert review.should_freeze is False
    assert review.is_review_week is True
    assert review.action is ProjectHealthAction.RUN_4_WEEK_RETROSPECTIVE

    frozen = evaluate_30_day_activity_gate(30, current_week=8)
    assert frozen.should_freeze is True
    assert frozen.action is ProjectHealthAction.FREEZE_OR_STOP_PROJECT


def test_audit_18_point_playbook_all_verified() -> None:
    """التحقق الآلي من أن جميع البنود الـ 18 لها أدلة وملفات حقيقية في المستودع."""
    all_ok, items = audit_18_point_playbook()
    assert all_ok is True
    assert len(items) == 18
    assert all(item["verified"] is True for item in items)
