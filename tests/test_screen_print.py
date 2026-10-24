"""اختبارات محرك أتمتة مطابع الملابس (السلك سكرين) وفحص وأرشفة أفلام الطباعة."""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from src.screen_print import (
    FabricShade,
    FilmArchiveStore,
    FilmQCStatus,
    FilmSetRecord,
    InkType,
    calculate_screen_print_job,
    inspect_film_image,
    recommend_mesh_and_lpi,
)


def test_film_archive_store_add_list_and_arabic_search(tmp_path: Path) -> None:
    """اختبار فهرسة واسترجاع أفلام السلك سكرين بالعربية لتوفير إعادة إخراج الأفلام."""
    archive_file = tmp_path / "films_archive.jsonl"
    store = FilmArchiveStore(archive_file, allowed_root=tmp_path)
    assert store.path == archive_file
    assert store.list_all() == []

    rec1 = FilmSetRecord(
        film_id="FLM-2026-001",
        design_name="هودي شتوي نسر ذهبي",
        client_name="براند كايرو ستريت",
        garment_type="هودي قطن ميلتون",
        fabric_shade=FabricShade.DARK,
        colors_count=4,
        color_layers=("أرضية بيضاء - Underbase", "ذهبي", "أحمر", "أسود تفصيلي"),
        mesh_counts_t=(55, 62, 62, 90),
        storage_location="درج 2 - حافظة B04",
        width_cm=30.0,
        height_cm=40.0,
        notes="يحتاج فلاش تجفيف بعد الأرضية البيضاء",
    )
    rec2 = FilmSetRecord(
        film_id="FLM-2026-002",
        design_name="تيشيرت صيفي موج البحر",
        client_name="براند الساحل",
        garment_type="تيشيرت سنجل ليكرا",
        fabric_shade=FabricShade.LIGHT,
        colors_count=2,
        color_layers=("أزرق مائي", "كحلي"),
        mesh_counts_t=(77, 77),
        storage_location="درج 5 - حافظة A11",
    )

    store.add_film_set(rec1)
    store.add_film_set(rec2)

    all_films = store.list_all()
    assert len(all_films) == 2

    # البحث باسم الموديل أو البراند أو مكان الحفظ
    assert len(store.search("نسر ذهبي")) == 1
    assert store.search("نسر ذهبي")[0].film_id == "FLM-2026-001"
    assert len(store.search("درج 5")) == 1
    assert store.search("درج 5")[0].film_id == "FLM-2026-002"
    assert len(store.search("أرضية بيضاء")) == 1
    assert len(store.search("")) == 2


def test_inspect_film_image_ready_low_density_and_edge_bleed(tmp_path: Path) -> None:
    """اختبار الفحص الفني لصورة فيلم السلك سكرين (كثافة السواد، التغطية، وهامش الصليبة)."""
    # 1. فيلم سليم: خلفية بيضاء/شفافة (255) ومستطيل أسود فاحم (0) في المنتصف مع هامش سليم
    good_img = Image.new("L", (100, 100), color=255)
    for y in range(25, 75):
        for x in range(25, 75):
            good_img.putpixel((x, y), 5)
    good_path = tmp_path / "film_good.png"
    good_img.save(good_path)

    rep_good = inspect_film_image(good_path, allowed_root=tmp_path)
    assert rep_good.status is FilmQCStatus.READY_FOR_EXPOSURE
    assert rep_good.has_margin_for_registration is True
    assert rep_good.black_density_pct > 95.0
    assert 20.0 <= rep_good.ink_coverage_pct <= 30.0
    assert rep_good.to_dict()["status"] == "ready_for_exposure"

    # 2. فيلم باهت (رمادي 110 بدلاً من أسود فاحم) -> يحذر من تسريب إضاءة الكشاف
    faint_img = Image.new("L", (100, 100), color=255)
    for y in range(30, 70):
        for x in range(30, 70):
            faint_img.putpixel((x, y), 115)
    faint_path = tmp_path / "film_faint.png"
    faint_img.save(faint_path)

    rep_faint = inspect_film_image(faint_path, allowed_root=tmp_path)
    assert rep_faint.status is FilmQCStatus.WARN_LOW_DENSITY

    # 3. فيلم واصل لحافة الصورة بلا هامش للصليبة
    bleed_img = Image.new("L", (100, 100), color=0)
    bleed_path = tmp_path / "film_bleed.png"
    bleed_img.save(bleed_path)

    rep_bleed = inspect_film_image(bleed_path, allowed_root=tmp_path)
    assert rep_bleed.status is FilmQCStatus.WARN_EDGE_BLEED
    assert rep_bleed.has_margin_for_registration is False


def test_recommend_mesh_and_calculate_screen_print_job() -> None:
    """اختبار حساب عدد الشبلونات (مع الأرضية البيضاء للغامق) والحرير والوفر من أرشيف الأفلام."""
    mesh_ub, _ = recommend_mesh_and_lpi(ink_type=InkType.PLASTISOL, is_underbase=True)
    assert mesh_ub == 55

    mesh_ht, lpi_ht = recommend_mesh_and_lpi(ink_type=InkType.PLASTISOL, has_halftone=True)
    assert mesh_ht == 110
    assert lpi_ht >= 60

    # أمر شغل 300 تيشيرت أسود (غامق) بـ 3 ألوان بلاستزول مع وجود الأفلام في الأرشيف
    est_with_archive = calculate_screen_print_job(
        quantity=300,
        colors_count=3,
        fabric_shade=FabricShade.DARK,
        ink_type=InkType.PLASTISOL,
        has_existing_films=True,
    )
    # 3 ألوان + 1 شبلونة أرضية بيضاء = 4 شبلونات
    assert est_with_archive.needs_underbase_white is True
    assert est_with_archive.total_screens_needed == 4
    assert est_with_archive.recommended_meshes_t == (55, 62, 62, 62)
    assert est_with_archive.film_archive_savings_egp == 4 * 85.0

    # مقارنة بنفس الأمر لو لم تكن الأفلام في الأرشيف
    est_new_films = calculate_screen_print_job(
        quantity=300,
        colors_count=3,
        fabric_shade="dark",
        ink_type="plastisol",
        has_existing_films=False,
    )
    assert est_new_films.film_archive_savings_egp == 0.0
    assert est_new_films.total_cost_egp == pytest.approx(
        est_with_archive.total_cost_egp + (4 * 85.0)
    )
    assert est_with_archive.to_dict()["total_screens_needed"] == 4

    with pytest.raises(ValueError, match="quantity"):
        calculate_screen_print_job(quantity=0, colors_count=2)
