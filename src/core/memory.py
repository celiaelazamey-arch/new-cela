"""
الذاكرة الاستراتيجية (Strategic Memory)
=======================================
ذاكرة طويلة المدى للوكيل تجعل كل مهمة تبدأ من حيث انتهت التي قبلها:

  • التخزين   — سجلات JSONL دائمة (append-only) على القرص، بلا أي خدمة خارجية.
  • الاسترجاع — ترتيب معجمي TF-IDF (Cosine) مع تطبيع عربي خفيف،
                ومكافآت للأهمية: الدرس > الفشل > النجاح > المعلومة.
  • الحقن     — كتلة نصية موجزة تُمرَّر للمخطّط والمنفّذ والمنسّق.
  • الكتابة   — بعد كل مهمة: خطوة ناجحة/فاشلة + خلاصة المهمة.

⚠️ **حدود حقيقية (توثيق صادق):** هذا استرجاع معجمي (Lexical RAG) لا متجهات
عصبية — لا يحتاج تضمينات (Embeddings) ولا مفتاح API ولا قاعدة بيانات، ويعمل
بالكامل داخل بيئة مجانية 100%. يتفوق على المطابقة الحرفية لأنه يوازن الكلمات
النادرة (IDF)، لكنه لا يفهم الترادف البعيد (مثل «سعر» و«تكلفة»).

قاعدة أمان: الذاكرة لا تُسجِّل المفاتيح ولا تفشل المهمة أبداً — أي خطأ إدخال/إخراج
يُسجَّل كتحذير ويُكمَل التنفيذ.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import threading
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from src.core.path_guard import UnsafePath, guard_path
from src.core.schemas import MemoryEntry, MemoryKind, StepRecord, StepStatus

logger = logging.getLogger("new-cela.memory")

# Allowlist لتسجيل اسم المسار في السجل — barrier يعترف به CodeQL (نمط store.py).
# يسمح بحروف/أرقام يونيكود (\w — يشمل العربية)/نقاط/شرطات/مسافات/فواصل مسارات؛
# أي محرف تحكّم أو سطر جديد ⇒ لا يطابق ⇒ يُحجب الاسم (يمنع CWE-117).
_SAFE_PATH_LABEL = re.compile(r"[\w .\-/\\]+")

# ---- حدود ثابتة تحمي حجم الطلب والقرص ----
MAX_CONTENT_CHARS = 600
MAX_RENDERED_CHARS = 1400
MIN_SIMILARITY = 0.05

# ---- تطبيع عربي خفيف (Normalization) ----
_TATWEEL_AND_DIACRITICS = re.compile(r"[\u0610-\u061A\u064B-\u0652\u0670\u06D6-\u06ED\u0640]")
# \W في بايثون يشمل الحروف العربية ولا يشمل الترقيم العربي (، ؛ ؟) — وهو المطلوب
_NON_WORD = re.compile(r"[\W_]+", re.UNICODE)
_CHAR_MAP = str.maketrans(
    {
        "أ": "ا",
        "إ": "ا",
        "آ": "ا",
        "ٱ": "ا",
        "ى": "ي",
        "ئ": "ي",
        "ؤ": "و",
        "ة": "ه",
        "ک": "ك",
        "ی": "ي",
        "ـ": "",
    }
)

# كلمات وظيفية لا تحمل دلالة — تُستبعد من الفهرسة
STOPWORDS: frozenset[str] = frozenset(
    (
        # عربية
        "من", "في", "على", "إلى", "عن", "مع", "هذا", "هذه", "ذلك", "تلك",
        "التي", "الذي", "الذين", "ما", "لا", "لم", "لن", "قد", "كان", "كانت",
        "يكون", "تكون", "هو", "هي", "هم", "هن", "أنا", "نحن", "أن", "إن",
        "أو", "ثم", "بل", "لكن", "حتى", "إذا", "كل", "بعض", "غير", "بين",
        "بعد", "قبل", "عند", "حيث", "كما", "أيضا", "أيضًا", "جدا", "جداً",
        "أي", "أيها", "يا", "و", "ف", "ب", "ل", "ك", "به", "له", "لها",
        "بهم", "منه", "منها", "عليه", "عليها", "هناك", "هنا", "الآن",
        "فقد", "وقد", "وهو", "وهي", "وما", "ولا", "ولم",
        # إنجليزية
        "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
        "is", "are", "was", "were", "be", "been", "it", "this", "that",
        "these", "those", "as", "at", "by", "from", "not", "no", "do", "does",
        "did", "can", "could", "should", "would", "will", "shall", "may",
        "might", "i", "you", "he", "she", "we", "they", "them", "his", "her",
        "its", "our", "your", "their",
    )
)

# سوابق التعريف فقط — لا نحذف الحروف المفردة (تفسد جذوراً كثيرة)
_PREFIXES = ("وال", "بال", "كال", "فال", "لل", "ال")
_SUFFIXES = ("ات", "ون", "ين", "ان", "ها", "هم", "نا", "ية", "ه", "ي")

_KIND_WEIGHT = {
    MemoryKind.LESSON: 1.20,
    MemoryKind.FAILURE: 1.10,
    MemoryKind.SUCCESS: 1.05,
    MemoryKind.FACT: 1.0,
}


def _normalize(text: str) -> str:
    """توحيد الحروف العربية وحذف التشكيل والتطويل."""
    return _TATWEEL_AND_DIACRITICS.sub("", text.lower()).translate(_CHAR_MAP)


# كلمات الوقف بعد التطبيع — حتى تُطابق «إلى» و«الى» و«أي» و«اي» معاً
_NORMALIZED_STOPWORDS = frozenset(_normalize(word) for word in STOPWORDS)


def _light_stem(token: str) -> str:
    """تجذيع خفيف وحتمي: حذف سابقة تعريف واحدة ولاحقة واحدة فقط."""
    for prefix in _PREFIXES:
        if token.startswith(prefix) and len(token) - len(prefix) >= 3:
            token = token[len(prefix) :]
            break
    for suffix in _SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            token = token[: -len(suffix)]
            break
    return token


def tokenize(text: str) -> list[str]:
    """
    يقطّع النص إلى وحدات معجمية بعد التطبيع العربي.

    الخطوات: تصغير الحروف ← حذف التشكيل والتطويل ← توحيد الألف/الياء/التاء المربوطة
    ← فصل الكلمات ← حذف كلمات الوقف ← تجذيع خفيف.
    """
    if not text:
        return []

    tokens: list[str] = []
    for raw in _NON_WORD.split(_normalize(text)):
        if len(raw) < 2 or raw in _NORMALIZED_STOPWORDS:
            continue
        if not any(character.isalpha() for character in raw):
            continue  # أرقام أو رموز فقط — لا قيمة استرجاعية لها
        stem = _light_stem(raw)
        if len(stem) >= 2 and stem not in _NORMALIZED_STOPWORDS:
            tokens.append(stem)
    return tokens


def _fingerprint(kind: MemoryKind, content: str) -> str:
    """بصمة محتوى حتمية — تُستخدم لمنع تكرار الذكريات المتطابقة."""
    payload = f"{kind.value}:{' '.join(content.lower().split())}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class MemoryHit:
    """نتيجة استرجاع واحدة مع تفكيك الدرجة (شفافية للتدقيق)."""

    entry: MemoryEntry
    score: float
    similarity: float
    matched: tuple[str, ...] = ()


def build_context_block(hits: Iterable[MemoryHit], max_chars: int = MAX_RENDERED_CHARS) -> str:
    """
    يحوّل نتائج الاسترجاع إلى كتلة نصية جاهزة للحقن في الـ prompts.
    يعيد نصاً فارغاً عند عدم وجود نتائج (حتى لا يتلوث الـ prompt بلا فائدة).
    """
    hits = list(hits)
    if not hits:
        return ""

    header = "🧠 ذاكرة استراتيجية من جلسات سابقة (استخدمها فقط إن كانت ذات صلة مباشرة):"
    lines: list[str] = [header]

    for hit in hits:
        score = f" (تقييم {hit.entry.score}/10)" if hit.entry.score else ""
        line = f"- [{hit.entry.kind.value}] {hit.entry.content}{score}"
        candidate = "\n".join([*lines, line])
        if len(candidate) > max_chars:
            break
        lines.append(line)

    if len(lines) == 1:  # حتى أول ذكرى أطول من الحد — نقتطعها بذكاء
        head = f"- [{hits[0].entry.kind.value}] "
        room = max(0, max_chars - len(header) - len(head) - 2)
        lines.append(head + hits[0].entry.content[:room] + "…")

    block = "\n".join(lines)
    if len(block) > max_chars:  # ضمان صارم: الكتلة لا تتجاوز الحد أبداً
        block = block[: max(0, max_chars - 1)].rstrip() + "…"
    return block


class MemoryStore:
    """
    مخزن الذاكرة الاستراتيجية: قائمة في الذاكرة + ملف JSONL دائم.

    - `path=None` أو `":memory:"` ⇒ تخزين في الذاكرة فقط (مناسب للاختبارات).
    - `max_entries` حد أقصى صارم يُبقي **الأحدث** ويُسقط الأقدم.
    - كل العمليات آمنة ضد التزامن عبر قفل واحد، وأي خطأ قرص لا يوقف الوكيل.
    """

    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        max_entries: int = 500,
        load: bool = True,
    ) -> None:
        self.path: Path | None
        if path is None or str(path) in {"", ":memory:"}:
            self.path = None
        else:
            # ✅ حاجز الأمان: المسار قد يأتي من st.text_input (مصدر غير موثوق).
            # يُطهَّر عند نقطة الدخول قبل أي exists/read_text/write — يعالج
            # تنبيهات CodeQL #1 و#2 على this file.
            try:
                self.path = guard_path(path)
            except UnsafePath as exc:
                # نسجّل نوع الاستثناء فقط (رسالته ثابتة) — لا تمرّ أي قيمة من المستخدم
                # إلى السجل، فيُقطع تدفّق البيانات تماماً قبل logger (CWE-117).
                logger.warning("مسار ذاكرة مرفوض (%s).", type(exc).__name__)
                raise

        self.max_entries = max(1, int(max_entries))
        self._entries: list[MemoryEntry] = []
        self._ids: set[str] = set()
        self._lock = threading.Lock()
        self._needs_compaction = False
        self._skipped_lines = 0

        if load and self.path is not None:
            self._load()

    # ---------------- الواجهة المصنعية ----------------
    @classmethod
    def from_settings(
        cls,
        settings: object,
        path: str | None = None,
        max_entries: int | None = None,
    ) -> MemoryStore:
        """ينشئ المخزن من الإعدادات المركزية (مع إمكانية تجاوز المسار)."""
        return cls(
            path=path if path is not None else getattr(settings, "agent_memory_path", None),
            max_entries=(
                max_entries
                if max_entries is not None
                else getattr(settings, "agent_memory_max_entries", 500)
            ),
        )

    # ---------------- الإضافة ----------------
    def add(
        self,
        content: str,
        kind: MemoryKind = MemoryKind.FACT,
        task: str = "",
        score: int = 0,
        tags: Iterable[str] = (),
    ) -> MemoryEntry | None:
        """
        يضيف ذكرى جديدة. يعيد `None` إذا كان النص فارغاً أو مكرراً.

        لا يرفع استثناء أبداً: فشل الكتابة على القرص يُبقي الذكرى في الذاكرة.
        """
        text = " ".join((content or "").split())
        if not text:
            return None
        text = text[:MAX_CONTENT_CHARS]

        entry = MemoryEntry(
            id=_fingerprint(kind, text),
            kind=kind,
            content=text,
            task=" ".join((task or "").split())[:MAX_CONTENT_CHARS],
            score=max(0, min(10, int(score or 0))),
            tags=[str(tag) for tag in tags if str(tag).strip()],
            created_at=_utc_now(),
        )

        with self._lock:
            if entry.id in self._ids:
                return None
            self._entries.append(entry)
            self._ids.add(entry.id)
            if len(self._entries) > self.max_entries:
                removed = len(self._entries) - self.max_entries
                self._entries = self._entries[-self.max_entries :]
                self._ids = {item.id for item in self._entries}
                self._needs_compaction = True
                logger.debug("إسقاط %d ذكرى قديمة لتجاوز الحد الأقصى.", removed)

        self._append_line(entry)
        return entry

    def add_step_record(self, record: StepRecord, task: str = "") -> MemoryEntry | None:
        """
        يحوّل سجل خطوة إلى ذكرى قابلة لإعادة الاستخدام.

        النجاح ⇒ SUCCESS، واستنفاد المحاولات ⇒ FAILURE مع آخر تغذية راجعة،
        لأن الخطأ الموثَّق يمنع تكراره في المهام القادمة.
        """
        if record.status is StepStatus.DONE:
            return self.add(
                content=f"{record.description} ← {record.result}",
                kind=MemoryKind.SUCCESS,
                task=task,
                score=record.score,
                tags=[f"attempts={record.attempts}"],
            )

        if record.status is StepStatus.FAILED:
            last = record.critiques[-1] if record.critiques else "بلا تغذية راجعة"
            fix = f" — التصحيح المقترح: {record.suggestion}" if record.suggestion else ""
            return self.add(
                content=(
                    f"فشلت خطوة «{record.description}» بعد {record.attempts} محاولة: {last}{fix}"
                ),
                kind=MemoryKind.FAILURE,
                task=task,
                score=record.score,
                tags=["failed"],
            )

        return None

    # ---------------- الاسترجاع ----------------
    def search(
        self,
        query: str,
        top_k: int = 4,
        min_score: float = MIN_SIMILARITY,
        kinds: Iterable[MemoryKind] | None = None,
    ) -> list[MemoryHit]:
        """
        يسترجع أقرب الذكريات للاستعلام بترتيب تنازلي.

        الدرجة = تشابه جيب التمام (TF-IDF) × وزن النوع × جودة التقييم × حداثة طفيفة.
        """
        query_tokens = tokenize(query)
        with self._lock:
            entries = list(self._entries)
        if not query_tokens or not entries:
            return []

        allowed = set(kinds) if kinds is not None else None
        corpus = [item for item in entries if allowed is None or item.kind in allowed]
        if not corpus:
            return []

        query_counts = Counter(query_tokens)
        docs = [(item, Counter(tokenize(f"{item.content} {item.task} {' '.join(item.tags)}"))) for item in corpus]

        # تردد المستندات (DF) على المستوى الكامل — يمنح الكلمات النادرة وزناً أعلى
        document_frequency: Counter[str] = Counter()
        for _, counts in docs:
            document_frequency.update(counts.keys())
        total_docs = len(docs)

        def vectorize(counts: Counter[str]) -> tuple[dict[str, float], float]:
            size = sum(counts.values()) or 1
            weights = {
                token: (1.0 + math.log(count))
                * (math.log((total_docs + 1) / (document_frequency.get(token, 0) + 1)) + 1.0)
                / size
                for token, count in counts.items()
            }
            norm = math.sqrt(sum(value * value for value in weights.values())) or 1.0
            return weights, norm

        query_weights, query_norm = vectorize(query_counts)
        hits: list[MemoryHit] = []

        for item, counts in docs:
            doc_weights, doc_norm = vectorize(counts)
            overlap = [token for token in query_weights if token in doc_weights]
            if not overlap:
                continue
            similarity = sum(query_weights[token] * doc_weights[token] for token in overlap) / (
                query_norm * doc_norm
            )
            if similarity <= 0:
                continue

            quality = 0.6 + 0.4 * (item.score / 10) if item.score else 1.0
            score = similarity * _KIND_WEIGHT.get(item.kind, 1.0) * quality * _recency_boost(item)
            if score < min_score:
                continue

            matched = tuple(sorted(overlap, key=lambda token: -query_weights[token])[:5])
            hits.append(
                MemoryHit(entry=item, score=round(score, 4), similarity=round(similarity, 4), matched=matched)
            )

        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[: max(0, top_k)]

    def context_for(
        self,
        query: str,
        top_k: int = 4,
        min_score: float = MIN_SIMILARITY,
        max_chars: int = MAX_RENDERED_CHARS,
    ) -> str:
        """كتلة الذاكرة الجاهزة للحقن في الـ prompt (نص فارغ عند عدم وجود نتائج)."""
        return build_context_block(
            self.search(query, top_k=top_k, min_score=min_score), max_chars=max_chars
        )

    # ---------------- التفتيش والإدارة ----------------
    def all(self) -> list[MemoryEntry]:
        """نسخة من كل الذكريات بترتيب الإضافة."""
        with self._lock:
            return list(self._entries)

    def stats(self) -> dict[str, object]:
        """إحصاءات مختصرة — تُستخدم في CLI ولوحة المتابعة لاحقاً."""
        with self._lock:
            entries = list(self._entries)
        by_kind = Counter(item.kind.value for item in entries)
        return {
            "total": len(entries),
            "by_kind": dict(sorted(by_kind.items())),
            "path": str(self.path) if self.path else None,
            "skipped_lines": self._skipped_lines,
        }

    def clear(self) -> None:
        """يمسح الذاكرة من الرام والقرص (عملية صريحة فقط)."""
        with self._lock:
            self._entries.clear()
            self._ids.clear()
            self._needs_compaction = False
        if self.path is not None:
            try:
                self.path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("تعذّر حذف ملف الذاكرة (%s).", type(exc).__name__)

    def compact(self) -> None:
        """يعيد كتابة الملف بإزالة التكرار والقديم — كتابة ذرّية عبر ملف مؤقت."""
        if self.path is None:
            return
        with self._lock:
            entries = list(self._entries)
            self._needs_compaction = False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
            with open(temp_path, "w", encoding="utf-8") as handle:
                handle.writelines(json.dumps(entry.model_dump(mode="json"), ensure_ascii=False) + "\n" for entry in entries)
            os.replace(temp_path, self.path)
        except OSError as exc:
            logger.warning("تعذّر ضغط ملف الذاكرة (%s) — الذاكرة الحية سليمة.", type(exc).__name__)

    def maybe_compact(self) -> None:
        """يضغط الملف فقط عند الحاجة الفعلية (إسقاط قديم/تخطي أسطر تالفة)."""
        if self.path is not None and self._needs_compaction:
            self.compact()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    # ---------------- الإدخال/الإخراج الداخلي ----------------
    def _append_line(self, entry: MemoryEntry) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock, open(self.path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry.model_dump(mode="json"), ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.warning(
                "تعذّر حفظ الذكرى على القرص (%s) — محفوظة في الذاكرة فقط.",
                type(exc).__name__,
            )

    def _load(self) -> None:
        """يقرأ الملف بتسامح: يتخطى الأسطر التالفة ويُبقي الأحدث فقط."""
        assert self.path is not None
        if not self.path.exists():
            return

        try:
            raw_lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            logger.warning("تعذّر قراءة ملف الذاكرة (%s).", type(exc).__name__)
            return

        ordered: dict[str, MemoryEntry] = {}
        duplicates = False
        for line in raw_lines:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entry = MemoryEntry.model_validate(json.loads(stripped))
            except Exception:  # noqa: BLE001 — سطر تالف لا يُسقط الذاكرة كلها
                self._skipped_lines += 1
                continue

            if not entry.id:
                entry = entry.model_copy(update={"id": _fingerprint(entry.kind, entry.content)})
            if entry.id in ordered:
                duplicates = True
                del ordered[entry.id]  # الأحدث يحل محل الأقدم ويأخذ آخر موضع
            ordered[entry.id] = entry

        entries = list(ordered.values())
        trimmed = len(entries) > self.max_entries
        if trimmed:
            entries = entries[-self.max_entries :]

        self._entries = entries
        self._ids = {item.id for item in entries}
        self._needs_compaction = bool(self._skipped_lines or duplicates or trimmed)
        # ✅ BARRIER محلي مطابق لنمط cognitive/store.py::_RUN_ID.fullmatch:
        # نتحقق بـ fullmatch على المتغير ثم نسجّل *نفس المتغير* بعد الـ guard.
        # أي سطر جديد/محرف تحكّم ⇒ لا يطابق ⇒ يُحجب الاسم (يمنع CWE-117).
        path_label = str(self.path)
        if _SAFE_PATH_LABEL.fullmatch(path_label):
            logger.info("تم تحميل %d ذكرى من %s.", len(entries), path_label)
        else:
            logger.info("تم تحميل %d ذكرى (اسم المسار محجوب).", len(entries))

    def __repr__(self) -> str:  # pragma: no cover - تمثيل تشخيصي
        return f"MemoryStore(path={self.path!r}, entries={len(self)})"


def _recency_boost(entry: MemoryEntry) -> float:
    """دفعة صغيرة جداً للأحدث (≤ 15%) — لا تقلب ترتيب الصلة الواضح."""
    try:
        created = datetime.fromisoformat(entry.created_at)
    except (TypeError, ValueError):
        return 1.0
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    age_days = max(0.0, (datetime.now(UTC) - created).total_seconds() / 86400.0)
    return 1.0 + 0.15 * math.exp(-age_days / 30.0)


class MemoryRetriever:
    """
    واجهة الاسترجاع التي يستخدمها `Planner`.

    تفصل سياسة الاسترجاع (k، حد أدنى للصلة، حجم الكتلة) عن التخزين،
    وتلتقط أي خطأ داخلي حتى لا تُعطّل الذاكرة حلقة التفكير.
    """

    def __init__(
        self,
        store: MemoryStore,
        top_k: int = 4,
        min_score: float = MIN_SIMILARITY,
        max_chars: int = MAX_RENDERED_CHARS,
    ) -> None:
        self.store = store
        self.top_k = max(1, int(top_k))
        self.min_score = float(min_score)
        self.max_chars = max_chars

    def search(self, query: str) -> list[MemoryHit]:
        return self.store.search(query, top_k=self.top_k, min_score=self.min_score)

    def context(self, query: str) -> str:
        """كتلة الذاكرة لهذا الاستعلام — سلسلة فارغة عند أي تعذّر."""
        try:
            hits = self.search(query)
            return build_context_block(hits, max_chars=self.max_chars)
        except Exception as exc:  # noqa: BLE001 — الذاكرة مساعدة، لا حرجة
            logger.warning("تعذّر استرجاع الذاكرة (%s) — المتابعة بدونها.", type(exc).__name__)
            return ""
