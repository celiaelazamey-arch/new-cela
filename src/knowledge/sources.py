"""
جالب الأبحاث العلمية (Free Research Fetchers)
=============================================
يجلب أوراقاً علمية من مصادر **مجانية بلا مفاتيح** ويدفعها إلى فهرس المعرفة:

  • arXiv              — عبر `export.arxiv.org/api/query` (Atom XML).
  • Semantic Scholar   — Graph API العام (محدود المعدل، بلا مفتاح).
  • Crossref           — بيانات DOI والاستشهادات (JSON، بلا مفتاح).

ضمانات:
  • **صفر تكلفة:** لا مفاتيح، لا اشتراكات، لا حسابات.
  • **مهلة وتكرار:** مهلة اتصال صريحة، وإعادة محاولة بتباعد أسّي، وحد أقصى للنتائج.
  • **كاش محلي:** نتائج البحث تُخزَّن في JSON مع صلاحية (TTL) لتقليل الطلبات.
  • **نص غير موثوق:** الملخصات بيانات تُقرأ وتُلخَّص، وليست تعليمات تُنفَّذ.
  • **بلا اعتماديات:** `urllib` من المكتبة القياسية فقط.

الاستخدام:
    python -m src.knowledge.sources search "quantum annealing job shop" --limit 5 \\
        --ingest .agent_knowledge/corpus.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger("new-cela.sources")

USER_AGENT = "new-cela/1.0 (research fetcher; zero-cost; contact via GitHub)"
DEFAULT_TIMEOUT = 12.0
MAX_RESULTS_CAP = 25
CACHE_TTL_HOURS = 72

ARXIV_API = "http://export.arxiv.org/api/query"
S2_API = "https://api.semanticscholar.org/graph/v1/paper/search"
CROSSREF_API = "https://api.crossref.org/works"

_NS = {"atom": "http://www.w3.org/2005/Atom"}
_WS = re.compile(r"\s+")


class Paper(BaseModel):
    """ورقة علمية — حقول قابلة للاستشهاد، بلا حشو."""

    id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    abstract: str = ""
    url: str = ""
    source: str = ""
    doi: str = ""
    citations: int | None = None

    @property
    def citation(self) -> str:
        year = f"{self.year}" if self.year else "بلا سنة"
        who = ", ".join(self.authors[:3]) or "مجهول"
        return f"{who} ({year}). {self.title}. {self.source}:{self.id}"

    def render(self, max_abstract: int = 500) -> str:
        abstract = self.abstract.strip()
        if len(abstract) > max_abstract:
            abstract = abstract[: max_abstract - 1] + "…"
        return (
            f"[{self.source}:{self.id}] {self.title}\n"
            f"  {self.citation}\n"
            f"  {self.url}\n"
            f"  {abstract}"
        )


class SearchReport(BaseModel):
    """نتيجة بحث مجمَّعة — تُظهر من نجح ومن فشل (شفافية)."""

    query: str
    papers: list[Paper] = Field(default_factory=list)
    errors: dict[str, str] = Field(default_factory=dict)
    from_cache: bool = False
    at: str = ""

    def summary(self) -> str:
        state = "من الكاش" if self.from_cache else "من الشبكة"
        head = f"بحث «{self.query}» — {len(self.papers)} ورقة ({state})"
        if self.errors:
            head += f" | مصادر فشلت: {', '.join(self.errors)}"
        return head


class FetchError(RuntimeError):
    """فشل طلب شبكي بعد كل المحاولات."""


# ================= طبقة الشبكة (قابلة للاستبدال في الاختبارات) =================

def _http_get(url: str, timeout: float = DEFAULT_TIMEOUT, retries: int = 2) -> bytes:
    """
    طلب GET مع مهلة وإعادة محاولة بتباعد أسّي.

    يرفع `FetchError` بعد استنفاد المحاولات — لا تعليق أبدي.
    """
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except Exception as exc:  # noqa: BLE001 — نُعيد المحاولة على أي فشل شبكي
            last = exc
            if attempt < retries:
                time.sleep(0.4 * (2**attempt))
    raise FetchError(f"{type(last).__name__}: {last}")


Transport = Callable[[str], bytes]


def _clean(text: str) -> str:
    return _WS.sub(" ", (text or "").strip())


def _year_of(text: str) -> int | None:
    match = re.search(r"(19|20)\d{2}", text or "")
    return int(match.group(0)) if match else None


# ================= المصادر =================

class ArxivSource:
    """arXiv — أفضل مصدر للنشرات الحديثة في CS/الفيزياء، بلا مفتاح."""

    name = "arxiv"

    def __init__(self, transport: Transport = _http_get) -> None:
        self._transport = transport

    def build_url(self, query: str, limit: int) -> str:
        params = {
            "search_query": f"all:{query}",
            "start": "0",
            "max_results": str(max(1, min(limit, MAX_RESULTS_CAP))),
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
        return f"{ARXIV_API}?{urllib.parse.urlencode(params)}"

    def search(self, query: str, limit: int = 5) -> list[Paper]:
        raw = self._transport(self.build_url(query, limit))
        return self.parse(raw)[:limit]

    @staticmethod
    def parse(raw: bytes) -> list[Paper]:
        root = ET.fromstring(raw)
        papers: list[Paper] = []
        for entry in root.findall("atom:entry", _NS):
            url = _clean(entry.findtext("atom:id", "", _NS))
            papers.append(
                Paper(
                    id=url.rsplit("/", 1)[-1] or hashlib.sha1(url.encode()).hexdigest()[:12],
                    title=_clean(entry.findtext("atom:title", "", _NS)),
                    authors=[
                        _clean(author.findtext("atom:name", "", _NS))
                        for author in entry.findall("atom:author", _NS)
                    ],
                    year=_year_of(entry.findtext("atom:published", "", _NS)),
                    abstract=_clean(entry.findtext("atom:summary", "", _NS)),
                    url=url,
                    source="arxiv",
                )
            )
        return [paper for paper in papers if paper.title]


class SemanticScholarSource:
    """Semantic Scholar Graph — يوفّر عدد الاستشهادات (مؤشر تأثير)."""

    name = "semantic_scholar"

    def __init__(self, transport: Transport = _http_get) -> None:
        self._transport = transport

    def build_url(self, query: str, limit: int) -> str:
        params = {
            "query": query,
            "limit": str(max(1, min(limit, MAX_RESULTS_CAP))),
            "fields": "title,authors,year,abstract,url,citationCount,externalIds",
        }
        return f"{S2_API}?{urllib.parse.urlencode(params)}"

    def search(self, query: str, limit: int = 5) -> list[Paper]:
        raw = self._transport(self.build_url(query, limit))
        return self.parse(raw)[:limit]

    @staticmethod
    def parse(raw: bytes) -> list[Paper]:
        data = json.loads(raw.decode("utf-8"))
        papers: list[Paper] = []
        for item in data.get("data", []):
            external = item.get("externalIds") or {}
            papers.append(
                Paper(
                    id=str(item.get("paperId") or external.get("ArXiv") or ""),
                    title=_clean(item.get("title", "")),
                    authors=[
                        _clean(author.get("name", ""))
                        for author in (item.get("authors") or [])
                    ],
                    year=item.get("year"),
                    abstract=_clean(item.get("abstract") or ""),
                    url=item.get("url") or "",
                    source="s2",
                    doi=external.get("DOI") or "",
                    citations=item.get("citationCount"),
                )
            )
        return [paper for paper in papers if paper.title]


class CrossrefSource:
    """Crossref — يغطي الدوريات وبيانات DOI الوصفية."""

    name = "crossref"

    def __init__(self, transport: Transport = _http_get) -> None:
        self._transport = transport

    def build_url(self, query: str, limit: int) -> str:
        params = {
            "query": query,
            "rows": str(max(1, min(limit, MAX_RESULTS_CAP))),
            "select": "DOI,title,author,abstract,issued,is-referenced-by-count,URL",
        }
        return f"{CROSSREF_API}?{urllib.parse.urlencode(params)}"

    def search(self, query: str, limit: int = 5) -> list[Paper]:
        raw = self._transport(self.build_url(query, limit))
        return self.parse(raw)[:limit]

    @staticmethod
    def parse(raw: bytes) -> list[Paper]:
        data = json.loads(raw.decode("utf-8"))
        papers: list[Paper] = []
        for item in data.get("message", {}).get("items", []):
            titles = item.get("title") or [""]
            issued = (item.get("issued") or {}).get("date-parts") or [[None]]
            papers.append(
                Paper(
                    id=str(item.get("DOI") or ""),
                    title=_clean(titles[0]),
                    authors=[
                        _clean(
                            f"{author.get('given', '')} {author.get('family', '')}".strip()
                        )
                        for author in (item.get("author") or [])
                    ],
                    year=issued[0][0] if issued and issued[0] else None,
                    abstract=_clean(re.sub(r"<[^>]+>", " ", item.get("abstract") or "")),
                    url=item.get("URL") or "",
                    source="crossref",
                    doi=str(item.get("DOI") or ""),
                    citations=item.get("is-referenced-by-count"),
                )
            )
        return [paper for paper in papers if paper.title]


# ================= التجميع والكاش =================

def _normalise_title(title: str) -> str:
    return re.sub(r"[^\w\u0600-\u06FF]+", "", title.lower())


def _merge_papers(base: Paper, extra: Paper) -> Paper:
    """
    يدمج سجلّين لنفس العنوان: يُبقي الأغنى معلومات (ملخص/DOI/رابط/استشهادات).

    التكرار بين المصادر شائع (arXiv ثم S2 ثم Crossref)؛ الدمج أفضل من الحذف
    لأنه يجمع بيانات وصفية لا تتوفر في مصدر واحد.
    """
    merged = base.model_copy()
    if (extra.citations or 0) > (merged.citations or 0):
        merged.citations = extra.citations
    if len(extra.abstract) > len(merged.abstract):
        merged.abstract = extra.abstract
    merged.doi = merged.doi or extra.doi
    merged.url = merged.url or extra.url
    merged.year = merged.year or extra.year
    if not merged.authors:
        merged.authors = list(extra.authors)
    return merged


class PaperSearch:
    """
    مجمِّع يبحث في عدة مصادر، يُزيل التكرار، ويُخزّن النتائج مؤقتاً.

    فشل مصدر لا يُسقط البحث: يُسجَّل ويُكمَل بالباقي.
    """

    def __init__(
        self,
        sources: list[Any] | None = None,
        cache_path: str | os.PathLike[str] | None = None,
        ttl_hours: int = CACHE_TTL_HOURS,
    ) -> None:
        self.sources = sources if sources is not None else [
            ArxivSource(),
            SemanticScholarSource(),
            CrossrefSource(),
        ]
        self.cache_path = Path(cache_path) if cache_path else None
        self.ttl_seconds = ttl_hours * 3600

    # ---------------- الكاش ----------------
    def _cache_key(self, query: str, limit: int) -> str:
        return hashlib.sha256(f"{query}|{limit}".encode()).hexdigest()[:24]

    def _read_cache(self, key: str) -> list[Paper] | None:
        if self.cache_path is None or not self.cache_path.exists():
            return None
        try:
            store = json.loads(self.cache_path.read_text(encoding="utf-8"))
            entry = store.get(key)
        except (OSError, ValueError):
            return None
        if not entry:
            return None
        try:
            saved = datetime.fromisoformat(entry["saved_at"])
        except (KeyError, ValueError):
            return None
        if (datetime.now(UTC) - saved).total_seconds() > self.ttl_seconds:
            return None
        try:
            return [Paper.model_validate(item) for item in entry.get("papers", [])]
        except Exception:  # noqa: BLE001 — كاش تالف ⇒ تجاهل
            return None

    def _write_cache(self, key: str, papers: list[Paper]) -> None:
        if self.cache_path is None:
            return
        try:
            store: dict[str, Any] = {}
            if self.cache_path.exists():
                store = json.loads(self.cache_path.read_text(encoding="utf-8"))
            store[key] = {
                "saved_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "papers": [paper.model_dump(mode="json") for paper in papers],
            }
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
            temp.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(temp, self.cache_path)
        except (OSError, ValueError) as exc:
            logger.warning("تعذّر كتابة كاش البحث (%s).", type(exc).__name__)

    # ---------------- البحث ----------------
    def search(self, query: str, limit: int = 5, use_cache: bool = True) -> SearchReport:
        query = (query or "").strip()
        limit = max(1, min(limit, MAX_RESULTS_CAP))
        report = SearchReport(query=query, at=datetime.now(UTC).isoformat(timespec="seconds"))
        if not query:
            return report

        key = self._cache_key(query, limit)
        if use_cache:
            cached = self._read_cache(key)
            if cached is not None:
                report.papers = cached
                report.from_cache = True
                return report

        merged: list[Paper] = []
        positions: dict[str, int] = {}
        for source in self.sources:
            try:
                found = source.search(query, limit=limit)
            except Exception as exc:  # noqa: BLE001 — مصدر واحد لا يُسقط البحث
                report.errors[getattr(source, "name", type(source).__name__)] = (
                    f"{type(exc).__name__}: {exc}"
                )
                continue
            for paper in found:
                fingerprint = _normalise_title(paper.title)
                if not fingerprint:
                    continue
                if fingerprint in positions:
                    index = positions[fingerprint]
                    merged[index] = _merge_papers(merged[index], paper)
                    continue
                positions[fingerprint] = len(merged)
                merged.append(paper)

        # الأفضل أولاً: عدد الاستشهادات، ثم الحداثة
        merged.sort(key=lambda paper: (-(paper.citations or 0), -(paper.year or 0)))
        report.papers = merged[:limit]
        self._write_cache(key, report.papers)
        return report


def ingest_papers(report: SearchReport, corpus) -> int:
    """
    يدفع الأوراق إلى فهرس المعرفة كي تصبح قابلة للاستشهاد.

    المصدر يُسجَّل بصيغة `arxiv:2402.02716` — فيظهر داخل كل مرجع.
    """
    added = 0
    for paper in report.papers:
        body = (
            f"# {paper.title}\n\n"
            f"{paper.citation}\n\n"
            f"الرابط: {paper.url}\n\n"
            f"## الملخص\n\n{paper.abstract or 'لا يتوفر ملخص من المصدر.'}\n"
        )
        chunks = corpus.add_document(
            body, source=f"{paper.source}:{paper.id}", title=paper.title
        )
        added += len(chunks)
    return added


# ================= واجهة CLI =================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.knowledge.sources",
        description="جلب أبحاث علمية مجانية (arXiv / Semantic Scholar / Crossref)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    search = sub.add_parser("search", help="بحث في المصادر المجانية")
    search.add_argument("query", help="عبارة البحث")
    search.add_argument("--limit", type=int, default=5, help=f"حد النتائج (≤{MAX_RESULTS_CAP})")
    search.add_argument("--cache", default=".agent_knowledge/papers_cache.json")
    search.add_argument("--no-cache", action="store_true")
    search.add_argument("--ingest", default=None, help="مسار فهرس JSONL للإضافة إليه")
    search.add_argument("--json", action="store_true", help="إخراج JSON خام")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    finder = PaperSearch(cache_path=args.cache)
    report = finder.search(args.query, limit=args.limit, use_cache=not args.no_cache)

    if args.json:
        print(report.model_dump_json(indent=2))
    else:
        print(report.summary())
        for paper in report.papers:
            print()
            print(paper.render())

    if args.ingest:
        from src.knowledge.corpus import CorpusIndex

        corpus = CorpusIndex(args.ingest)
        added = ingest_papers(report, corpus)
        print(f"\nفهرس المعرفة: {len(corpus)} قطعة ({added} مضافة) — {args.ingest}")

    return 0 if report.papers else 1


if __name__ == "__main__":
    raise SystemExit(main())
