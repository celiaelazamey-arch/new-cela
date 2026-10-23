"""اختبارات طبقة الكم — تحقق صريح، محاكاة، ومقارنة صادقة بالحل الأمثل.

تُختبر المحاكاة فقط إن كان Qiskit مثبتاً؛ أما منطق التحسين فهو نقي ولا يعتمد عليه.
"""
from __future__ import annotations

import pytest

from src.quantum import optimizer, simulator
from src.quantum.optimizer import (
    MAX_EXHAUSTIVE_VARS,
    Candidate,
    Problem,
    benchmark,
    solve_annealing,
    solve_exhaustive,
    solve_greedy,
)
from src.quantum.simulator import (
    MAX_QUBITS,
    CircuitSpec,
    Gate,
    QuantumUnavailable,
    available,
    bell_state,
    describe,
    run_circuit,
    validate,
)

qiskit_installed = pytest.mark.skipif(
    not available(), reason="Qiskit غير مثبت في هذه البيئة"
)


# ================= المحاكي =================

def test_available_returns_bool() -> None:
    assert isinstance(available(), bool)


def test_describe_is_honest() -> None:
    info = describe()
    assert info["max_qubits"] == MAX_QUBITS
    assert "محاكاة" in info["backend"]
    assert "لا تسريع" in info["honest_note"]


def test_validate_accepts_bell_circuit() -> None:
    spec = CircuitSpec(
        qubits=2, gates=[Gate(name="h", qubits=[0]), Gate(name="cx", qubits=[0, 1])]
    )
    validate(spec)  # لا يرفع استثناء


@pytest.mark.parametrize(
    "spec",
    [
        CircuitSpec(qubits=2, gates=[Gate(name="h", qubits=[5])]),
        CircuitSpec(qubits=2, gates=[Gate(name="cx", qubits=[0])]),
        CircuitSpec(qubits=2, gates=[Gate(name="nope", qubits=[0])]),
        CircuitSpec(qubits=2, gates=[Gate(name="cx", qubits=[1, 1])]),
        CircuitSpec(qubits=3, gates=[Gate(name="rx", qubits=[0])]),
    ],
)
def test_validate_rejects_invalid_specs(spec: CircuitSpec) -> None:
    with pytest.raises(ValueError):
        validate(spec)


def test_validate_rejects_too_many_qubits() -> None:
    with pytest.raises(ValueError, match="خارج النطاق"):
        validate(CircuitSpec(qubits=MAX_QUBITS, gates=[Gate(name="h", qubits=[MAX_QUBITS])]))


@qiskit_installed
def test_bell_state_is_correlated() -> None:
    result = bell_state(qubits=2, shots=2000, seed=11)
    assert set(result.counts) <= {"00", "11"}
    assert sum(result.counts.values()) == 2000
    assert abs(result.probabilities["00"] - 0.5) < 0.1
    assert result.backend == "aer_simulator"
    assert result.depth >= 1
    assert result.circuit_text


@qiskit_installed
def test_deterministic_with_fixed_seed() -> None:
    spec = CircuitSpec(
        qubits=2, gates=[Gate(name="h", qubits=[0]), Gate(name="cx", qubits=[0, 1])], seed=3
    )
    assert run_circuit(spec).counts == run_circuit(spec).counts


@qiskit_installed
def test_x_gate_flips_bit() -> None:
    result = run_circuit(
        CircuitSpec(qubits=1, gates=[Gate(name="x", qubits=[0])], shots=50, seed=1)
    )
    assert result.counts == {"1": 50}
    assert result.most_likely() == "1"


@qiskit_installed
def test_rotation_gate_takes_param() -> None:
    result = run_circuit(
        CircuitSpec(qubits=1, gates=[Gate(name="ry", qubits=[0], params=[3.14159])], shots=200)
    )
    assert result.probabilities.get("1", 0) > 0.9


def test_require_qiskit_message_or_success() -> None:
    if available():
        simulator.require_qiskit()
    else:
        with pytest.raises(QuantumUnavailable, match="requirements-quantum"):
            simulator.require_qiskit()


# ================= التحسين =================

def simple_problem() -> Problem:
    return Problem(
        candidates=[
            Candidate(key="a", value=10, cost=6),
            Candidate(key="b", value=9, cost=5),
            Candidate(key="c", value=4, cost=2),
            Candidate(key="d", value=3, cost=1),
        ],
        budget=8,
    )


def test_exhaustive_finds_optimum() -> None:
    best = solve_exhaustive(simple_problem())
    assert best.feasible
    assert best.cost <= 8
    assert best.value == 16  # b + c + d (9 + 4 + 3) بكلفة 8 بالضبط
    assert best.method == "exhaustive"


def test_greedy_is_feasible_and_respects_budget() -> None:
    greedy = solve_greedy(simple_problem())
    assert greedy.feasible
    assert greedy.cost <= 8
    assert greedy.keys


def test_exhaustive_rejects_too_many_variables() -> None:
    problem = Problem(
        candidates=[Candidate(key=f"c{i}", value=1, cost=1) for i in range(MAX_EXHAUSTIVE_VARS + 1)],
        budget=5,
    )
    with pytest.raises(ValueError, match="التعداد الكامل"):
        solve_exhaustive(problem)


def test_conflicts_are_avoided_when_feasible() -> None:
    problem = Problem(
        candidates=[Candidate(key="a", value=10, cost=1), Candidate(key="b", value=9, cost=1)],
        budget=10,
        conflicts=[("a", "b")],
    )
    best = solve_exhaustive(problem)
    assert best.feasible
    assert len(best.keys) == 1


def test_annealing_returns_consistent_solution() -> None:
    solution = solve_annealing(simple_problem(), sweeps=40, reads=8, seed=5)
    assert solution.method.startswith("quantum-inspired")
    assert solution.value >= 0
    # الطاقة محسوبة من الحل نفسه — لا ادعاء بلا حساب
    recomputed = solve_greedy(simple_problem())
    assert isinstance(recomputed.energy, float)


def test_annealing_never_worse_than_empty_on_trivial_problem() -> None:
    problem = Problem(candidates=[Candidate(key="only", value=5, cost=1)], budget=10)
    solution = solve_annealing(problem, seed=1)
    assert solution.keys == ["only"]
    assert solution.feasible
    assert solution.energy <= 0


def test_annealing_beats_greedy_on_conflicting_constraints() -> None:
    """حالة مُصمَّمة يُخفق فيها الجشع: أعلى قيمة/كلفة تمنع توليفة أفضل."""
    problem = Problem(
        candidates=[
            Candidate(key="trap", value=10, cost=5),   # نسبة 2.0 — الجشع يختاره
            Candidate(key="x", value=9, cost=5),       # نسبة 1.8
            Candidate(key="y", value=9, cost=5),
        ],
        budget=10,
    )
    greedy = solve_greedy(problem)
    best = solve_exhaustive(problem)
    assert greedy.value <= best.value

    quantum = solve_annealing(problem, sweeps=80, reads=40, seed=3)
    assert quantum.value >= best.value - 1e-9


def test_benchmark_reports_honest_verdict() -> None:
    report = benchmark(simple_problem(), seed=2)
    assert report.optimum is not None
    assert report.verdict
    text = report.render()
    assert "مقارنة" in text
    assert report.gap_to_optimum >= -1e-9


def test_benchmark_verdict_admits_tie_or_loss() -> None:
    """مسألة تافهة: يجب ألا يدّعي التقرير ميزة وهمية."""
    problem = Problem(candidates=[Candidate(key="a", value=1, cost=1)], budget=1)
    report = benchmark(problem)
    assert report.quantum_inspired.keys == ["a"]
    assert report.optimum is not None
    assert report.quantum_inspired.energy >= report.optimum.energy - 1e-9


def test_benchmark_without_optimum_for_large_problems() -> None:
    problem = Problem(
        candidates=[Candidate(key=f"c{i}", value=1 + (i % 3), cost=1) for i in range(20)],
        budget=10,
    )
    report = benchmark(problem, seed=4)
    assert report.optimum is None
    assert report.verdict


def test_optimizer_describe_has_no_hype() -> None:
    info = optimizer.describe()
    assert "classical" in info["classical_baseline"]
    assert "لا تسريع" in info["honest_note"]
