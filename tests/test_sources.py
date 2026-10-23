"""اختبارات جالب الأبحاث — تحليل الاستجابات، التكرار، الكاش، والفشل المُحتمَل."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.knowledge.corpus import CorpusIndex
from src.knowledge.sources import (
    MAX_RESULTS_CAP,
    ArxivSource,
    CrossrefSource,
    FetchError,
    Paper,
    PaperSearch,
    SearchReport,
    SemanticScholarSource,
    _http_get,
    _normalise_title,
    build_parser,
    ingest_papers,
)

ARXIV_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2402.02716v1</id>
    <published>2024-02-05T10:00:00Z</published>
    <title>Understanding the planning of LLM agents</title>
    <summary>We survey planning approaches   for LLM agents.</summary>
    <author><name>Xu Huang</name></author>
    <author><name>Wei Liu</name></author>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2503.16416v2</id>
    <published>2025-03-20T00:00:00Z</published>
    <title>Evaluation of LLM Agents</title>
    <summary>Benchmarks and gaps.</summary>
    <author><name>Someone</name></author>
  </entry>
</feed>
"""

S2_JSON = json.dumps(
    {
        "data": [
            {
                "paperId": "abc123",
                "title": "Evaluation of LLM Agents",
                "authors": [{"name": "Someone"}],
                "year": 2025,
                "abstract": "Duplicate title, should be merged.",
                "url": "https://s2/abc123",
                "citationCount": 42,
                "externalIds": {"DOI": "10.1/x"},
            },
            {
                "paperId": "def456",
                "title": "Quantum Annealing for Scheduling",
                "authors": [{"name": "A. Author"}, {"name": "B. Author"}],
                "year": 2024,
                "abstract": "QUBO beats classical? Not always.",
                "url": "https://s2/def456",
                "citationCount": 7,
                "externalIds": {},
            },
        ]
    }
).encode()

CROSSREF_JSON = json.dumps(
    {
        "message": {
            "items": [
                {
                    "DOI": "10.9/zzz",
                    "title": ["Quantum Annealing for Scheduling"],
                    "author": [{"given": "C.", "family": "Writer"}],
                    "abstract": "<p>Same title again.</p>",
                    "issued": {"date-parts": [[2023]]},
                    "is-referenced-by-count": 3,
                    "URL": "https://doi.org/10.9/zzz",
                }
            ]
        }
    }
).encode()


def test_arxiv_parse_extracts_fields() -> None:
    papers = ArxivSource.parse(ARXIV_XML)
    assert len(papers) == 2
    first = papers[0]
    assert first.title == "Understanding the planning of LLM agents"
    assert first.authors == ["Xu Huang", "Wei Liu"]
    assert first.year == 2024
    assert first.id == "2402.02716v1"
    assert first.url.startswith("http://arxiv.org/abs/")
    assert "  " not in first.abstract  # مسافات مُطبَّعة
    assert "[arxiv:2402.02716v1]" in first.render()


def test_s2_parse_extracts_citations_and_doi() -> None:
    papers = SemanticScholarSource.parse(S2_JSON)
    assert len(papers) == 2
    assert papers[0].citations == 42
    assert papers[0].doi == "10.1/x"
    assert papers[0].source == "s2"


def test_crossref_parse_strips_html_from_abstract() -> None:
    papers = CrossrefSource.parse(CROSSREF_JSON)
    assert len(papers) == 1
    assert "<p>" not in papers[0].abstract
    assert papers[0].year == 2023
    assert papers[0].citations == 3


def test_arxiv_url_encodes_query_and_caps_limit() -> None:
    url = ArxivSource().build_url("quantum annealing & scheduling", 999)
    assert "max_results=25" in url
    assert "quantum+annealing" in url or "quantum%20annealing" in url
    assert "sortBy=relevance" in url


def test_s2_url_includes_fields() -> None:
    url = SemanticScholarSource().build_url("agents", 3)
    assert "citationCount" in url
    assert "limit=3" in url


def test_normalise_title_ignores_punctuation() -> None:
    assert _normalise_title("Evaluation of LLM Agents!") == _normalise_title("evaluation of llm-agents")


class FakeSource:
    def __init__(self, name: str, papers: list[Paper] | None = None, error: str = "") -> None:
        self.name = name
        self.papers = papers or []
        self.error = error
        self.calls = 0

    def search(self, query: str, limit: int = 5) -> list[Paper]:
        self.calls += 1
        if self.error:
            raise FetchError(self.error)
        return self.papers[:limit]


def paper(title: str, **kwargs) -> Paper:
    return Paper(id=kwargs.pop("id", title[:6]), title=title, source=kwargs.pop("source", "t"), **kwargs)


def test_search_merges_sources_and_dedupes() -> None:
    sources = [
        FakeSource("a", [paper("Evaluation of LLM Agents", citations=5)]),
        FakeSource("b", [paper("evaluation of llm-agents!", citations=99)]),
        FakeSource("c", [paper("Other Work", citations=1)]),
    ]
    report = PaperSearch(sources=sources).search("agents", limit=5, use_cache=False)
    assert len(report.papers) == 2
    assert report.papers[0].citations == 99  # الأفضل استشهاداً أولاً


def test_search_survives_source_failure() -> None:
    sources = [
        FakeSource("broken", error="network down"),
        FakeSource("good", [paper("Usable Result")]),
    ]
    report = PaperSearch(sources=sources).search("x", use_cache=False)
    assert len(report.papers) == 1
    assert "broken" in report.errors
    assert "broken" in report.summary()


def test_search_respects_limit_and_cap() -> None:
    papers = [paper(f"Title number {i}") for i in range(40)]
    report = PaperSearch(sources=[FakeSource("s", papers)]).search("t", limit=3, use_cache=False)
    assert len(report.papers) == 3

    capped = PaperSearch(sources=[FakeSource("s", papers)]).search("t", limit=999, use_cache=False)
    assert len(capped.papers) == MAX_RESULTS_CAP


def test_empty_query_returns_empty_report() -> None:
    source = FakeSource("s", [paper("A")])
    report = PaperSearch(sources=[source]).search("   ", use_cache=False)
    assert report.papers == []
    assert source.calls == 0


def test_cache_hit_avoids_second_call(tmp_path: Path) -> None:
    source = FakeSource("s", [paper("Cached Result", citations=2)])
    finder = PaperSearch(sources=[source], cache_path=tmp_path / "cache.json")
    first = finder.search("cache me", limit=2)
    second = finder.search("cache me", limit=2)

    assert source.calls == 1
    assert second.from_cache is True
    assert second.papers[0].title == "Cached Result"
    assert first.papers == second.papers


def test_expired_cache_is_ignored(tmp_path: Path) -> None:
    source = FakeSource("s", [paper("Fresh")])
    cache = tmp_path / "cache.json"
    finder = PaperSearch(sources=[source], cache_path=cache, ttl_hours=1)
    finder.search("stale query", limit=2)

    store = json.loads(cache.read_text(encoding="utf-8"))
    for entry in store.values():
        entry["saved_at"] = (datetime.now(UTC) - timedelta(hours=5)).isoformat()
    cache.write_text(json.dumps(store), encoding="utf-8")

    again = finder.search("stale query", limit=2)
    assert again.from_cache is False
    assert source.calls == 2


def test_corrupt_cache_is_tolerated(tmp_path: Path) -> None:
    cache = tmp_path / "cache.json"
    cache.write_text("ليس JSON", encoding="utf-8")
    source = FakeSource("s", [paper("Still Works")])
    report = PaperSearch(sources=[source], cache_path=cache).search("q", use_cache=True)
    assert report.papers[0].title == "Still Works"


def test_no_cache_path_means_no_files(tmp_path: Path) -> None:
    finder = PaperSearch(sources=[FakeSource("s", [paper("Memory Only")])])
    finder.search("q", use_cache=False)
    assert not list(tmp_path.iterdir())


def test_ingest_papers_feeds_corpus() -> None:
    report = SearchReport(
        query="q",
        papers=[paper("Quantum Annealing for Scheduling", abstract="ملخص تجريبي", source="arxiv", id="2402.02716")],
    )
    corpus = CorpusIndex()
    added = ingest_papers(report, corpus)
    assert added > 0
    assert corpus.sources() == ["arxiv:2402.02716"]
    hits = corpus.search("Quantum Annealing Scheduling")
    assert hits
    assert "[arxiv:2402.02716#L" in hits[0].render()


def test_paper_without_abstract_still_ingests() -> None:
    report = SearchReport(query="q", papers=[paper("No Abstract Paper", source="s2", id="x1")])
    corpus = CorpusIndex()
    assert ingest_papers(report, corpus) > 0
    assert "لا يتوفر ملخص" in corpus.all_chunks()[0].text


def test_http_get_retries_then_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = {"n": 0}

    def boom(*args, **kwargs):
        attempts["n"] += 1
        raise OSError("down")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    monkeypatch.setattr("time.sleep", lambda *_: None)
    with pytest.raises(FetchError, match="OSError"):
        _http_get("http://example.invalid", retries=2)
    assert attempts["n"] == 3  # محاولة + محاولتان إعادة


def test_cli_parser_requires_query() -> None:
    parser = build_parser()
    args = parser.parse_args(["search", "quantum", "--limit", "3", "--ingest", "c.jsonl"])
    assert args.query == "quantum"
    assert args.limit == 3
    with pytest.raises(SystemExit):
        parser.parse_args(["search"])
