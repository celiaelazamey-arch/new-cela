"""اختبارات فهرس المعرفة — التقطيع، الاستشهاد، البحث، والاستمرارية."""
from __future__ import annotations

from pathlib import Path

from src.knowledge.corpus import (
    CHUNK_CHARS,
    CorpusIndex,
    chunk_document,
)

TEXT = """مقدمة عن نظام New33

النظام يبني عقوداً صريحة بين المخطط والمنفّذ والناقد. كل عقد يحمل شرطاً
قابلاً للتحقق، ودليلاً نصياً، وبصمة SHA-256 تمنع التلاعب.

## الذاكرة الاستراتيجية

الذاكرة تحفظ الدروس المستفادة من المهام السابقة. البحث فيها معجمي بالكامل
بلا أي خدمة خارجية، وهذا يخفض التكلفة إلى الصفر ويحفظ الخصوصية.

## ميكانيكا الكم

المحاكي الكمومي يعمل محلياً. لا نزعم تسريعاً؛ الأبحاث الحديثة تُظهر غياب
ميزة قابلة للتكرار على البيانات العادية.
"""


def test_chunk_document_returns_chunks_with_citations() -> None:
    chunks = chunk_document(TEXT, source="notes.md", title="ملاحظات")
    assert chunks
    for chunk in chunks:
        assert chunk.source == "notes.md"
        assert chunk.title == "ملاحظات"
        assert chunk.start_line >= 1
        assert chunk.end_line >= chunk.start_line
        assert chunk.citation.startswith("[notes.md#L")
        assert len(chunk.sha) == 16


def test_chunk_line_numbers_are_monotonic_and_bounded() -> None:
    chunks = chunk_document(TEXT, source="notes.md")
    total_lines = TEXT.count("\n") + 1
    starts = [chunk.start_line for chunk in chunks]
    assert starts == sorted(starts)
    assert all(1 <= chunk.end_line <= total_lines for chunk in chunks)


def test_chunk_respects_max_chars() -> None:
    long_text = "\n\n".join(f"فقرة رقم {i} " + ("نص " * 60) for i in range(30))
    chunks = chunk_document(long_text, source="long.md", chunk_chars=300, overlap=50)
    assert len(chunks) > 1
    # القطعة قد تتجاوز الحد بقليل بسبب التداخل، لكن ليس بلا حد
    assert all(len(chunk.text) <= 300 * 2 for chunk in chunks)


def test_chunk_max_chunks_cap() -> None:
    long_text = "\n\n".join(f"فقرة {i}" for i in range(500))
    chunks = chunk_document(long_text, source="cap.md", chunk_chars=20, max_chunks=5)
    assert len(chunks) == 5


def test_empty_text_gives_no_chunks() -> None:
    assert chunk_document("", source="x") == []
    assert chunk_document("   \n\n  ", source="x") == []


def test_add_document_deduplicates_on_reingest() -> None:
    index = CorpusIndex()
    first = index.add_document(TEXT, source="notes.md")
    assert first
    second = index.add_document(TEXT, source="notes.md")
    assert second == []
    assert len(index) == len(first)


def test_ingest_file_reads_from_disk(tmp_path: Path) -> None:
    path = tmp_path / "doc.md"
    path.write_text(TEXT, encoding="utf-8")
    index = CorpusIndex()
    assert index.ingest_file(path) > 0
    assert str(path) in index.sources()


def test_search_finds_relevant_chunk_with_score() -> None:
    index = CorpusIndex()
    index.add_document(TEXT, source="notes.md")
    hits = index.search("الذاكرة الاستراتيجية")
    assert hits
    assert hits[0].score > 0
    assert "الذاكرة" in hits[0].chunk.text
    assert hits[0].render().startswith("[notes.md#L")


def test_search_empty_query_returns_nothing() -> None:
    index = CorpusIndex()
    index.add_document(TEXT, source="notes.md")
    assert index.search("") == []
    assert index.search("   ؟؟ !!  ") == []


def test_search_on_empty_index_returns_nothing() -> None:
    assert CorpusIndex().search("أي شيء") == []


def test_search_top_k_respected() -> None:
    index = CorpusIndex()
    for i in range(5):
        index.add_document(f"المهمة رقم {i}\n\nالمهمة والخطة والتنفيذ {i}", source=f"d{i}.md")
    hits = index.search("المهمة", top_k=2)
    assert len(hits) <= 2


def test_context_for_includes_header_and_citation() -> None:
    index = CorpusIndex()
    index.add_document(TEXT, source="notes.md")
    block = index.context_for("ميكانيكا الكم")
    assert "فهرس المعرفة" in block
    assert "[notes.md#L" in block


def test_context_for_empty_when_no_match() -> None:
    index = CorpusIndex()
    index.add_document(TEXT, source="notes.md")
    assert index.context_for("zzzz qqqq xxxx") == ""


def test_stats_and_sources() -> None:
    index = CorpusIndex()
    index.add_document(TEXT, source="a.md")
    index.add_document("نص آخر\n\nقصير جداً", source="b.md")
    stats = index.stats()
    assert stats["chunks"] == len(index)
    assert stats["sources"] == 2
    assert index.sources() == ["a.md", "b.md"]
    assert stats["chars"] > 0


def test_persistence_round_trip(tmp_path: Path) -> None:
    store = tmp_path / "corpus.jsonl"
    index = CorpusIndex(store)
    index.add_document(TEXT, source="notes.md")
    assert store.exists()
    expected = len(index)

    reloaded = CorpusIndex(store)
    assert len(reloaded) == expected
    assert reloaded.sources() == ["notes.md"]
    assert reloaded.search("الذاكرة")


def test_persistence_skips_duplicates_after_reload(tmp_path: Path) -> None:
    store = tmp_path / "corpus.jsonl"
    first = CorpusIndex(store)
    first.add_document(TEXT, source="notes.md")

    second = CorpusIndex(store)
    assert second.add_document(TEXT, source="notes.md") == []
    assert len(second) == len(first)


def test_corrupt_store_does_not_crash(tmp_path: Path) -> None:
    store = tmp_path / "corpus.jsonl"
    store.write_text('{"bad": "record"}\nليس JSON\n', encoding="utf-8")
    index = CorpusIndex(store)  # لا يرفع استثناء
    assert len(index) == 0


def test_path_none_is_memory_only(tmp_path: Path) -> None:
    index = CorpusIndex(None)
    index.add_document(TEXT, source="notes.md")
    assert index.stats()["path"] is None
    assert not list(tmp_path.iterdir())


def test_long_document_chunk_size_default() -> None:
    body = "\n\n".join("جملة " * 40 for _ in range(40))
    chunks = chunk_document(body, source="big.md")
    assert len(chunks) > 1
    assert all(len(chunk.text) < CHUNK_CHARS * 2 for chunk in chunks)
