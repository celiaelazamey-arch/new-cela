"""واجهة محلية تجريبية لأرشيف وفحص وحساب أعمال السلك سكرين.

تشغيل: streamlit run src/screen_print/app.py
هذه الواجهة محلية أحادية المستخدم؛ ليست واجهة SaaS ولا تحتوي على تسجيل دخول.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import streamlit as st

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.screen_print import (
    FabricShade,
    FilmArchiveStore,
    FilmQCStatus,
    FilmSetRecord,
    InkType,
    calculate_screen_print_job,
)
from src.screen_print.cloud_sync import CloudSyncError, SupabaseArchiveClient, SupabaseConfig
from src.screen_print.design_tools import (
    build_mockup_prompt,
    build_pattern_prompt,
    inspect_print_resolution,
    summarize_manual_trends,
)
from src.screen_print.uploads import inspect_uploaded_image

DATA_DIR = Path(
    os.environ.get("SCREEN_PRINT_DATA_DIR", "~/.new-cela/screen_print")
).expanduser()
ARCHIVE_PATH = DATA_DIR / "film_archive.jsonl"


def get_store() -> FilmArchiveStore:
    # On a shared Streamlit host, never mix local archive files across authenticated users.
    user = st.session_state.get("cloud_user")
    if isinstance(user, dict) and isinstance(user.get("id"), str):
        try:
            user_id = str(uuid.UUID(user["id"]))
        except ValueError:
            raise ValueError("Invalid authenticated user identifier.") from None
        account_root = DATA_DIR / "accounts" / user_id
        return FilmArchiveStore(account_root / "film_archive.jsonl", allowed_root=DATA_DIR)
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


def render_design_and_mockup() -> None:
    st.warning(
        "هذه أدوات صياغة brief فقط: لا يوجد اتصال بـMidjourney أو PatternedAI أو Verve أو Botika أو Photoshop. "
        "انسخ النص إلى أداة مرخصة تملك صلاحية استخدامها، وراجع شروطها قبل رفع تصميم عميل."
    )
    pattern_tab, mockup_tab = st.tabs(["أمر نقش متكرر", "موجز موك أب"])
    with pattern_tab:
        with st.form("pattern_prompt_form"):
            motif = st.text_input("العناصر أو الفكرة *", placeholder="زهور استوائية، أشكال هندسية...")
            style = st.text_input("الأسلوب *", placeholder="بوهيمي، ألوان مائية، فيكتور...")
            palette = st.text_input("الألوان *", placeholder="أخضر زيتوني، مرجاني، كريمي")
            audience = st.text_input("الشريحة أو تفضيلات العميل", placeholder="ملابس أطفال، ألوان هادئة...")
            repeat = st.checkbox("نقش seamless قابل للتكرار", value=True)
            submitted = st.form_submit_button("صياغة الأمر")
        if submitted:
            try:
                st.session_state["pattern_prompt"] = build_pattern_prompt(
                    motif=motif,
                    style=style,
                    palette=palette,
                    target_customer=audience,
                    repeat=repeat,
                )
            except ValueError as exc:
                st.error(str(exc))
                return
            else:
                st.rerun()
        if prompt := st.session_state.get("pattern_prompt"):
            st.caption("أمر نصي قابل للنسخ — لم تُولّد صورة ولم يُستدعَ مزود خارجي.")
            st.code(prompt, language="text")

    with mockup_tab:
        with st.form("mockup_prompt_form"):
            garment = st.text_input("المنتج", placeholder="تيشيرت، هودي، tote bag...")
            model = st.text_input("وصف العارض/العارضة", placeholder="لون البشرة، المقاس، العمر التقريبي...")
            placement = st.text_input("موضع الطباعة", placeholder="منتصف الصدر، الظهر...")
            scene = st.text_input("المشهد والإضاءة", placeholder="استوديو بسيط، ضوء نهاري...")
            submitted = st.form_submit_button("إنشاء موجز الموك أب")
        if submitted:
            try:
                st.session_state["mockup_prompt"] = build_mockup_prompt(
                    garment=garment,
                    model_description=model,
                    print_placement=placement,
                    scene=scene,
                )
            except ValueError as exc:
                st.error(str(exc))
                return
            else:
                st.rerun()
        if brief := st.session_state.get("mockup_prompt"):
            st.caption("موجز نصي فقط؛ لم يُنشأ عارض أو mockup فعلي.")
            st.code(brief, language="text")


def render_print_optimization() -> None:
    st.subheader("فحص دقة ملف الطباعة")
    st.warning(
        "هذا فحص أبعاد/DPI فقط. لا يتضمن AI upscaling أو إزالة خلفية أو فصل ألوان spot/DTG، "
        "ولا يثبت جاهزية الملف للطباعة. لا ترفع تصميماً سرياً إلى المعاينة المشتركة."
    )
    uploaded = st.file_uploader(
        "ملف صورة للفحص (PNG / JPEG / TIFF، حتى 25 MiB)",
        type=["png", "jpg", "jpeg", "tif", "tiff"],
        key="print_resolution_upload",
    )
    width_cm = st.number_input("عرض الطباعة (سم)", min_value=0.1, max_value=500.0, value=28.0, key="print_width")
    height_cm = st.number_input("ارتفاع الطباعة (سم)", min_value=0.1, max_value=500.0, value=38.0, key="print_height")
    target_dpi = st.selectbox("الدقة المطلوبة (DPI)", [150, 300], index=1)
    if uploaded is None or not st.button("افحص الدقة", key="inspect_resolution"):
        return
    try:
        report = inspect_print_resolution(
            uploaded.getvalue(),
            physical_width_cm=float(width_cm),
            physical_height_cm=float(height_cm),
            target_dpi=int(target_dpi),
        )
    except ValueError as exc:
        st.error(str(exc))
        return
    st.write(f"الصيغة: {report.image.format} · الأبعاد: {report.image.width_px} × {report.image.height_px} بكسل")
    st.write("قناة شفافية موجودة:", "نعم" if report.image.has_alpha else "لا")
    left, right = st.columns(2)
    left.metric("DPI أفقي فعلي", f"{report.effective_dpi_x:.1f}")
    right.metric("DPI رأسي فعلي", f"{report.effective_dpi_y:.1f}")
    st.write(
        f"الحد الأدنى الحسابي عند {report.target_dpi} DPI: "
        f"{report.required_width_px} × {report.required_height_px} بكسل."
    )
    if report.meets_target:
        st.success("الأبعاد تحقق عتبة DPI التي اخترتها حسابياً؛ لا يعني ذلك اعتماد الملف للطباعة.")
    else:
        st.error("الأبعاد أقل من عتبة DPI المختارة؛ لا نكبّر الصورة تلقائياً أو بالذكاء الاصطناعي.")


def render_trend_notes() -> None:
    st.subheader("ملاحظات الاتجاهات والتخصيص")
    st.info(
        "أدخل ملاحظات رصدتها بنفسك؛ الأداة تحصي الكلمات محلياً فقط. لا تتصل بوسائل التواصل أو المتاجر، "
        "ولا تتنبأ بالطلب أو تقيس سلوك المشترين."
    )
    notes = st.text_area(
        "ملاحظات/كلمات اتجاه — كل سطر ملاحظة",
        placeholder="ألوان ترابية في طلبات هذا الأسبوع\\nنقوش زهرية لتيشيرتات الصيف",
        height=160,
    )
    if st.button("لخّص الملاحظات المدخلة", key="summarize_trends"):
        terms = summarize_manual_trends(notes.splitlines())
        if not terms:
            st.info("أدخل ملاحظات أولاً لاحتساب تكرار الكلمات.")
        else:
            st.dataframe(
                [{"الكلمة/الوسم": term, "عدد مرات الورود": count} for term, count in terms],
                hide_index=True,
                width="stretch",
            )


def _cloud_client() -> SupabaseArchiveClient | None:
    """Build cloud client from environment/Streamlit secrets; never embed project secrets."""
    try:
        url = os.environ.get("SUPABASE_URL") or st.secrets.get("SUPABASE_URL", "")
        key = os.environ.get("SUPABASE_ANON_KEY") or st.secrets.get("SUPABASE_ANON_KEY", "")
    except (FileNotFoundError, KeyError):
        return None
    if not url or not key:
        return None
    try:
        return SupabaseArchiveClient(SupabaseConfig(url=str(url), anon_key=str(key)))
    except ValueError:
        return None


def report(message: str) -> None:
    """Display a safe cloud operation error in the Streamlit UI."""
    st.error(message)


def _save_cloud_session(response: dict[str, object]) -> bool:
    user = response.get("user")
    token = response.get("access_token")
    if not isinstance(user, dict) or not isinstance(user.get("id"), str) or not isinstance(token, str):
        return False
    st.session_state["cloud_user"] = {"id": user["id"], "email": str(user.get("email", ""))}
    st.session_state["cloud_access_token"] = token
    return True


def _sync_archive(client: SupabaseArchiveClient, access_token: str, user_id: str) -> tuple[int, int, list[str]]:
    """Add one-sided records only; never silently overwrite a divergent same-ID record."""
    store = get_store()
    local = {record.film_id: record for record in store.list_all()}
    remote_rows = client.pull_records(access_token)
    remote = {str(row["film_id"]): FilmSetRecord.from_dict(row) for row in remote_rows}
    conflicts = sorted(
        film_id for film_id in local.keys() & remote.keys()
        if local[film_id].to_dict() != remote[film_id].to_dict()
    )
    if conflicts:
        return 0, 0, conflicts

    local_only = [record for film_id, record in local.items() if film_id not in remote]
    client.push_records(access_token, user_id, [record.to_dict() for record in local_only])
    # Re-read after insert-only upserts to detect a concurrent insert without overwriting it.
    remote_rows = client.pull_records(access_token)
    remote = {str(row["film_id"]): FilmSetRecord.from_dict(row) for row in remote_rows}
    conflicts = sorted(
        film_id for film_id in local.keys() & remote.keys()
        if local[film_id].to_dict() != remote[film_id].to_dict()
    )
    if conflicts:
        return len(local_only), 0, conflicts
    downloaded = [record for film_id, record in remote.items() if film_id not in local]
    for record in downloaded:
        store.add_film_set(record)
    return len(local_only), len(downloaded), []


def render_cloud_account() -> None:
    st.subheader("الحساب السحابي والمزامنة")
    client = _cloud_client()
    if client is None:
        st.info(
            "خدمة Supabase غير مهيأة. أضف SUPABASE_URL وSUPABASE_ANON_KEY إلى إعدادات Streamlit/البيئة، "
            "ثم نفّذ مخطط قاعدة البيانات الموضح في docs/screen-print-cloud-sync.md. لا توجد مفاتيح في المستودع."
        )
        return

    user = st.session_state.get("cloud_user")
    token = st.session_state.get("cloud_access_token")
    if not user or not token:
        st.caption("أنشئ حساباً أو سجّل الدخول بالبريد وكلمة المرور. تُحفظ جلسة الدخول في ذاكرة جلسة التطبيق فقط.")
        login_tab, signup_tab = st.tabs(["تسجيل الدخول", "إنشاء حساب"])
        with login_tab:
            with st.form("cloud_login_form"):
                email = st.text_input("البريد الإلكتروني", key="cloud_login_email")
                password = st.text_input("كلمة المرور", type="password", key="cloud_login_password")
                submitted = st.form_submit_button("دخول")
            if submitted:
                try:
                    if not _save_cloud_session(client.sign_in(email, password)):
                        st.error("لم تُنشأ جلسة. قد يتطلب الحساب تأكيد البريد الإلكتروني.")
                    else:
                        st.rerun()
                except CloudSyncError as exc:
                    report(str(exc))
        with signup_tab:
            with st.form("cloud_signup_form"):
                new_email = st.text_input("البريد الإلكتروني", key="cloud_signup_email")
                new_password = st.text_input("كلمة المرور (8 أحرف على الأقل)", type="password", key="cloud_signup_password")
                create = st.form_submit_button("إنشاء الحساب")
            if create:
                if len(new_password) < 8:
                    st.error("استخدم كلمة مرور من 8 أحرف على الأقل.")
                else:
                    try:
                        result = client.sign_up(new_email, new_password)
                        if not _save_cloud_session(result):
                            st.success("تم إرسال طلب التسجيل. راجع بريدك لتأكيد الحساب، ثم سجّل الدخول.")
                        else:
                            st.success("تم إنشاء الحساب وتسجيل الدخول.")
                            st.rerun()
                    except CloudSyncError as exc:
                        report(str(exc))
        return

    st.write(f"مسجل الدخول: **{user['email']}**")
    st.caption("المزامنة تقتصر على بيانات بطاقات الأرشيف الوصفية؛ لا تُرفع الصور أو الملفات.")
    col_sync, col_out = st.columns(2)
    if col_sync.button("مزامنة الأرشيف الآن", type="primary"):
        try:
            uploaded, downloaded, conflicts = _sync_archive(client, token, str(user["id"]))
        except (CloudSyncError, OSError, ValueError, KeyError) as exc:
            report(f"تعذرت المزامنة: {exc}")
        else:
            if conflicts:
                st.error("توجد سجلات محلية وسحابية متعارضة للمعرّفات التالية؛ لم تُستبدل أي نسخة: " + ", ".join(conflicts))
            else:
                st.success(f"اكتملت المزامنة: أُرسل {uploaded} سجل، واستُقبل {downloaded} سجل."
                           " الحذف لا يتزامن تلقائياً؛ راجع الأرشيفين قبل حذف البيانات.")
    if col_out.button("تسجيل الخروج"):
        try:
            client.sign_out(token)
        except CloudSyncError:
            report("تعذر تأكيد تسجيل الخروج من الخدمة؛ أُزيلت جلسة هذا المتصفح محلياً. أعد المحاولة إذا بقيت الجلسة نشطة.")
        st.session_state.pop("cloud_user", None)
        st.session_state.pop("cloud_access_token", None)
        st.rerun()


def main() -> None:
    st.set_page_config(page_title="مساعد مطبعة السلك سكرين — محلي", page_icon="🖨️", layout="wide")
    st.markdown(
        "<style>.stApp{direction:rtl}.stMarkdown, label, h1, h2, h3, p{text-align:right}</style>",
        unsafe_allow_html=True,
    )
    st.title("مساعد مطبعة السلك سكرين")
    st.caption("دعم تجريبي لتسجيل دخول Supabase ومزامنة يدوية لسجلات الأرشيف؛ تُبقى البيانات محلية ما لم تهيأ الخدمة وتسجل الدخول.")
    st.info(
        "المزامنة السحابية لا تشمل صوراً أو ملفات، وتعمل فقط بعد إعداد مشروع Supabase وسياسات RLS. "
        "لا ترفع ملفات عملاء حقيقية إلى معاينة مشتركة، ولا تعتبر هذه النسخة جاهزة للإطلاق العام."
    )
    flash_message = st.session_state.pop("screen_print_flash", None)
    if flash_message:
        st.success(flash_message)

    cloud_tab, archive_tab, inspect_tab, estimate_tab, design_tab, optimization_tab, trends_tab = st.tabs(
        [
            "الحساب والمزامنة",
            "الأرشيف",
            "فحص صورة فيلم",
            "تقدير أمر شغل",
            "التصميم والموك أب",
            "تحسين الطباعة",
            "الاتجاهات والتخصيص",
        ]
    )
    with cloud_tab:
        render_cloud_account()
    with archive_tab:
        render_archive()
    with inspect_tab:
        render_inspection()
    with estimate_tab:
        render_estimator()
    with design_tab:
        render_design_and_mockup()
    with optimization_tab:
        render_print_optimization()
    with trends_tab:
        render_trend_notes()


if __name__ == "__main__":
    main()
