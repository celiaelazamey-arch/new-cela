"""
محاكي الدوائر الكمومية (Quantum Circuit Simulator)
==================================================
تشغيل دوائر كمومية **محلياً وبلا تكلفة** عبر Qiskit Aer:

  • لا حساب سحابي، لا مفتاح API، لا حصة — محاكاة كلاسيكية على جهازك.
  • لغة صغيرة مُتحقَّقة (DSL) بدل تنفيذ كود عشوائي: بوابات محددة + حدود صلبة.
  • بذرة عشوائية ثابتة ⇒ نتائج قابلة للتكرار والاختبار.

⚠️ **حدود صادقة:** هذا **محاكي** لا حاسوب كمي. تكلفة المحاكاة تنمو أسّياً
(2^n)، لذلك الحد الأعلى هنا 12 كيوبت. ولا تُوجد أي مزاعم تسريع: التسريع
الكمي الحقيقي يحتاج عتاداً مصحَّح الأخطاء غير متوفر اليوم (انظر
`docs/research/`). القيمة العملية الآن: تعليم، تحقّق من الدوائر، وتجارب
خوارزميات قبل أي تشغيل على عتاد حقيقي.

الاعتماديات: `pip install -r requirements-quantum.txt` (qiskit + qiskit-aer).
بدونها يظل النظام يعمل، وتُعيد الدوال رسالة إرشادية واضحة.
"""
from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger("new-cela.quantum")

MAX_QUBITS = 12
MAX_SHOTS = 20_000
MAX_GATES = 400

# اسم البوابة ⇒ (عدد الكيوبتات، عدد المعاملات)
GATE_SPECS: dict[str, tuple[int, int]] = {
    "h": (1, 0),
    "x": (1, 0),
    "y": (1, 0),
    "z": (1, 0),
    "s": (1, 0),
    "sdg": (1, 0),
    "t": (1, 0),
    "tdg": (1, 0),
    "rx": (1, 1),
    "ry": (1, 1),
    "rz": (1, 1),
    "p": (1, 1),
    "cx": (2, 0),
    "cy": (2, 0),
    "cz": (2, 0),
    "swap": (2, 0),
    "ccx": (3, 0),
}


class Gate(BaseModel):
    """بوابة واحدة — كيوبتات صريحة ومعاملات اختيارية."""

    name: str
    qubits: list[int] = Field(..., min_length=1, max_length=3)
    params: list[float] = Field(default_factory=list)


class CircuitSpec(BaseModel):
    """دائرة كمومية كاملة قابلة للتحقق قبل التنفيذ."""

    qubits: int = Field(..., ge=1, le=MAX_QUBITS)
    gates: list[Gate] = Field(default_factory=list, max_length=MAX_GATES)
    shots: int = Field(default=1024, ge=1, le=MAX_SHOTS)
    seed: int = Field(default=7, ge=0, le=2**31 - 1)


class CircuitResult(BaseModel):
    """نتائج القياس — والقيم الإضافية مفيدة للتحقق والتدقيق."""

    counts: dict[str, int] = Field(default_factory=dict)
    probabilities: dict[str, float] = Field(default_factory=dict)
    shots: int = 0
    qubits: int = 0
    depth: int = 0
    backend: str = "aer_simulator"
    circuit_text: str = ""

    def most_likely(self) -> str:
        return max(self.counts, key=self.counts.get) if self.counts else ""


class QuantumUnavailable(RuntimeError):
    """Qiskit غير مثبت — رسالة إرشادية بدل ImportError خام."""


def available() -> bool:
    """هل محاكي Qiskit متاح في هذه البيئة؟"""
    try:
        import qiskit  # noqa: F401
        import qiskit_aer  # noqa: F401

        return True
    except ImportError:
        return False


def require_qiskit() -> None:
    if not available():
        raise QuantumUnavailable(
            "محاكي الكم غير مثبت. نفّذ: pip install -r requirements-quantum.txt"
        )


def validate(spec: CircuitSpec) -> None:
    """
    تحقق صريح قبل أي تنفيذ — يرفع `ValueError` برسالة عربية دقيقة.

    الأخطاء هنا تُكتشف بلا استهلاك وقت محاكاة، وهي أرخص من تشغيل دائرة خاطئة.
    """
    if spec.qubits > MAX_QUBITS:
        raise ValueError(f"عدد الكيوبتات {spec.qubits} يتجاوز الحد {MAX_QUBITS}.")

    for index, gate in enumerate(spec.gates):
        name = gate.name.lower()
        if name not in GATE_SPECS:
            raise ValueError(f"بوابة غير مدعومة: {gate.name} (الموضع {index}).")

        arity, param_count = GATE_SPECS[name]
        if len(gate.qubits) != arity:
            raise ValueError(f"البوابة {name} تحتاج {arity} كيوبت، وأُعطي {len(gate.qubits)}.")
        if len(set(gate.qubits)) != len(gate.qubits):
            raise ValueError(f"البوابة {name} تستخدم نفس الكيوبت مرتين.")
        if any(qubit < 0 or qubit >= spec.qubits for qubit in gate.qubits):
            raise ValueError(f"رقم كيوبت خارج النطاق في البوابة {name} (الموضع {index}).")
        if len(gate.params) != param_count:
            raise ValueError(f"البوابة {name} تحتاج {param_count} معاملاً.")


def build_circuit(spec: CircuitSpec):
    """يبني دائرة Qiskit من المواصفة المُتحقَّقة."""
    require_qiskit()
    validate(spec)

    from qiskit import QuantumCircuit

    circuit = QuantumCircuit(spec.qubits, spec.qubits)
    for gate in spec.gates:
        name = gate.name.lower()
        qubits = gate.qubits
        match name:
            case "h" | "x" | "y" | "z" | "s" | "sdg" | "t" | "tdg":
                getattr(circuit, name)(qubits[0])
            case "rx" | "ry" | "rz" | "p":
                getattr(circuit, name)(gate.params[0], qubits[0])
            case "cx" | "cy" | "cz" | "swap":
                getattr(circuit, name)(qubits[0], qubits[1])
            case "ccx":
                circuit.ccx(qubits[0], qubits[1], qubits[2])
    circuit.measure(range(spec.qubits), range(spec.qubits))
    return circuit


def run_circuit(spec: CircuitSpec) -> CircuitResult:
    """
    ينفّذ الدائرة على `AerSimulator` ويعيد التوزيعات.

    يعيد دائماً نتيجة قابلة للتسلسل — لا كائنات Qiskit للخارج.
    """
    require_qiskit()
    circuit = build_circuit(spec)

    from qiskit_aer import AerSimulator

    backend = AerSimulator()
    job = backend.run(circuit, shots=spec.shots, seed_simulator=spec.seed)
    counts = job.result().get_counts()

    total = sum(counts.values()) or 1
    return CircuitResult(
        counts={key: int(value) for key, value in counts.items()},
        probabilities={key: round(value / total, 4) for key, value in counts.items()},
        shots=spec.shots,
        qubits=spec.qubits,
        depth=circuit.depth(),
        backend="aer_simulator",
        circuit_text=str(circuit.draw(output="text", fold=100)),
    )


def bell_state(qubits: int = 2, shots: int = 1024, seed: int = 7) -> CircuitResult:
    """
    حالة Bell معمَّمة — تحقّق سريع من سلامة المحاكي.

    لـ 2 كيوبت: التوزيع المتوقع ≈ {00: 50%, 11: 50%}.
    """
    gates = [Gate(name="h", qubits=[0])]
    gates += [Gate(name="cx", qubits=[q, q + 1]) for q in range(qubits - 1)]
    return run_circuit(CircuitSpec(qubits=qubits, gates=gates, shots=shots, seed=seed))


def describe() -> dict[str, Any]:
    """وصف القدرة — يُعرض في الواجهة ويُمرَّر للنموذج عند الحاجة."""
    return {
        "available": available(),
        "max_qubits": MAX_QUBITS,
        "max_shots": MAX_SHOTS,
        "gates": sorted(GATE_SPECS),
        "backend": "qiskit_aer.AerSimulator (محاكاة كلاسيكية محلية، بلا تكلفة)",
        "honest_note": (
            "محاكي لا حاسوب كمي؛ لا تسريع مُدَّعى. التكلفة تنمو أسّياً مع عدد الكيوبتات."
        ),
    }
