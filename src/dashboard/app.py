"""
لوحة متابعة new-cela (Streamlit Dashboard)
==========================================
واجهة **للقراءة فقط** لمراقبة:

  • الحالة الحية لأحدث تشغيل: الخطوات، حالة كل خطوة، تقييم المقيّم، والمدة.
  • الذاكرة التي استُرجعت فعلاً قبل التخطيط، وما تعلّمه الوكيل بعده.
  • سجل التشغيلات السابقة وأداء الوكيل عبر الزمن.
  • الذاكرة الاستراتيجية: استعلام حقيقي بنفس محرك الاسترجاع، وتصدير JSON.

التشغيل:
    python -m src.dashboard            # أو: streamlit run src/dashboard/app.py

لا تشغّل هذه الواجهة أي مهمة ولا تعدّل أي بيانات — تقرأ ملفات
`.agent_runs/*.json` و `.agent_memory/memory.jsonl` فقط.
"""
from __future__ import annotations

import sys
from pathlib import Path

# تشغيل الملف مباشرة عبر `streamlit run` يتطلب إضافة جذر المستودع للمسار
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import streamlit as st

from src.config import Settings
from src.core.schemas import MemoryKind, RunStatus
from src.dashboard import data

REFRESH_SECONDS = "3s"

_RTL_CSS = """
<style>
  .stApp { direction: rtl; }
  [data-testid="stSidebar"] { direction: rtl; }
  .stMarkdown, .stCaption, label, h1, h2, h3, h4, p { text-align: right; }
  code, pre, .stCode { direction: ltr; text-align: left; }
  [data-testid="stMetricValue"] { font-size: 1.35rem; }
  .block-container { padding-top: 2.2rem; }
</style>
"""


def empty_state(title: str, hint: str, command: str | None = None) -> None:
    """حالة فارغة مفيدة بدل صفحة بيضاء."""
    st.info(title)
    st.caption(hint)
    if command:
        st.code(command, language="bash")


def show_table(rows: list[dict]) -> None:
    """
    يعرض جدولاً بعرض كامل مع توافق بين الإصدارات:
    `width="stretch"` في الحديثة، و`use_container_width` في القديمة.
    """
    try:
        st.dataframe(rows, width="stretch", hide_index=True)
    except TypeError:  # إصدار قديم لا يعرف `width`
        show_table(rows)


def render_metrics(summary: dict) -> None:
    columns = st.columns(4)
    columns[0].metric("الحالة", f"{summary['status_icon']} {summary['status_text']}")
    columns[1].metric(
        "الخطوات المنجزة",
        f"{summary['steps_done']} / {summary['steps_planned']}",
        delta=f"{summary['steps_failed']} فاشلة" if summary["steps_failed"] else None,
    )
    columns[2].metric("متوسط التقييم", f"{summary['average_score']} / 10")
    columns[3].metric("المدة", summary["duration"])


def render_report(report, show_task: bool = True) -> None:
    """يعرض تقرير تشغيل واحد — يُستخدم في التبويب الحيّ وفي سجل التشغيلات."""
    summary = data.summarize(report)
    render_metrics(summary)

    st.progress(
        min(1.0, float(summary["progress"])),
        text=f"التقدّم: {int(float(summary['progress']) * 100)}%",
    )

    if show_task:
        st.markdown(f"**المهمة:** {summary['task']}")
    st.caption(
        f"بدء: {summary['started_at']} • انتهاء: {summary['finished_at']} "
        f"• ذكريات مسترجعة: {summary['memory_hits']} • ذكريات مكتوبة: {summary['memories_written']}"
    )

    if summary["status"] == "stale":
        st.warning(
            "هذا التشغيل ما زال مسجّلاً «قيد التنفيذ» لكن مضى عليه وقت طويل — "
            "الأرجح أن العملية أُوقفت قبل أن تُغلق تقريرها."
        )
    elif report.status is RunStatus.RUNNING:
        st.caption("🟡 التشغيل جارٍ الآن — البيانات تُقرأ من القرص وتُحدَّث تلقائياً.")
    elif report.status is RunStatus.FAILED:
        st.error(f"فشل التشغيل: {summary['error'] or 'سبب غير مسجّل'}")

    st.subheader("🧩 الخطوات")
    rows = data.step_rows(report)
    if rows:
        show_table(rows)
    else:
        st.caption("لم تُنفَّذ أي خطوة بعد.")

    with st.expander(f"🧠 الذاكرة المستخدمة في هذا التشغيل ({len(report.memory_hits)})"):
        if report.memory_hits:
            show_table(data.memory_hit_rows(report))
            st.caption("الدرجة = تشابه TF-IDF × وزن النوع × جودة التقييم × حداثة طفيفة.")
        else:
            st.caption("لم تُسترجع أي ذكرى ذات صلة — بدأ الوكيل من الصفر.")

    if report.answer:
        st.subheader("📄 الإجابة النهائية")
        st.markdown(report.answer)


def render_live(runs_dir: str, auto_refresh: bool) -> None:
    """تبويب الحالة الحية — يُعاد تنفيذه تلقائياً أثناء التشغيل."""
    refresh = REFRESH_SECONDS if auto_refresh else None

    @st.fragment(run_every=refresh)
    def live_block() -> None:
        report = data.latest_run(runs_dir)
        if report is None:
            empty_state(
                "لا يوجد أي تشغيل مسجّل بعد.",
                "شغّل مهمة من الطرفية وستظهر هنا خلال ثوانٍ.",
                'python -m src.main --task "حلّل تكاليف مشروعي" --mode reason',
            )
            return
        render_report(report)

    live_block()


def render_history(runs_dir: str) -> None:
    """تبويب سجل التشغيلات: مؤشرات عامة + جدول + تفاصيل أي تشغيل."""
    reports = data.load_runs(runs_dir, limit=50)
    if not reports:
        empty_state(
            "السجل فارغ.",
            "كل تشغيل يكتب تقريراً في مجلد سجل التشغيلات (افتراضياً .agent_runs/).",
            'python -m src.main --task "مهمة" --runs-dir .agent_runs',
        )
        return

    stats = data.aggregate(reports)
    columns = st.columns(4)
    columns[0].metric("إجمالي التشغيلات", stats["total"])
    columns[1].metric("نسبة النجاح", f"{stats['success_rate']}%")
    columns[2].metric("متوسط التقييم", f"{stats['average_score']} / 10")
    columns[3].metric("ذكريات مكتسبة", stats["memories_written"])

    table = [
        {
            "التشغيل": f"{data.status_icon(report)} {report.id}",
            "المهمة": report.task[:80],
            "الحالة": data.status_text(report),
            "الخطوات": f"{report.steps_done} / {max(report.planned_steps, len(report.steps))}",
            "متوسط التقييم": report.average_score,
            "المدة": data.format_duration(report.duration_seconds),
            "البدء": data.format_time(report.started_at),
        }
        for report in reports
    ]
    show_table(table)

    st.divider()
    options = {f"{report.id} — {report.task[:70]}": index for index, report in enumerate(reports)}
    choice = st.selectbox("اختر تشغيلاً لعرض تفاصيله", list(options.keys()), index=0)
    selected = reports[options[choice]]
    st.markdown(f"#### تفاصيل التشغيل `{selected.id}`")
    render_report(selected)


def render_memory(memory_path: str) -> None:
    """تبويب الذاكرة: إحصاءات + استعلام حقيقي + أحدث الذكريات + تصدير."""
    overview = data.memory_overview(memory_path)
    counts = overview["counts"]

    columns = st.columns(4)
    columns[0].metric("إجمالي الذكريات", overview["total"])
    columns[1].metric("💡 دروس", counts[MemoryKind.LESSON])
    columns[2].metric("❌ إخفاقات", counts[MemoryKind.FAILURE])
    columns[3].metric("✅ نجاحات", counts[MemoryKind.SUCCESS])

    if overview["total"] == 0:
        empty_state(
            "الذاكرة فارغة حالياً.",
            "تُكتب الذاكرة تلقائياً بعد كل مهمة ناجحة (أو بعد درس من مهمة فاشلة).",
            'python -m src.main --task "مهمة" --mode reason',
        )
        return

    st.caption(
        f"الملف: {overview['path'] or '—'} • أحدث ذكرى: {overview['latest'] or '—'}"
    )

    st.subheader("🔍 استعلام حقيقي على الذاكرة")
    st.caption("نفس محرك TF-IDF الذي يعمل قبل كل مهمة — هنا ترى ما سيراه الوكيل بالضبط.")
    query = st.text_input(
        "اكتب مهمة أو كلمات مفتاحية",
        placeholder="مثال: تكاليف المشروع",
    )
    available = [MemoryKind.LESSON, MemoryKind.FAILURE, MemoryKind.SUCCESS, MemoryKind.FACT]
    selected_labels = st.multiselect(
        "تصفية بالنوع (اتركه فارغاً للكل)",
        options=[data.KIND_LABELS[kind] for kind in available],
    )
    kinds = [kind for kind in available if data.KIND_LABELS[kind] in selected_labels]
    top_k = st.slider("عدد النتائج", min_value=1, max_value=20, value=8)

    if query.strip():
        hits = data.search_memory(memory_path, query, top_k=top_k, kinds=kinds or None)
        if hits:
            show_table(data.hit_rows(hits))
        else:
            st.warning("لا توجد ذكرى مطابقة بدرجة كافية — جرّب كلمات أقل أو أعمّ.")

    st.subheader("📚 أحدث الذكريات")
    show_table(data.recent_entries(memory_path, limit=30))

    st.download_button(
        "⬇️ تصدير الذاكرة (JSON)",
        data=data.memory_export(memory_path),
        file_name="agent_memory.json",
        mime="application/json",
    )


def main() -> None:
    st.set_page_config(page_title="new-cela | لوحة المتابعة", page_icon="🛰️", layout="wide")
    st.markdown(_RTL_CSS, unsafe_allow_html=True)

    # Settings() جديد في كل إعادة تشغيل ⇒ تغيير متغيرات البيئة ينعكس فوراً
    settings = Settings()

    with st.sidebar:
        st.header("⚙️ الإعدادات")
        runs_dir = st.text_input("مجلد سجل التشغيلات", value=settings.agent_runs_path)
        memory_path = st.text_input("ملف الذاكرة", value=settings.agent_memory_path)
        auto_refresh = st.toggle(
            "تحديث تلقائي كل 3 ثوانٍ",
            value=True,
            help="يفيد أثناء تشغيل مهمة — يعيد قراءة أحدث تقرير من القرص.",
        )
        st.divider()
        st.markdown("**تشغيل مهمة جديدة**")
        st.code('python -m src.main --task "..." --mode reason', language="bash")
        st.caption("اللوحة للقراءة فقط: لا تشغّل مهام ولا تعدّل بيانات.")

    st.title("🛰️ لوحة متابعة new-cela")
    st.caption("خطوات الوكيل • تقييمات المقيّم • الذاكرة الاستراتيجية")

    live_tab, history_tab, memory_tab = st.tabs(
        ["🛰️ الحالة الحية", "🗂️ سجل التشغيلات", "🧠 الذاكرة"]
    )
    with live_tab:
        render_live(runs_dir, auto_refresh)
    with history_tab:
        render_history(runs_dir)
    with memory_tab:
        render_memory(memory_path)


main()
