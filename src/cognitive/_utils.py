"""تطبيع وبصمات حتمية مشتركة لطبقة الحالة الإدراكية."""
from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> str:
    """JSON قانوني: ترتيب مفاتيح وفواصل ثابتة ودعم نصوص عربية."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def digest_json(value: Any) -> str:
    return digest_text(canonical_json(value))
