"""سجل أحداث hash-chained ونقاط حفظ ذرّية قابلة للتحقق.

السجل لا يجعل القرص مقاوماً لخصم يملك إعادة كتابة كل شيء؛ ضمانه المحدد هو كشف
أي تعديل جزئي أو حذف/إعادة ترتيب دون إعادة بناء السلسلة والـcheckpoint معاً.
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from src.cognitive._utils import digest_json
from src.cognitive.state import (
    GENESIS_HASH,
    Checkpoint,
    CognitiveState,
    Transition,
)
from src.core.evidence import Evidence

_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class EvidenceIntegrityError(RuntimeError):
    """السجل أو checkpoint لا يطابق بصمته؛ لا يجوز الاستئناف منه بصمت."""


class CognitiveEvent(BaseModel):
    """حدث مترابط؛ بصمته تشمل التسلسل والحمولة وبصمة السابق."""

    sequence: int = Field(..., ge=1)
    kind: str
    ref: str = ""
    payload: dict
    payload_hash: str
    previous_hash: str
    event_hash: str = ""

    def signing_payload(self) -> dict:
        return self.model_dump(mode="json", exclude={"event_hash"})

    def calculated_hash(self) -> str:
        return digest_json(self.signing_payload())


class EvidenceStore:
    """ذاكرة أحداث مع JSONL اختياري؛ القراءة والكتابة صارمتا السلامة."""

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = Path(path) if path else None
        self._events: list[CognitiveEvent] = []
        self._lock = threading.Lock()
        if self.path is not None and self.path.exists():
            self._load()

    @property
    def head_hash(self) -> str:
        return self._events[-1].event_hash if self._events else GENESIS_HASH

    def events(self) -> list[CognitiveEvent]:
        return list(self._events)

    def append(self, kind: str, payload: dict[str, Any], ref: str = "") -> CognitiveEvent:
        """يكتب على القرص أولاً ثم يثبت الحدث في الذاكرة؛ فشل القرص ظاهر."""
        with self._lock:
            event = CognitiveEvent(
                sequence=len(self._events) + 1,
                kind=kind,
                ref=ref,
                payload=payload,
                payload_hash=digest_json(payload),
                previous_hash=self.head_hash,
            )
            event = event.model_copy(update={"event_hash": event.calculated_hash()})
            self._append_line(event)
            self._events.append(event)
            return event

    def append_evidence(self, evidence: Evidence) -> CognitiveEvent:
        sealed = evidence.sealed()
        return self.append(
            "evidence",
            sealed.model_dump(mode="json"),
            ref=sealed.ref or f"evidence-{len(self._events) + 1}",
        )

    def append_transition(self, transition: Transition) -> CognitiveEvent:
        if not transition.verify():
            raise EvidenceIntegrityError("رُفض انتقال لا تطابق صورتاه بصمتيهما.")
        return self.append(
            "transition",
            transition.model_dump(mode="json"),
            ref=transition.stage,
        )

    def verify(self) -> bool:
        previous = GENESIS_HASH
        for index, event in enumerate(self._events, start=1):
            if event.sequence != index or event.previous_hash != previous:
                return False
            if event.payload_hash != digest_json(event.payload):
                return False
            if not event.event_hash or event.event_hash != event.calculated_hash():
                return False
            previous = event.event_hash
        return True

    def require_valid(self) -> None:
        if not self.verify():
            raise EvidenceIntegrityError("سلسلة الأدلة مكسورة أو معدّلة.")

    def _append_line(self, event: CognitiveEvent) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.model_dump(mode="json"), ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _load(self) -> None:
        assert self.path is not None
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
            self._events = [
                CognitiveEvent.model_validate(json.loads(line)) for line in lines if line.strip()
            ]
        except (OSError, ValueError, TypeError, ValidationError) as exc:
            raise EvidenceIntegrityError(f"تعذّر تحميل سجل الأدلة: {self.path}") from exc
        self.require_valid()


class CheckpointStore:
    """ملف مستقل لكل تشغيل، بكتابة ذرّية ورفض أي بصمة غير صالحة."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)

    def save(self, state: CognitiveState, event_head: str) -> Checkpoint:
        self._validate_run_id(state.run_id)
        checkpoint = Checkpoint(state=state, event_head=event_head).sealed()
        self.root.mkdir(parents=True, exist_ok=True)
        destination = self._path(state.run_id)
        temporary = destination.with_suffix(".json.tmp")
        payload = json.dumps(checkpoint.model_dump(mode="json"), ensure_ascii=False, indent=2)
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return checkpoint

    def load(self, run_id: str) -> Checkpoint | None:
        self._validate_run_id(run_id)
        path = self._path(run_id)
        if not path.exists():
            return None
        try:
            checkpoint = Checkpoint.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, ValidationError) as exc:
            raise EvidenceIntegrityError(f"checkpoint غير صالح: {run_id}") from exc
        if checkpoint.state.run_id != run_id or not checkpoint.verify():
            raise EvidenceIntegrityError(f"بصمة checkpoint لا تطابق محتواه: {run_id}")
        return checkpoint

    def _path(self, run_id: str) -> Path:
        return self.root / f"{run_id}.json"

    @staticmethod
    def _validate_run_id(run_id: str) -> None:
        if not _RUN_ID.fullmatch(run_id):
            raise ValueError("run_id غير صالح؛ استخدم حروفاً وأرقاماً و._- فقط.")


__all__ = [
    "CheckpointStore",
    "CognitiveEvent",
    "EvidenceIntegrityError",
    "EvidenceStore",
]
