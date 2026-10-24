"""Tests for copy-ready design prompts, raster preflight and manual trend notes."""
from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from src.screen_print.design_tools import (
    build_mockup_prompt,
    build_pattern_prompt,
    inspect_print_resolution,
    summarize_manual_trends,
)


def image_bytes(size: tuple[int, int]) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, color="white").save(buffer, format="PNG")
    return buffer.getvalue()


def test_pattern_prompt_contains_seamless_design_and_personalization_constraints() -> None:
    prompt = build_pattern_prompt(
        motif="زهور استوائية",
        style="ألوان مائية",
        palette="أخضر، مرجاني، كريمي",
        target_customer="ملابس أطفال",
    )
    assert "perfectly seamless" in prompt
    assert "زهور استوائية" in prompt
    assert "ملابس أطفال" in prompt
    assert "no mockup" in prompt


def test_pattern_prompt_can_create_non_repeating_art_and_reject_missing_fields() -> None:
    prompt = build_pattern_prompt(motif="أشكال هندسية", style="فيكتور", palette="أزرق وأسود", repeat=False)
    assert "standalone fabric-print composition" in prompt
    with pytest.raises(ValueError, match="مطلوب"):
        build_pattern_prompt(motif="", style="فيكتور", palette="أزرق")


def test_mockup_brief_requires_fields_and_protects_source_artwork() -> None:
    prompt = build_mockup_prompt(
        garment="هودي",
        model_description="عارض متنوع البنية والمقاس",
        print_placement="منتصف الصدر",
        scene="استوديو بسيط بإضاءة ناعمة",
    )
    assert "Preserve the supplied artwork exactly" in prompt
    assert "do not redraw, recolor, crop, mirror" in prompt
    with pytest.raises(ValueError, match="مطلوب"):
        build_mockup_prompt(garment="", model_description="عارض", print_placement="الصدر", scene="استوديو")


def test_print_resolution_checks_physical_size_dpi_and_format() -> None:
    exact = inspect_print_resolution(
        image_bytes((3000, 3000)), physical_width_cm=25.4, physical_height_cm=25.4
    )
    assert exact.meets_target is True
    assert exact.required_width_px == 3000
    assert exact.effective_dpi_x == pytest.approx(300)
    assert exact.image.format == "PNG"

    low = inspect_print_resolution(
        image_bytes((1500, 1000)), physical_width_cm=25.4, physical_height_cm=25.4
    )
    assert low.meets_target is False
    assert low.effective_dpi_y == pytest.approx(100)


def test_print_resolution_rejects_unreasonable_dimensions_and_dpi() -> None:
    payload = image_bytes((100, 100))
    with pytest.raises(ValueError, match="أبعاد الطباعة"):
        inspect_print_resolution(payload, physical_width_cm=0, physical_height_cm=2)
    with pytest.raises(ValueError, match="DPI"):
        inspect_print_resolution(payload, physical_width_cm=2, physical_height_cm=2, target_dpi=10)


def test_manual_trend_summary_counts_only_supplied_notes() -> None:
    summary = summarize_manual_trends(["أزرق زهري", "أزرق هندسي", "BLUE floral"])
    assert summary[0] == ("أزرق", 2)
    assert ("blue", 1) in summary
    with pytest.raises(ValueError, match="top_n"):
        summarize_manual_trends([], top_n=0)
