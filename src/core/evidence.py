"""دليل قابل للتتبع، منفصل عن عقود المهمة لتفادي الاعتماد الدائري."""
from __future__ import annotations

import hashlib

from pydantic import BaseModel


class Evidence(BaseModel):
    """دليل واحد: نص ومصدر وحالة، وبصمة اختيارية للمحتوى أو الأثر."""

    kind: str
    ref: str = ""
    content: str = ""
    ok: bool = True
    source: str = ""
    sha256: str = ""

    def sealed(self, source: str = "") -> Evidence:
        """يعيد نسخة ذات مصدر وبصمة محتوى، دون الكتابة فوق بصمة أثر موجودة."""
        return self.model_copy(
            update={
                "source": self.source or source or self.ref,
                "sha256": self.sha256 or hashlib.sha256(self.content.encode("utf-8")).hexdigest(),
            }
        )

    def content_digest(self) -> str:
        """بصمة النص نفسه؛ تختلف عن بصمة ملف عندما يكون الدليل من نوع artifact."""
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()
