"""
فهرس المعرفة (Knowledge Corpus & Grounded Retrieval)
=====================================================
الطبقة التي تحوّل «ملفات وأبحاثاً» إلى **إجابات موثّقة بمصدر**:

  • تقطيع (Chunking) يحافظ على السطر والمرجع، لا نص مسحوق بلا هوية.
  • بحث TF-IDF معياري يعيد **اقتباساً + مصدراً + أرقام أسطر** لكل نتيجة.
  • استمرارية JSONL: يبقى الفهرس بعد إعادة التشغيل، ويُحدَّث بلا تكرار.

لماذا هذا هو جوهر «جلب الأبحاث لتفيد النظام»؟ لأن النموذج وحده لا يستطيع
الاستشهاد بما لم يقرأه؛ وبدون مصدر+سطر تصبح الإجابة ادّعاءً لا يمكن التحقق منه.

قاعدة أمان: الفهرس لا يقرأ خارج الجذور المسموحة (يُفرض في طبقة الأدوات)،
ولا يخزّن أي محتوى إلا ما مرّره المتصل صراحةً.
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
from pathlib import Path

from pydantic import BaseModel, Field

from src.core.memory import tokenize

logger = logging.getLogger("new-cela.knowledge")

CHUNK_CHARS = 900
CHUNK_OVERLAP = 150
MAX_CHUNKS_PER_DOC = 400
MIN_SEARCH_SCORE = 0.03

_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")


class Chunk(BaseModel):
    """قطعة نصية من مصدر — وحدة الاسترجاع والاستشهاد."""

    id: str
    source: str
    title: str = ""
    text: str
    start_line: int = 1
    end_line: int = 1
    sha: str = ""

    @property
    def citation(self) -> str:
        """مرجع مختصر يُلحق بالاقتباس: `[المصدر#L12-L18]`."""
        return f"[{self.source}#L{self.start_line}-L{self.end_line}]"


class CorpusHit(BaseModel):
    """نتيجة استرجاع مع درجة وتفكيك المطابقة (شفافية للتدقيق)."""

    chunk: Chunk
    score: float
    matched: list[str] = Field(default_factory=list)

    def render(self, max_chars: int = 700) -> str:
        body = self.chunk.text.strip()
        if len(body) > max_chars:
            body = body[: max_chars - 1] + "…"
        title = f" — {self.chunk.title}" if self.chunk.title else ""
        return f"{self.chunk.citation}{title}\n{body}"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def chunk_document(
    text: str,
    source: str,
    title: str = "",
    chunk_chars: int = CHUNK_CHARS,
    overlap: int = CHUNK_OVERLAP,
    max_chunks: int = MAX_CHUNKS_PER_DOC,
) -> list[Chunk]:
    """
    يقسّم نصاً إلى قطع متداخلة مع تتبّع أرقام الأسطر.

    التقطيع يفضّل حدود الفقرات، فلا تُقطع الجملة في منتصفها إن أمكن.
    """
    if not text or not text.strip():
        return []

    paragraphs: list[tuple[int, str]] = []
    cursor_line = 1
    for block in _PARAGRAPH_SPLIT.split(text):
        if block.strip():
            paragraphs.append((cursor_line, block))
        cursor_line += block.count("\n") + 2  # + سطر الفراغ الفاصل

    chunks: list[Chunk] = []
    buffer: list[str] = []
    buffer_len = 0
    start_line = paragraphs[0][0] if paragraphs else 1
    last_line = start_line

    def flush() -> None:
        nonlocal buffer, buffer_len
        if not buffer:
            return
        body = "\n\n".join(buffer).strip()
        if body:
            chunks.append(
                Chunk(
                    id=_sha(f"{source}:{start_line}:{body}"),
                    source=source,
                    title=title,
                    text=body,
                    start_line=start_line,
                    end_line=last_line,
                    sha=_sha(body),
                )
            )
        # تداخل: نُبقي ذيل النص ليحافظ السياق على استمراريته
        tail = body[-overlap:] if overlap > 0 and len(body) > overlap else ""
        buffer = [tail] if tail else []
        buffer_len = len(tail)

    for line_no, paragraph in paragraphs:
        if buffer_len and buffer_len + len(paragraph) > chunk_chars:
            flush()
            start_line = line_no
        if not buffer or buffer == [""]:
            start_line = line_no
        buffer.append(paragraph)
        buffer_len += len(paragraph)
        last_line = line_no + paragraph.count("\n")

    flush()
    return chunks[:max_chunks]


class CorpusIndex:
    """
    فهرس معرفة قابل للاستمرار: إضافة مستندات، بحث، واستشهاد.

    `path=None` ⇒ في الذاكرة فقط (مناسب للاختبارات).
    """

    def __init__(self, path: str | os.PathLike[str] | None = None, load: bool = True) -> None:
        self.path: Path | None = None if path in (None, "", ":memory:") else Path(path)
        self._chunks: list[Chunk] = []
        self._ids: set[str] = set()
        self._lock = threading.Lock()
        self._dirty = False
        if load and self.path is not None:
            self._load()

    # ---------------- الإضافة ----------------
    def add_document(
        self, text: str, source: str, title: str = "", max_chunks: int = MAX_CHUNKS_PER_DOC
    ) -> list[Chunk]:
        """يقطّع ويضيف مستنداً؛ القطع المكرّرة (نفس البصمة) تُتجاهل."""
        added: list[Chunk] = []
        for chunk in chunk_document(text, source=source, title=title, max_chunks=max_chunks):
            with self._lock:
                if chunk.id in self._ids:
                    continue
                self._chunks.append(chunk)
                self._ids.add(chunk.id)
            added.append(chunk)
        if added:
            self._dirty = True
            self._persist()
        return added

    def ingest_file(self, path: str | Path, source: str | None = None, title: str = "") -> int:
        """يضيف ملفاً نصياً من القرص ويعيد عدد القطع المضافة."""
        target = Path(path)
        text = target.read_text(encoding="utf-8", errors="replace")
        chunks = self.add_document(
            text,
            source=source or str(target),
            title=title or target.name,
        )
        return len(chunks)

    # ---------------- البحث ----------------
    def search(self, query: str, top_k: int = 5, min_score: float = MIN_SEARCH_SCORE) -> list[CorpusHit]:
        """بحث TF-IDF (جيب التمام) يعيد أفضل القطع مع كلمات المطابقة."""
        query_tokens = tokenize(query)
        with self._lock:
            chunks = list(self._chunks)
        if not query_tokens or not chunks:
            return []

        q_counts = Counter(query_tokens)
        docs: list[tuple[Chunk, Counter]] = [
            (chunk, Counter(tokenize(f"{chunk.title}\n{chunk.text}"))) for chunk in chunks
        ]

        df: Counter[str] = Counter()
        for _, counts in docs:
            df.update(counts.keys())
        total = len(docs)

        def vector(counts: Counter) -> tuple[dict[str, float], float]:
            size = sum(counts.values()) or 1
            weights = {
                token: (1.0 + math.log(count))
                * (math.log((total + 1) / (df.get(token, 0) + 1)) + 1.0)
                / size
                for token, count in counts.items()
            }
            norm = math.sqrt(sum(value * value for value in weights.values())) or 1.0
            return weights, norm

        q_weights, q_norm = vector(q_counts)
        hits: list[CorpusHit] = []
        for chunk, counts in docs:
            d_weights, d_norm = vector(counts)
            overlap = [token for token in q_weights if token in d_weights]
            if not overlap:
                continue
            score = sum(q_weights[t] * d_weights[t] for t in overlap) / (q_norm * d_norm)
            if score < min_score:
                continue
            matched = sorted(overlap, key=lambda t: -q_weights[t])[:5]
            hits.append(CorpusHit(chunk=chunk, score=round(score, 4), matched=matched))

        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[: max(0, top_k)]

    def context_for(self, query: str, top_k: int = 4, max_chars: int = 1600) -> str:
        """
        كتلة سياق جاهزة للحقن في الـ prompt — مع مراجع إلزامية.

        نص فارغ عند عدم وجود نتائج (لا نُلوّث الطلب بلا فائدة).
        """
        hits = self.search(query, top_k=top_k)
        if not hits:
            return ""

        header = (
            "📚 مقتطفات موثّقة من فهرس المعرفة (استشهد بالمرجع كما هو، "
            "ولا تنسب للنص ما ليس فيه):"
        )
        lines = [header]
        for hit in hits:
            block = f"- {hit.render()}"
            if len("\n".join([*lines, block])) > max_chars:
                break
            lines.append(block)
        return "\n".join(lines)

    # ---------------- التفتيش ----------------
    def stats(self) -> dict[str, object]:
        with self._lock:
            chunks = list(self._chunks)
        sources = Counter(chunk.source for chunk in chunks)
        return {
            "chunks": len(chunks),
            "sources": len(sources),
            "top_sources": sources.most_common(5),
            "chars": sum(len(chunk.text) for chunk in chunks),
            "path": str(self.path) if self.path else None,
        }

    def sources(self) -> list[str]:
        with self._lock:
            return sorted({chunk.source for chunk in self._chunks})

    def all_chunks(self) -> list[Chunk]:
        with self._lock:
            return list(self._chunks)

    def __len__(self) -> int:
        with self._lock:
            return len(self._chunks)

    # ---------------- الاستمرارية ----------------
    def _persist(self) -> None:
        """كتابة ذرّية للملف (لا فهرس نصف مكتوب)."""
        if self.path is None or not self._dirty:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(self.path.suffix + ".tmp")
            with self._lock:
                chunks = list(self._chunks)
            with open(temp, "w", encoding="utf-8") as handle:
                handle.writelines(json.dumps(chunk.model_dump(mode="json"), ensure_ascii=False) + "\n" for chunk in chunks)
            os.replace(temp, self.path)
            self._dirty = False
        except OSError as exc:
            logger.warning("تعذّر حفظ الفهرس (%s) — المحتوى سليم في الذاكرة.", type(exc).__name__)

    def _load(self) -> None:
        assert self.path is not None
        if not self.path.exists():
            return
        skipped = 0
        try:
            lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            logger.warning("تعذّر قراءة الفهرس (%s).", type(exc).__name__)
            return

        for line in lines:
            if not line.strip():
                continue
            try:
                chunk = Chunk.model_validate(json.loads(line))
            except Exception:  # noqa: BLE001 — سطر تالف لا يُسقط الفهرس
                skipped += 1
                continue
            if chunk.id in self._ids:
                continue
            self._chunks.append(chunk)
            self._ids.add(chunk.id)

        if skipped:
            logger.warning("تخطّي %d سطراً تالفاً في الفهرس.", skipped)
        logger.info("فهرس المعرفة: %d قطعة من %d مصدراً.", len(self._chunks), len(self.sources()))
