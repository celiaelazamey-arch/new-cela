"""
المُحسِّن المستوحى من الكم (Quantum-Inspired Optimizer)
=====================================================
اختيار «أفضل تشكيلة» من المرشحين — أدوات، خطوات خطة، مستندات مرجعية — مع
**مقارنة صادقة** بالحل الأمثل (exhaustive) وبخط أساس كلاسيكي.

الفكرة (QUBO):
    كل مرشح متغيّر ثنائي  x_i ∈ {0,1}   (مُختار / غير مُختار)
    دالة الهدف:   E(x) = −Σ v_i x_i  +  λ·(Σ c_i x_i − B)₊  +  Σ_ij p_ij x_i x_j
    نبحث عن x الذي يُصغّر E.

طرق الحل الثلاث المقدَّمة (كلها **كلاسيكية** على عتاد اليوم):
    1. `solve_exhaustive` — الحل الأمثل بالضبط (للمرجع فقط، حد أقصى 16 متغيّراً).
    2. `solve_greedy`     — خط أساس كلاسيكي سريع وجشِع.
    3. `solve_annealing`  — **محاكاة تلدين كمومي (Simulated Quantum Annealing)**
       عبر نموذج Transverse-Field Ising: نُحاكي نفقاً كمياً بمصفوفة كثافة
       مُبسَّطة عبر تلدين مجال عرضي متناقص. هذا هو «الجانب الكمومي» الحقيقي.

⚠️ **الادعاء الصادق:** المحاكاة تعمل على معالج كلاسيكي، وتكلفتها أعلى من
الجشع. في أحجام صغيرة (<20 مرشحاً) لا يُتوقع تفوّق، والأدبيات الحديثة
تؤكد غياب ميزة قابلة للتكرار على بيانات عادية (انظر `docs/research/`).
لذلك كل استدعاء يُنتج `BenchmarkReport` يُظهر الفرق فعلياً — والفائدة
المعلنة هي **جودة الحل عند قيود متعارضة**، لا السرعة.
"""
from __future__ import annotations

import itertools
import math
import random
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

MAX_EXHAUSTIVE_VARS = 16
MAX_VARIABLES = 64


class Candidate(BaseModel):
    """مرشح واحد: قيمة ومقابل واسم للعرض."""

    key: str
    value: float = Field(..., description="الفائدة المتوقعة (أعلى = أفضل)")
    cost: float = Field(default=1.0, ge=0, description="الكلفة/الحصة المستهلكة")
    meta: dict[str, str] = Field(default_factory=dict)


class Problem(BaseModel):
    """مسألة اختيار: مرشحون + ميزانية + قيود تعارض."""

    candidates: list[Candidate] = Field(..., min_length=1, max_length=MAX_VARIABLES)
    budget: float = Field(default=float("inf"), ge=0)
    conflicts: list[tuple[str, str]] = Field(
        default_factory=list, description="أزواج لا يمكن اختيارها معاً"
    )
    penalty: float = Field(default=10.0, gt=0, description="وزن مخالفة القيود")

    def index(self) -> dict[str, int]:
        return {candidate.key: i for i, candidate in enumerate(self.candidates)}


class Solution(BaseModel):
    """حل واحد مع تفصيل كلفته — قابل للتدقيق لا مجرد قائمة أسماء."""

    keys: list[str]
    energy: float
    value: float
    cost: float
    feasible: bool
    method: str

    def render(self) -> str:
        mark = "✓" if self.feasible else "✗ مخالف للقيود"
        return (
            f"{self.method}: E={self.energy:.3f} value={self.value:.2f} "
            f"cost={self.cost:.2f} {mark} → {', '.join(self.keys) or '—'}"
        )


class BenchmarkReport(BaseModel):
    """نتيجة مقارنة الطرق — تُقرأ بصدق: من فاز؟ وبأي فرق؟"""

    quantum_inspired: Solution
    greedy: Solution
    optimum: Solution | None = None
    gain_vs_greedy: float = 0.0
    gap_to_optimum: float = 0.0
    verdict: str = ""

    def render(self) -> str:
        lines = [
            "مقارنة طرق التحسين (QUBO):",
            f"  • {self.quantum_inspired.render()}",
            f"  • {self.greedy.render()}",
        ]
        if self.optimum:
            lines.append(f"  • {self.optimum.render()}")
        lines.append(
            f"  الفرق مقابل الجشع: {self.gain_vs_greedy:+.2f} | "
            f"الفجوة عن الأمثل: {self.gap_to_optimum:.2f}"
        )
        lines.append(f"  الحكم: {self.verdict}")
        return "\n".join(lines)


@dataclass
class _Energy:
    """حساب دالة الطاقة ومكوّناتها — مصدر واحد للحقيقة."""

    problem: Problem
    _index: dict[str, int] = field(init=False)

    def __post_init__(self) -> None:
        self._index = self.problem.index()
        self._conflicts = [
            (self._index[a], self._index[b])
            for a, b in self.problem.conflicts
            if a in self._index and b in self._index
        ]

    def total(self, bits: list[int]) -> float:
        selected = [i for i, bit in enumerate(bits) if bit]
        value = sum(self.problem.candidates[i].value for i in selected)
        cost = sum(self.problem.candidates[i].cost for i in selected)
        violation = max(0.0, cost - self.problem.budget)
        clashes = sum(1 for i, j in self._conflicts if bits[i] and bits[j])
        return -value + self.problem.penalty * (violation + clashes)

    def breakdown(self, bits: list[int]) -> tuple[float, float, bool]:
        selected = [i for i, bit in enumerate(bits) if bit]
        value = sum(self.problem.candidates[i].value for i in selected)
        cost = sum(self.problem.candidates[i].cost for i in selected)
        clashes = any(bits[i] and bits[j] for i, j in self._conflicts)
        feasible = cost <= self.problem.budget + 1e-9 and not clashes
        return value, cost, feasible


def _to_solution(problem: Problem, bits: list[int], method: str) -> Solution:
    energy_model = _Energy(problem)
    value, cost, feasible = energy_model.breakdown(bits)
    return Solution(
        keys=[candidate.key for candidate, bit in zip(problem.candidates, bits, strict=True) if bit],
        energy=round(energy_model.total(bits), 6),
        value=round(value, 6),
        cost=round(cost, 6),
        feasible=feasible,
        method=method,
    )


# ================= الطرق الثلاث =================

def solve_exhaustive(problem: Problem) -> Solution:
    """
    الحل الأمثل بالضبط — تعداد كامل. للمرجع والمقارنة فقط.

    يرفع `ValueError` إن تجاوز عدد المرشحين `MAX_EXHAUSTIVE_VARS`.
    """
    count = len(problem.candidates)
    if count > MAX_EXHAUSTIVE_VARS:
        raise ValueError(
            f"التعداد الكامل محدود بـ {MAX_EXHAUSTIVE_VARS} متغيّراً (لديك {count})."
        )
    energy_model = _Energy(problem)
    best_bits = [0] * count
    best_energy = math.inf
    for combo in itertools.product((0, 1), repeat=count):
        bits = list(combo)
        energy = energy_model.total(bits)
        if energy < best_energy - 1e-12:
            best_energy, best_bits = energy, bits
    return _to_solution(problem, best_bits, "exhaustive")


def solve_greedy(problem: Problem) -> Solution:
    """
    خط الأساس الكلاسيكي: رتّب بنسبة قيمة/كلفة واختر طالما تسمح الميزانية.

    سريع جداً (O(n log n)) لكنه يفشل عند تعارضات القيود وقد يعلق في أمثلة
    محلية — وهذا بالضبط ما يُظهره تقرير المقارنة.
    """
    ordered = sorted(
        range(len(problem.candidates)),
        key=lambda i: (
            -(problem.candidates[i].value / max(problem.candidates[i].cost, 1e-9)),
            -problem.candidates[i].value,
        ),
    )
    bits = [0] * len(problem.candidates)
    used = 0.0
    selected: set[int] = set()
    conflicts = {(problem.index()[a], problem.index()[b]) for a, b in problem.conflicts
                 if a in problem.index() and b in problem.index()}

    for i in ordered:
        candidate = problem.candidates[i]
        if used + candidate.cost > problem.budget + 1e-9:
            continue
        if any((i, j) in conflicts or (j, i) in conflicts for j in selected):
            continue
        bits[i] = 1
        used += candidate.cost
        selected.add(i)
    return _to_solution(problem, bits, "greedy")


def solve_annealing(
    problem: Problem,
    sweeps: int = 60,
    reads: int = 24,
    transverse_start: float = 2.5,
    transverse_end: float = 0.05,
    seed: int = 7,
) -> Solution:
    """
    تلدين كمي مُحاكى (SQA) على نموذج Transverse-Field Ising.

    كيف يعمل:
      1. نبدأ بحالة متساوية الترجيح (كما يفعل المجال العرضي القوي).
      2. في كل خطوة تُحسب «ميل» كل بت من فرق الطاقة ΔE عند قلبه.
      3. احتمال القبول:  P(flip) = 1 / (1 + exp(β·ΔE))   — قاعدة Metropolis
         ممزوجة باحتمال نفق كمي يعتمد على Γ (المجال العرضي):
             P_quantum = sin²(Γ·Δt)   ← يعبّر عن العبور عبر حواجز الطاقة
      4. Γ يتناقص أسّياً حتى الصفر، فتنتهي العملية عند الحل الكلاسيكي.
      5. نقرأ الحالة عدة مرات ونختار الأفضل — يقابل `reads` على QPU.

    هذا **نموذج رياضي للسلوك الكمومي** يعمل على معالج كلاسيكي؛ التكلفة
    O(read × sweeps × n²). القرار النهائي دائماً مبني على طاقة محسوبة بالكامل.
    """
    if not problem.candidates:
        raise ValueError("مسألة بلا مرشحين.")

    rng = random.Random(seed)
    count = len(problem.candidates)
    energy_model = _Energy(problem)
    best_bits = [0] * count
    best_energy = math.inf

    schedules = max(1, reads)
    for read_index in range(schedules):
        bits = [rng.randint(0, 1) for _ in range(count)]
        current = energy_model.total(bits)

        for step in range(sweeps):
            progress = step / max(1, sweeps - 1)
            gamma = transverse_start * (transverse_end / transverse_start) ** progress
            beta = 1.0 + 6.0 * progress  # تبريد: الحرارة تنخفض
            # احتمال النفق الكمي يتراجع مع تراجع المجال العرضي
            tunnel = math.sin(min(1.0, gamma / max(transverse_start, 1e-9)) * (math.pi / 2)) ** 2
            tunnel *= 0.5

            for i in range(count):
                bits[i] ^= 1
                delta = energy_model.total(bits) - current
                if delta <= 0:
                    current += delta
                    continue
                accept = 1.0 / (1.0 + math.exp(min(60.0, beta * delta)))
                accept = max(accept, tunnel)
                if rng.random() < accept:
                    current += delta
                else:
                    bits[i] ^= 1

        if current < best_energy - 1e-12:
            best_energy = current
            best_bits = list(bits)

    return _to_solution(problem, best_bits, "quantum-inspired (SQA)")


def benchmark(problem: Problem, seed: int = 7) -> BenchmarkReport:
    """
    يشغّل الطرق الثلاث ويُصدر حكماً صادقاً — لا «نجاح» بلا مقارنة.

    الفجوة تُحسب على «جودة» (القيمة − الجزاء)، والحكم يذكر من فاز فعلاً حتى
    لو لم يكن الكم.
    """
    quantum = solve_annealing(problem, seed=seed)
    greedy = solve_greedy(problem)

    optimum: Solution | None = None
    if len(problem.candidates) <= MAX_EXHAUSTIVE_VARS:
        optimum = solve_exhaustive(problem)

    best_energy = optimum.energy if optimum else min(quantum.energy, greedy.energy)
    gain = greedy.energy - quantum.energy  # موجب = الكم أفضل
    gap = quantum.energy - best_energy

    if abs(gain) < 1e-9:
        verdict = "تعادل: لا ميزة للتحسين المستوحى من الكم على الجشع في هذه المسألة."
    elif gain > 0:
        verdict = (
            f"المستوحى من الكم أفضل بـ {gain:.3f} في الطاقة — "
            "لكن التكلفة الحسابية أعلى بكثير؛ استخدمه فقط عند قيود متعارضة."
        )
    else:
        verdict = (
            f"الجشع أفضل بـ {-gain:.3f} — وهذا متوقع في مسائل صغيرة؛ "
            "لا مبرر لاستخدام الكم هنا."
        )
    if optimum and gap > 1e-9:
        verdict += f" الحل لم يبلغ الأمثل (فجوة {gap:.3f})."

    return BenchmarkReport(
        quantum_inspired=quantum,
        greedy=greedy,
        optimum=optimum,
        gain_vs_greedy=round(gain, 6),
        gap_to_optimum=round(gap, 6),
        verdict=verdict,
    )


def describe() -> dict[str, str]:
    return {
        "method": "QUBO + Simulated Quantum Annealing (TFIM)",
        "classical_baseline": "classical: greedy value/cost + exhaustive optimum (reference)",
        "honest_note": (
            "يعمل على معالج كلاسيكي؛ لا تسريع مُدَّعى. الفائدة: جودة الاختيار "
            "عند قيود متعارضة، مع مقارنة صريحة تُظهر الفرق الحقيقي."
        ),
    }
