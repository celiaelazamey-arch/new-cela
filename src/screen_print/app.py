"""واجهة محلية تجريبية لأرشيف وفحص وحساب أعمال السلك سكرين.

تشغيل: streamlit run src/screen_print/app.py
هذه الواجهة محلية أحادية المستخدم؛ ليست واجهة SaaS ولا تحتوي على تسجيل دخول.
"""
from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

from src.screen_print import (
    FabricShade,
    FilmArchiveStore,
    FilmQCStatus,
    FilmSetRecord,
    InkType,
    calculate_screen_print_job,
)
from src.screen_print.uploads import inspect_uploaded_image

DATA_DIR = Path(
    os.environ.get("SCREEN_PRINT_DATA_DIR", "~/.new-cela/screen_print")
).expanduser()
ARCHIVE_PATH = DATA_DIR / "film_archive.jsonl"


def get_store() -> FilmArchiveStore:
    return FilmArchiveStore(ARCHIVE_PATH, allowed_root=DATA_DIR)


def render_archive() -> None:
    st.subheader("أرشيف أطقم الأفلام")
    st.caption(f"يحفظ السجل الوصفي محلياً في `{ARCHIVE_PATH}`. لا تُحفظ صور الرفع في الأرشيف.")

    query = st.text_input("ابحث برقم الفيلم أو التصميم أو العميل أو مكان الحفظ", key="film_search")
    records = get_store().search(query)
    if records:
        rows = [
            {
                "رقم الفيلم": r.film_id,
                "التصميم": r.design_name,
                "العميل": r.client_name,
                "القطعة": r.garment_type,
                "الألوان": r.colors_count,
                "المقاس (سم)": f"{r.width_cm:g} × {r.height_cm:g}",
                "مكان الحفظ": r.storage_location,
                "ملاحظات": r.notes,
            }
            for r in records
        ]
        st.dataframe(rows, hide_index=True, width="stretch")
    else:
        st.info("لا توجد سجلات مطابقة بعد.")

    if records:
        with st.form("delete_film_record_form"):
            film_ids = [record.film_id for record in records]
            selected_id = st.selectbox("حذف بطاقة فيلم", film_ids)
            confirm_delete = st.checkbox("أؤكد حذف بطاقة الفيلم المحددة من هذا الجهاز")
            delete_submitted = st.form_submit_button("حذف البطاقة")
        if delete_submitted:
            if not confirm_delete:
                st.error("علّم مربع التأكيد قبل الحذف.")
                return
            try:
                deleted = get_store().delete_film_set(selected_id)
            except OSError as exc:
                st.error(f"تعذر حذف البطاقة محلياً: {exc}")
                return
            if deleted:
                st.session_state["screen_print_flash"] = "حُذفت بطاقة الفيلم من الأرشيف المحلي."
                st.rerun()
            st.warning("لم يُعثر على البطاقة؛ ربما حُذفت في نافذة أخرى.")

    with st.expander("إضافة أو تحديث بطاقة فيلم"):
        with st.form("film_record_form", clear_on_submit=False):
            film_id = st.text_input("رقم الفيلم *", max_chars=80)
            design_name = st.text_input("اسم التصميم *", max_chars=160)
            client_name = st.text_input("العميل / البراند", max_chars=160)
            garment_type = st.text_input("نوع القطعة", value="تيشيرت", max_chars=100)
            fabric_shade_label = st.selectbox("درجة لون القماش", ["غامق", "فاتح"])
            colors_count = st.number_input("عدد ألوان التصميم", min_value=1, max_value=30, value=1)
            color_layers_text = st.text_input("طبقات الألوان (افصل بينها بفاصلة)")
            mesh_text = st.text_input("أرقام الحرير T (اختياري، افصل بينها بفاصلة)")
            width_cm = st.number_input("العرض (سم)", min_value=0.1, max_value=200.0, value=28.0)
            height_cm = st.number_input("الارتفاع (سم)", min_value=0.1, max_value=200.0, value=38.0)
            storage_location = st.text_input("مكان الفيلم في الأرشيف", max_chars=160)
            notes = st.text_area("ملاحظات", max_chars=1000)
            submitted = st.form_submit_button("حفظ البطاقة")

        if submitted:
            clean_id = film_id.strip()
            clean_name = design_name.strip()
            if not clean_id or not clean_name:
                st.error("رقم الفيلم واسم التصميم مطلوبان.")
                return
            try:
                layers = tuple(part.strip() for part in color_layers_text.split(",") if part.strip())
                meshes = tuple(int(part.strip()) for part in mesh_text.split(",") if part.strip())
                shade = FabricShade.DARK if fabric_shade_label == "غامق" else FabricShade.LIGHT
                record = FilmSetRecord(
                    film_id=clean_id,
                    design_name=clean_name,
                    client_name=client_name.strip(),
                    garment_type=garment_type.strip() or "غير محدد",
                    fabric_shade=shade,
                    colors_count=int(colors_count),
                    color_layers=layers,
                    mesh_counts_t=meshes,
                    storage_location=storage_location.strip(),
                    width_cm=float(width_cm),
                    height_cm=float(height_cm),
                    notes=notes.strip(),
                )
                get_store().add_film_set(record)
            except ValueError:
                st.error("أرقام الحرير يجب أن تكون أعداداً صحيحة مفصولة بفواصل.")
                return
            except OSError as exc:
                st.error(f"تعذر حفظ السجل محلياً: {exc}")
                return
            else:
                st.session_state["screen_print_flash"] = "حُفظت البطاقة محلياً. لا يعني ذلك حفظ صورة الفيلم."
                st.rerun()


def render_inspection() -> None:
    st.subheader("فحص أولي لصورة الفيلم")
    st.warning(
        "ارفع صورة اختبارية فقط في هذه النسخة المحلية. الصورة تُفحص مؤقتاً ولا تُضاف إلى الأرشيف؛ "
        "النتيجة مؤشر تقني محدود وليست اعتماداً لجاهزية الطباعة أو تصوير الشبلونة."
    )
    uploaded = st.file_uploader(
        "صورة الفيلم (PNG / JPEG / TIFF، حتى 25 MiB)",
        type=["png", "jpg", "jpeg", "tif", "tiff"],
        key="film_image_upload",
    )
    if uploaded is None:
        return

    if st.button("افحص الصورة", type="primary"):
        try:
            report = inspect_uploaded_image(uploaded.getvalue())
        except ValueError as exc:
            st.error(str(exc))
            return
        except OSError:
            st.error("تعذر قراءة الصورة. جرّب ملف PNG أو JPEG أو TIFF سليماً.")
            return

        status_labels = {
            FilmQCStatus.READY_FOR_EXPOSURE: "لم تُرصد مشكلة ضمن القياسات المحدودة فقط",
            FilmQCStatus.WARN_LOW_DENSITY: "تحذير: كثافة السواد منخفضة حسب العتبة البرمجية",
            FilmQCStatus.WARN_EDGE_BLEED: "تحذير: قد يلامس الرسم حافة الصورة",
        }
        st.markdown(f"**النتيجة الأولية:** {status_labels[report.status]}")
        col1, col2, col3 = st.columns(3)
        col1.metric("الأبعاد بالبكسل", f"{report.width_px} × {report.height_px}")
        col2.metric("كثافة السواد المقدّرة", f"{report.black_density_pct:.1f}%")
        col3.metric("تغطية البكسلات الداكنة", f"{report.ink_coverage_pct:.1f}%")
        st.write("هامش للصليبة حسب فحص الإطار:", "نعم" if report.has_margin_for_registration else "لا")
        st.caption("راجع الفيلم بصرياً وفنياً قبل الاستخدام؛ هذه الحسابات لا تفحص دقة الطباعة أو فصل الألوان أو صلاحية الفيلم الفعلية.")


def render_estimator() -> None:
    st.subheader("تقدير أمر شغل")
    st.caption("أرقام التكلفة افتراضات قابلة للتعديل وليست عرض سعر أو ضماناً للهامش.")
    with st.form("job_estimate_form"):
        quantity = st.number_input("الكمية", min_value=1, max_value=1_000_000, value=100, step=10)
        colors = st.number_input("عدد ألوان التصميم", min_value=1, max_value=30, value=2)
        shade_label = st.selectbox("لون القماش", ["غامق", "فاتح"], key="estimate_shade")
        ink_label = st.selectbox(
            "نوع الحبر",
            ["بلاستيزول", "مائي", "ديسشارج", "بف عالي الكثافة"],
        )
        ink_types = {
            "بلاستيزول": InkType.PLASTISOL,
            "مائي": InkType.WATER_BASED,
            "ديسشارج": InkType.DISCHARGE,
            "بف عالي الكثافة": InkType.HIGH_DENSITY_PUFF,
        }
        has_halftone = st.checkbox("التصميم يحتوي على هافتون")
        has_existing_films = st.checkbox("الأفلام موجودة في الأرشيف", value=True)
        ink_coverage = st.slider("تغطية الحبر المقدّرة (%)", min_value=5, max_value=100, value=35)
        print_area = st.number_input("مساحة الطباعة (سم²)", min_value=1.0, max_value=20_000.0, value=900.0)
        submitted = st.form_submit_button("احسب التقدير", type="primary")

    if not submitted:
        return
    try:
        estimate = calculate_screen_print_job(
            quantity=int(quantity),
            colors_count=int(colors),
            fabric_shade=FabricShade.DARK if shade_label == "غامق" else FabricShade.LIGHT,
            ink_type=ink_types[ink_label],
            has_halftone=has_halftone,
            has_existing_films=has_existing_films,
            ink_coverage_pct=float(ink_coverage),
            print_area_cm2=float(print_area),
        )
    except ValueError as exc:
        st.error(f"تعذر الحساب: {exc}")
        return

    first, second, third = st.columns(3)
    first.metric("عدد الشبلونات المقدر", estimate.total_screens_needed)
    second.metric("التكلفة المقدرة", f"{estimate.total_cost_egp:,.2f} ج.م")
    third.metric("سعر بيع استرشادي للوحدة", f"{estimate.unit_price_egp:,.2f} ج.م")
    st.json(estimate.to_dict())
    st.caption("تحقق من أسعار الخامات والأجور والهدر والضرائب محلياً قبل اعتماد أي سعر.")


def main() -> None:
    st.set_page_config(page_title="مساعد مطبعة السلك سكرين — محلي", page_icon="🖨️", layout="wide")
    st.markdown(
        "<style>.stApp{direction:rtl}.stMarkdown, label, h1, h2, h3, p{text-align:right}</style>",
        unsafe_allow_html=True,
    )
    st.title("مساعد مطبعة السلك سكرين")
    st.caption("نسخة محلية أحادية المستخدم — لا تسجيل دخول ولا خدمة سحابية ولا مزامنة.")
    st.info(
        "البيانات الوصفية المحفوظة تظل على هذا الجهاز في مجلد البيانات المحدد. "
        "لا ترفع ملفات عملاء حقيقية إلى معاينة مشتركة، ولا تعتبر هذه النسخة جاهزة للإطلاق العام."
    )
    flash_message = st.session_state.pop("screen_print_flash", None)
    if flash_message:
        st.success(flash_message)

    archive_tab, inspect_tab, estimate_tab = st.tabs(
        ["الأرشيف", "فحص صورة فيلم", "تقدير أمر شغل"]
    )
    with archive_tab:
        render_archive()
    with inspect_tab:
        render_inspection()
    with estimate_tab:
        render_estimator()


if __name__ == "__main__":
    main()
