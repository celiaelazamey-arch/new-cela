"""اختبارات دمج المعرفة والكم في الطبقات العليا (المحرك + CLI)."""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import pytest
from test_engine import HAPPY, ScriptedRouter, make_engine

from src.core.engine import AgentEngine
from src.core.quota import QuotaGuard
from src.knowledge.corpus import CorpusIndex


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    """مساحة عمل معزولة — نسخة محلية كي لا نعتمد على ترتيب الوحدات."""
    (tmp_path / "README.md").write_text("# مشروع\n\nQuotaGuard يحمي الميزانية.\n", encoding="utf-8")
    return tmp_path


DOC = """# ميكانيكا الكم

المحاكي الكمومي يعمل محلياً بلا تكلفة. لا نزعم أي تسريع على البيانات العادية.

# الذاكرة

الذاكرة الاستراتيجية تُخزَّن في ملفات JSONL ولا تحتاج أي خدمة خارجية.
"""


def build_corpus(tmp_path: Path) -> CorpusIndex:
    corpus = CorpusIndex(tmp_path / "corpus.jsonl")
    corpus.add_document(DOC, source="notes.md")
    return corpus


def test_engine_injects_knowledge_context(workspace: Path, tmp_path: Path) -> None:
    corpus = build_corpus(tmp_path)
    engine, _, _, _ = make_engine(workspace, knowledge=corpus)
    result = asyncio.run(engine.run(_request("اشرح الذاكرة الاستراتيجية")))

    assert result.status.value != "blocked"
    assert any(item.kind == "knowledge" for item in result.evidence)
    knowledge = next(item for item in result.evidence if item.kind == "knowledge")
    assert "[notes.md#L" in knowledge.content
    # السياق وصل فعلاً إلى النموذج لا إلى السجل فقط
    assert any("فهرس المعرفة" in prompt for prompt in engine.router.prompts)


def test_engine_without_knowledge_is_unchanged(workspace: Path) -> None:
    engine, _, _, _ = make_engine(workspace)
    result = asyncio.run(engine.run(_request("اشرح الذاكرة")))
    assert not any(item.kind == "knowledge" for item in result.evidence)


def test_knowledge_failure_never_breaks_task(workspace: Path, tmp_path: Path) -> None:
    class Broken:
        def context_for(self, query: str) -> str:
            raise RuntimeError("فهرس تالف")

    engine, _, _, _ = make_engine(workspace, knowledge=Broken())
    result = asyncio.run(engine.run(_request("أي مهمة")))
    assert result.status.value != "blocked"
    assert not any(item.kind == "knowledge" for item in result.evidence)


def test_knowledge_returns_non_string_is_ignored(workspace: Path) -> None:
    class Weird:
        def context_for(self, query: str):
            return 42

    engine, _, _, _ = make_engine(workspace, knowledge=Weird())
    result = asyncio.run(engine.run(_request("مهمة")))
    assert not any(item.kind == "knowledge" for item in result.evidence)


def test_empty_knowledge_context_adds_no_evidence(workspace: Path, tmp_path: Path) -> None:
    corpus = CorpusIndex(tmp_path / "c.jsonl")  # فارغ
    engine, _, _, _ = make_engine(workspace, knowledge=corpus)
    result = asyncio.run(engine.run(_request("شيء لا وجود له")))
    assert not any(item.kind == "knowledge" for item in result.evidence)


def _request(task: str):
    from src.core.task import TaskRequest

    return TaskRequest(task=task)


def _args(**overrides) -> argparse.Namespace:
    base = {
        "knowledge": None,
        "learn": None,
        "learn_limit": 3,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_build_knowledge_returns_none_when_not_requested() -> None:
    from src.main import build_knowledge

    assert build_knowledge(_args()) is None


def test_build_knowledge_loads_existing_corpus(tmp_path: Path) -> None:
    from src.main import build_knowledge

    store = tmp_path / "corpus.jsonl"
    seed = CorpusIndex(store)
    seed.add_document(DOC, source="notes.md")

    corpus = build_knowledge(_args(knowledge=str(store)))
    assert corpus is not None
    assert len(corpus) == len(seed)
    assert corpus.context_for("الذاكرة الاستراتيجية")


def test_build_knowledge_survives_network_failure(tmp_path: Path, monkeypatch) -> None:
    """فشل جلب الأبحاث يجب ألا يمنع تشغيل المهمة."""
    from src.knowledge import sources
    from src.main import build_knowledge

    def boom(self, query: str, limit: int = 5, use_cache: bool = True):
        raise OSError("شبكة مقطوعة")

    monkeypatch.setattr(sources.PaperSearch, "search", boom)
    corpus = build_knowledge(_args(knowledge=str(tmp_path / "c.jsonl"), learn="quantum"))
    assert corpus is not None
    assert len(corpus) == 0


def test_cli_has_knowledge_flags() -> None:
    from src.main import build_parser

    args = build_parser().parse_args(
        ["--task", "x", "--knowledge", "c.jsonl", "--learn", "quantum", "--learn-limit", "5"]
    )
    assert args.knowledge == "c.jsonl"
    assert args.learn == "quantum"
    assert args.learn_limit == 5


def test_engine_accepts_knowledge_kwarg(workspace: Path) -> None:
    """العقد الجديد لا يكسر البناء القديم (توافق خلفي)."""
    engine = AgentEngine(
        ScriptedRouter(HAPPY), registry=None, quota=QuotaGuard(), knowledge=None
    )
    assert engine.knowledge is None
