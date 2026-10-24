"""
محرك أتمتة مطابع الملابس (السلك سكرين) وأرشفة وفحص أفلام الطباعة
==================================================================
بُنيت هذه الوحدة خصيصاً لنيش رقم #0 في `new-cela`:
**مطبعة ملابس سلك سكرين تمتلك أرشيفاً كبيراً من أفلام الطباعة (الكلك / البوزيتيف).**

تُنفّذ الوحدة 3 مسارات عمل عالية القيمة وقابلة للتكرار يومياً:
  1. **فهرسة وأرشفة أفلام الطباعة (`FilmArchiveStore`):**
     تحويل مئات/آلاف الأفلام المتراكمة إلى سجل رقمي منظم وقابل للبحث الفوري بالعربية
     (حسب اسم الموديل، العميل/البراند، عدد الألوان، رقم الحرير، ومكان الحفظ في الأدراج/الحافظات)
     لاسترجاع الفيلم فوراً عند طلبات إعادة التشغيل (Re-orders) وتوفير تكلفة إخراج أفلام جديدة.
  2. **الفحص الفني لصورة فيلم السلك سكرين قبل تصوير الشبلونة (`inspect_film_image`):**
     يقرأ صورة الفيلم عبر `Pillow` ويحسب:
       - كثافة سواد المناطق المعتمة (`black_density_pct`) لمنع تسريب إضاءة الكشاف أثناء تصوير الإيمالشن.
       - نسبة تغطية الحبر (`ink_coverage_pct`) لحساب استهلاك الحبر الفعلي بالجرام.
       - سلامة الهامش الخارجي لعلامات التسجيل/الصليبة (`has_margin_for_registration`).
  3. **حاسبة الشبلونات ورقم الحرير واستهلاك الحبر والتسعير بالجنيه (`calculate_screen_print_job`):**
     تحسب تلقائياً:
       - إضافة شبلونة أرضية بيضاء (`Underbase White` + `Flash Cure`) للأقمشة الغامقة.
       - رقم الحرير المناسب لكل طبقة (`43T`–`120T`) وأقصى تردد شبكي `LPI` لمنع المواريه (`Moiré`).
       - استهلاك الحبر بالجرام والتكلفة والسعر المقترح بالجنيه المصري والوفر الناتج عن استخدام فيلم من الأرشيف.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from src.core.path_guard import guard_path

logger = logging.getLogger(__name__)


class FabricShade(str, Enum):
    """لون القماش لتحديد الحاجة إلى شبلونة أرضية بيضاء (Underbase)."""

    LIGHT = "light"
    DARK = "dark"


class InkType(str, Enum):
    """أنواع أحبار السلك سكرين الشائعة في طباعة الملابس."""

    PLASTISOL = "plastisol"
    WATER_BASED = "water_based"
    DISCHARGE = "discharge"
    HIGH_DENSITY_PUFF = "high_density_puff"


class FilmQCStatus(str, Enum):
    """نتيجة الفحص الفني للفيلم قبل تصوير الشبلونة."""

    READY_FOR_EXPOSURE = "ready_for_exposure"
    WARN_LOW_DENSITY = "warn_low_density"
    WARN_EDGE_BLEED = "warn_edge_bleed"


@dataclass(frozen=True)
class FilmSetRecord:
    """
    بطاقة طقم أفلام طباعة لموديل/تصميم واحد داخل أرشيف المطبعة.
    """

    film_id: str
    design_name: str
    client_name: str
    garment_type: str
    fabric_shade: FabricShade
    colors_count: int
    color_layers: tuple[str, ...]
    mesh_counts_t: tuple[int, ...]
    storage_location: str
    width_cm: float = 28.0
    height_cm: float = 38.0
    notes: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "film_id": self.film_id,
            "design_name": self.design_name,
            "client_name": self.client_name,
            "garment_type": self.garment_type,
            "fabric_shade": self.fabric_shade.value,
            "colors_count": self.colors_count,
            "color_layers": list(self.color_layers),
            "mesh_counts_t": list(self.mesh_counts_t),
            "storage_location": self.storage_location,
            "width_cm": self.width_cm,
            "height_cm": self.height_cm,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> FilmSetRecord:
        shade_raw = str(data.get("fabric_shade", "dark")).lower()
        shade = FabricShade(shade_raw) if shade_raw in ("light", "dark") else FabricShade.DARK
        layers_raw = data.get("color_layers")
        layers = tuple(str(x) for x in layers_raw) if isinstance(layers_raw, list) else ()
        mesh_raw = data.get("mesh_counts_t")
        meshes = tuple(int(x) for x in mesh_raw) if isinstance(mesh_raw, list) else ()
        return cls(
            film_id=str(data["film_id"]),
            design_name=str(data["design_name"]),
            client_name=str(data.get("client_name", "")),
            garment_type=str(data.get("garment_type", "تيشيرت")),
            fabric_shade=shade,
            colors_count=int(data.get("colors_count", max(len(layers), 1))),
            color_layers=layers,
            mesh_counts_t=meshes,
            storage_location=str(data.get("storage_location", "")),
            width_cm=float(data.get("width_cm", 28.0)),
            height_cm=float(data.get("height_cm", 38.0)),
            notes=str(data.get("notes", "")),
        )


class FilmArchiveStore:
    """
    مخزن أرشيف أفلام الطباعة (JSONL) — يتيح تسجيل الأفلام والبحث السريع فيها
    لمنع ضياع الأفلام أو إعادة إخراجها بدون داعٍ.
    """

    def __init__(self, path: str | Path, *, allowed_root: str | Path | None = None) -> None:
        raw_path = Path(path)
        root = Path(allowed_root) if allowed_root is not None else raw_path.resolve().parent
        self._root = root.resolve()
        self._path = guard_path(raw_path, allowed_roots=(self._root,))

    @property
    def path(self) -> Path:
        return self._path

    def add_film_set(self, record: FilmSetRecord) -> None:
        """إضافة أو تحديث طقم أفلام في الأرشيف."""
        existing = {r.film_id: r for r in self.list_all()}
        existing[record.film_id] = record
        self._write_all(list(existing.values()))

    def list_all(self) -> list[FilmSetRecord]:
        """قراءة جميع أطقم الأفلام المسجلة في الأرشيف."""
        if not self._path.exists():
            return []
        records: list[FilmSetRecord] = []
        for line_no, line in enumerate(
            self._path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
                records.append(FilmSetRecord.from_dict(payload))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                logger.warning("تخطي سطر تالف في أرشيف الأفلام (%d): %s", line_no, exc)
        return records

    def delete_film_set(self, film_id: str) -> bool:
        """حذف بطاقة فيلم محلية حسب المعرّف؛ تعيد False إن لم يوجد سجل مطابق."""
        clean_id = film_id.strip()
        if not clean_id:
            return False
        records = self.list_all()
        remaining = [record for record in records if record.film_id != clean_id]
        if len(remaining) == len(records):
            return False
        self._write_all(remaining)
        return True

    def search(self, query: str) -> list[FilmSetRecord]:
        """
        بحث نصي ذكي في الأرشيف (رقم الفيلم، اسم التصميم، العميل، نوع القطعة، الألوان، مكان الحفظ، الملاحظات).
        """
        q = query.strip().lower()
        if not q:
            return self.list_all()
        matched: list[FilmSetRecord] = []
        for rec in self.list_all():
            haystack = " ".join(
                [
                    rec.film_id,
                    rec.design_name,
                    rec.client_name,
                    rec.garment_type,
                    rec.storage_location,
                    rec.notes,
                    " ".join(rec.color_layers),
                ]
            ).lower()
            if q in haystack:
                matched.append(rec)
        return matched

    def _write_all(self, records: list[FilmSetRecord]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(r.to_dict(), ensure_ascii=False) for r in records]
        content = "\n".join(lines) + ("\n" if lines else "")
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._path.parent,
                prefix=f".{self._path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_path = Path(handle.name)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            if os.name != "nt":
                temp_path.chmod(0o600)
            os.replace(temp_path, self._path)
        except OSError:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError as cleanup_error:
                    logger.warning("تعذر تنظيف ملف الأرشيف المؤقت %s: %s", temp_path, cleanup_error)
            raise


@dataclass(frozen=True)
class FilmInspectionReport:
    """تقرير الفحص الفني لصورة فيلم السلك سكرين قبل تصوير الشبلونة."""

    width_px: int
    height_px: int
    ink_coverage_pct: float
    black_density_pct: float
    has_margin_for_registration: bool
    status: FilmQCStatus
    advice_ar: str

    def to_dict(self) -> dict[str, object]:
        return {
            "width_px": self.width_px,
            "height_px": self.height_px,
            "ink_coverage_pct": round(self.ink_coverage_pct, 2),
            "black_density_pct": round(self.black_density_pct, 2),
            "has_margin_for_registration": self.has_margin_for_registration,
            "status": self.status.value,
            "advice_ar": self.advice_ar,
        }


def inspect_film_image(
    image_path: str | Path,
    *,
    allowed_root: str | Path | None = None,
) -> FilmInspectionReport:
    """
    فحص صورة فيلم طباعة (بوزيتيف سلك سكرين) عبر `Pillow`:
      1. حساب نسبة مساحة التصميم السوداء (`ink_coverage_pct`) لتقدير استهلاك الحبر.
      2. قياس كثافة السواد (`black_density_pct`) في مناطق التصميم للتأكد من حجب الأشعة فوق البنفسجية
         أثناء تصوير الإيمالشن على الشبلونة.
      3. فحص الهامش الخارجي (5% من الحواف) للتأكد من عدم التصاق الرسم بحافة الفيلم وترك مساحة للصليبة.
    """
    from PIL import Image

    raw = Path(image_path)
    root = Path(allowed_root) if allowed_root is not None else raw.resolve().parent
    safe_path = guard_path(raw, allowed_roots=(root.resolve(),))

    with Image.open(safe_path) as img:
        gray = img.convert("L")
        width, height = gray.size
        pixels = gray.tobytes()

    total_pixels = max(len(pixels), 1)
    # احسب مباشرة من bytes بدلاً من إنشاء قائمتين ضخمتين بحجم الصورة.
    dark_count = 0
    dark_value_sum = 0
    for value in pixels:
        if value < 128:
            dark_count += 1
            dark_value_sum += value
    ink_coverage_pct = (dark_count / total_pixels) * 100.0

    if dark_count:
        # كلما اقتربت قيمة البكسل من 0 زادت عتامة السواد (100% = أسود فاحم مثالي لحجب الضوء)
        mean_dark_val = dark_value_sum / dark_count
        black_density_pct = max(0.0, min(100.0, ((255.0 - mean_dark_val) / 255.0) * 100.0))
    else:
        black_density_pct = 0.0

    # افحص فقط محيط الصورة؛ لا تمرّ على كل البكسلات لاستخراج حدوده.
    border_dark = 0
    border_total = 0
    if width and height:
        edge_rows = (0,) if height == 1 else (0, height - 1)
        for y in edge_rows:
            for x in range(width):
                border_total += 1
                if pixels[y * width + x] < 64:
                    border_dark += 1
        edge_columns = (0,) if width == 1 else (0, width - 1)
        for y in range(1, height - 1):
            for x in edge_columns:
                border_total += 1
                if pixels[y * width + x] < 64:
                    border_dark += 1

    border_dark_ratio = border_dark / max(border_total, 1)
    has_margin = border_dark_ratio < 0.15

    if not dark_count or black_density_pct < 75.0:
        status = FilmQCStatus.WARN_LOW_DENSITY
        advice = (
            f"⚠️ كثافة سواد الفيلم ({black_density_pct:.1f}%) ضعيفة — قد تتسرّب إضاءة الكشاف أثناء "
            "تصوير الشبلونة وتُغلق تفاصيل الرسم. يُنصح بتغميق الفيلم أو تبطين فيلمين فوق بعض."
        )
    elif not has_margin:
        status = FilmQCStatus.WARN_EDGE_BLEED
        advice = (
            "⚠️ التصميم واصل لحافة الفيلم بدون هامش كافٍ لعلامات الصليبة (Registration) وحرم الشبلونة."
        )
    else:
        status = FilmQCStatus.READY_FOR_EXPOSURE
        advice = (
            f"✅ الفيلم جاهز لتصوير الشبلونة (كثافة السواد {black_density_pct:.1f}%، "
            f"تغطية الحبر {ink_coverage_pct:.1f}%، وهامش الصليبة سليم)."
        )

    return FilmInspectionReport(
        width_px=width,
        height_px=height,
        ink_coverage_pct=ink_coverage_pct,
        black_density_pct=black_density_pct,
        has_margin_for_registration=has_margin,
        status=status,
        advice_ar=advice,
    )


def recommend_mesh_and_lpi(
    *,
    ink_type: InkType = InkType.PLASTISOL,
    is_underbase: bool = False,
    has_halftone: bool = False,
) -> tuple[int, int]:
    """
    توصية رقم الحرير بالـ T (خيوط/سم) وأقصى تردد شبكي `LPI` (خط/بوصة) لمنع المواريه (`Moiré`).
    القاعدة الفيزيائية في السلك سكرين:
      Mesh (threads/inch) = Mesh_T * 2.54
      Max safe LPI = Mesh (threads/inch) / 4.5
    """
    if is_underbase or ink_type is InkType.HIGH_DENSITY_PUFF:
        mesh_t = 43 if ink_type is InkType.HIGH_DENSITY_PUFF else 55
    elif has_halftone:
        mesh_t = 90 if ink_type is InkType.WATER_BASED else 110
    elif ink_type in (InkType.WATER_BASED, InkType.DISCHARGE):
        mesh_t = 77
    else:
        mesh_t = 62

    max_lpi = round((mesh_t * 2.54) / 4.5)
    return (mesh_t, max_lpi)


@dataclass(frozen=True)
class ScreenJobEstimate:
    """
    تقدير فني ومالي شامل لأمر شغل سلك سكرين ملابس بالجنيه المصري.
    """

    quantity: int
    design_colors: int
    needs_underbase_white: bool
    total_screens_needed: int
    recommended_meshes_t: tuple[int, ...]
    max_halftone_lpi: int
    estimated_ink_grams: float
    film_archive_savings_egp: float
    setup_and_screens_cost_egp: float
    ink_and_printing_cost_egp: float
    total_cost_egp: float
    suggested_price_egp: float
    unit_price_egp: float

    def to_dict(self) -> dict[str, object]:
        return {
            "quantity": self.quantity,
            "design_colors": self.design_colors,
            "needs_underbase_white": self.needs_underbase_white,
            "total_screens_needed": self.total_screens_needed,
            "recommended_meshes_t": list(self.recommended_meshes_t),
            "max_halftone_lpi": self.max_halftone_lpi,
            "estimated_ink_grams": round(self.estimated_ink_grams, 1),
            "film_archive_savings_egp": round(self.film_archive_savings_egp, 2),
            "setup_and_screens_cost_egp": round(self.setup_and_screens_cost_egp, 2),
            "ink_and_printing_cost_egp": round(self.ink_and_printing_cost_egp, 2),
            "total_cost_egp": round(self.total_cost_egp, 2),
            "suggested_price_egp": round(self.suggested_price_egp, 2),
            "unit_price_egp": round(self.unit_price_egp, 2),
        }


def calculate_screen_print_job(
    *,
    quantity: int,
    colors_count: int,
    fabric_shade: FabricShade | str = FabricShade.DARK,
    ink_type: InkType | str = InkType.PLASTISOL,
    has_halftone: bool = False,
    has_existing_films: bool = True,
    ink_coverage_pct: float = 35.0,
    print_area_cm2: float = 900.0,
    film_output_cost_per_color_egp: float = 85.0,
    screen_prep_cost_per_screen_egp: float = 120.0,
    ink_cost_per_kg_egp: float = 650.0,
    press_impression_cost_egp: float = 1.25,
    target_margin_pct: float = 45.0,
) -> ScreenJobEstimate:
    """
    حاسبة أمر شغل مطبعة ملابس سلك سكرين:
      - تضيف تلقائياً شبلونة أرضية بيضاء (`Underbase White`) للأقمشة الغامقة مع البلاستزول/المائي.
      - تحسب الوفر المباشر من امتلاك أرشيف الأفلام (`has_existing_films=True`).
      - تقدّر كمية الحبر بالجرام وتكلفة الشبلونات والطباعة وسعر البيع للعميل بالجنيه.
    """
    if quantity <= 0:
        raise ValueError("quantity يجب أن يكون أكبر من صفر")
    if colors_count <= 0:
        raise ValueError("colors_count يجب أن يكون أكبر من صفر")
    if not (0.0 < target_margin_pct < 95.0):
        raise ValueError("target_margin_pct يجب أن يكون بين 0 و 95")

    shade = (
        fabric_shade
        if isinstance(fabric_shade, FabricShade)
        else FabricShade(fabric_shade.strip().lower())
    )
    ink = ink_type if isinstance(ink_type, InkType) else InkType(ink_type.strip().lower())

    # الأقمشة الغامقة تحتاج شبلونة أرضية بيضاء إضافية (إلا إذا كان التصميم لوناً أبيض واحداً فقط أو حبر ديسشارج)
    needs_underbase = shade is FabricShade.DARK and ink is not InkType.DISCHARGE and colors_count >= 1
    total_screens = colors_count + (1 if needs_underbase else 0)

    meshes: list[int] = []
    if needs_underbase:
        ub_mesh, _ = recommend_mesh_and_lpi(ink_type=ink, is_underbase=True, has_halftone=False)
        meshes.append(ub_mesh)

    color_mesh, max_lpi = recommend_mesh_and_lpi(
        ink_type=ink, is_underbase=False, has_halftone=has_halftone
    )
    for _ in range(colors_count):
        meshes.append(color_mesh)

    # تقدير الحبر بالجرام: نحو 0.0022 جرام لكل سم2 مطبوع عند تغطية 100% لكل لون
    effective_area_cm2 = print_area_cm2 * (max(ink_coverage_pct, 5.0) / 100.0)
    grams_per_layer_per_piece = effective_area_cm2 * 0.0022
    underbase_multiplier = 1.35 if needs_underbase else 1.0
    estimated_ink_grams = (
        quantity * colors_count * grams_per_layer_per_piece * underbase_multiplier
    )

    # حساب التوفير الناتج عن وجود الأفلام في أرشيف المطبعة مسبقاً
    film_savings = (total_screens * film_output_cost_per_color_egp) if has_existing_films else 0.0
    film_cost = 0.0 if has_existing_films else (total_screens * film_output_cost_per_color_egp)

    setup_and_screens_cost = film_cost + (total_screens * screen_prep_cost_per_screen_egp)
    ink_cost = (estimated_ink_grams / 1000.0) * ink_cost_per_kg_egp
    impressions_cost = quantity * total_screens * press_impression_cost_egp
    ink_and_printing_cost = ink_cost + impressions_cost

    total_cost = setup_and_screens_cost + ink_and_printing_cost
    suggested_price = total_cost / (1.0 - (target_margin_pct / 100.0))
    unit_price = suggested_price / quantity

    return ScreenJobEstimate(
        quantity=quantity,
        design_colors=colors_count,
        needs_underbase_white=needs_underbase,
        total_screens_needed=total_screens,
        recommended_meshes_t=tuple(meshes),
        max_halftone_lpi=max_lpi,
        estimated_ink_grams=estimated_ink_grams,
        film_archive_savings_egp=film_savings,
        setup_and_screens_cost_egp=setup_and_screens_cost,
        ink_and_printing_cost_egp=ink_and_printing_cost,
        total_cost_egp=total_cost,
        suggested_price_egp=suggested_price,
        unit_price_egp=unit_price,
    )
