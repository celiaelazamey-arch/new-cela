"""
تحقق شامل (E2E) لطبقة المعرفة + الكم — بلا شبكة وبلا مفاتيح ولا تكلفة.

يُثبت ثلاث حلقات كاملة قابلة للتدقيق:
  1. Drive (بعميل وهمي) → مزامنة تزايدية → فهرس معرفة → مراجع في الطلب.
  2. أبحاث (باستجابة arXiv حقيقية محفوظة) → تحليل → فهرس → استشهاد.
  3. الكم: محاكاة Bell + تحسين QUBO مع مقارنة صادقة بالحل الأمثل.

التشغيل:
    python scripts/e2e_knowledge_quantum.py
الرمز الناتج: 0 = كل الحلقات نجحت، 1 = فشل حلقة واحدة على الأقل.
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.engine import AgentEngine
from src.core.task import TaskRequest
from src.integrations.drive import GOOGLE_DOC, DriveItem, ingest_synced, sync_folder
from src.knowledge.corpus import CorpusIndex
from src.knowledge.sources import ArxivSource, PaperSearch, ingest_papers
from src.quantum import optimizer, simulator

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    CHECKS.append((name, passed, detail))
    print(f"{'✅' if passed else '❌'} {name}" + (f" — {detail}" if detail else ""))


class FakeDrive:
    """عميل Drive وهمي — يمثّل مستنداً واحداً يتغيّر بين مزامنتين."""

    def __init__(self) -> None:
        self.text = (
            "معمارية New33\n\n"
            "النواة المعرفية ثلاثية العقول: Architect و EpistemicCritic و Synthesizer.\n\n"
            "كل حكم يمر عبر HonestEvidenceReceipt بدليل نصي وبصمة SHA-256."
        )
        self.modified = "2026-01-01T00:00:00Z"

    def list_folder(self, folder_id: str) -> list[DriveItem]:
        return [DriveItem(id="doc00001", name="New33-arch", mime_type=GOOGLE_DOC,
                          modified_time=self.modified)]

    def read_text(self, item: DriveItem, max_bytes: int = 2_000_000) -> str:
        return self.text


class FakeRouter:
    """موجّه وهمي يسجّل الطلبات كي نُثبت أن المراجع وصلت للنموذج."""

    def __init__(self) -> None:
        from src.config import Settings
        from src.core.model_api import ModelInfo
        from src.core.quota import TrustLevel

        self.prompts: list[str] = []
        self.settings = Settings(_env_file=None)
        self._info = ModelInfo(provider="fake", model="fake-1", trust=TrustLevel.VERIFIED_FREE)

    def available(self):
        return [self._info]

    async def execute_reasoning_step(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return "FINAL ANSWER: تمت الإجابة بالاستناد إلى المصادر المرفقة."

    async def execute_structured_step(self, prompt, output_format):
        return None


def loop_drive() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        client = FakeDrive()
        corpus = CorpusIndex(root / "corpus.jsonl")

        first = sync_folder(client, "folder-1", root / "drive")
        added = ingest_synced(first, corpus)
        check("Drive: مزامنة أولى + فهرسة", len(first.added) == 1 and added > 0, first.summary())

        client.text += "\n\nإضافة: التكلفة صفرية والخصوصية محفوظة."
        client.modified = "2026-02-02T00:00:00Z"
        second = sync_folder(client, "folder-1", root / "drive")
        check("Drive: مزامنة تزايدية تكتشف التغيير فقط",
              len(second.updated) == 1 and second.unchanged == 0, second.summary())

        block = corpus.context_for("ما هي النواة المعرفية ثلاثية العقول؟")
        check("المعرفة: مراجع بأرقام أسطر", "[drive:" in block and "#L" in block,
              block.splitlines()[0] if block else "لا نتائج")

        router = FakeRouter()
        engine = AgentEngine(router, knowledge=corpus)
        result = asyncio.run(engine.run(TaskRequest(task="لخّص معمارية New33 بدليل")))
        injected = any("فهرس المعرفة" in prompt for prompt in router.prompts)
        check("المحرك: سياق المعرفة يصل إلى الprompt",
              injected and any(e.kind == "knowledge" for e in result.evidence),
              result.summary())


ARXIV_FIXTURE = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2609.31937v1</id>
    <published>2026-09-01T00:00:00Z</published>
    <title>Verification as an Architectural Layer in LLM Agents</title>
    <summary>We argue verification must be a layer, not a step.</summary>
    <author><name>A. Researcher</name></author>
  </entry>
</feed>
"""


def loop_papers() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        finder = PaperSearch(
            sources=[ArxivSource(transport=lambda url: ARXIV_FIXTURE)],
            cache_path=root / "cache.json",
        )
        report = finder.search("verification agent architecture", limit=3)
        check("الأبحاث: جلب وتحليل", len(report.papers) == 1, report.summary())

        cached = finder.search("verification agent architecture", limit=3)
        check("الأبحاث: الكاش يمنع الطلب المتكرر", cached.from_cache, cached.summary())

        corpus = CorpusIndex(root / "papers.jsonl")
        added = ingest_papers(report, corpus)
        hit = corpus.search("verification architectural layer")
        check("الأبحاث: تصبح مرجعاً قابلاً للاستشهاد",
              added > 0 and bool(hit) and "[arxiv:2609.31937v1#L" in hit[0].render(),
              hit[0].chunk.citation if hit else "لا نتائج")


def loop_quantum() -> None:
    info = simulator.describe()
    check("الكم: وصف صادق بلا مزاعم تسريع", "لا تسريع" in info["honest_note"], info["backend"])

    if info["available"]:
        bell = simulator.bell_state(qubits=2, shots=2000, seed=13)
        correlation = set(bell.counts) <= {"00", "11"}
        p00 = bell.probabilities.get("00", 0)
        check("الكم: حالة Bell مترابطة", correlation and 0.4 <= p00 <= 0.6,
              f"{bell.counts} | depth={bell.depth}")
    else:
        check("الكم: المحاكي غير مثبت (تخطّي صادق)", True,
              "pip install -r requirements-quantum.txt")

    problem = optimizer.Problem(
        candidates=[
            optimizer.Candidate(key="tool:arxiv", value=9, cost=4),
            optimizer.Candidate(key="tool:drive", value=8, cost=3),
            optimizer.Candidate(key="tool:shell", value=5, cost=2),
            optimizer.Candidate(key="tool:browser", value=7, cost=5),
        ],
        budget=7,
        conflicts=[("tool:arxiv", "tool:browser")],
    )
    report = optimizer.benchmark(problem, seed=5)
    print("\n" + report.render() + "\n")
    check("الكم: مقارنة صادقة بالجشع والأمثل",
          report.verdict != "" and report.optimum is not None,
          f"gain={report.gain_vs_greedy:+.3f} gap={report.gap_to_optimum:.3f}")


def main() -> int:
    print("===== تحقق شامل: المعرفة + الأبحاث + الكم =====\n")
    loop_drive()
    print()
    loop_papers()
    print()
    loop_quantum()

    passed = sum(1 for _, ok, _ in CHECKS if ok)
    total = len(CHECKS)
    print(f"\n===== {passed}/{total} نجحت =====")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
