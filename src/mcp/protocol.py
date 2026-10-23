"""
بروتوكول MCP — الطبقة النقية (بلا عمليات، بلا شبكة)
==================================================
MCP (Model Context Protocol) يعرّف خوادم أدوات تتحدث JSON-RPC 2.0 عبر stdio.
هنا فقط: بناء الرسائل، تحليلها، وأخطاء صريحة — بلا تنفيذ أي شيء.

لماذا نُنفّذه بأنفسنا بدل حزمة جاهزة؟
  • صفر اعتماديات إضافية في نظام يلتزم بالتكلفة الصفرية والبساطة.
  • سطح صغير قابل للاختبار بالكامل (وكل رفض له نوع خطأ معروف).
  • نتحكم في تسريب البيئة: لا نمرّر مفاتيح API إلى خادم خارجي بلا قرار صريح.

الحد المعلن: نُنفّذ مجموعة فرعية من MCP —
`initialize` / `notifications/initialized` / `tools/list` / `tools/call`.
لا موارد (resources)، لا مطالبات (prompts)، لا اشتراكات (subscriptions).
أي رسالة إشعار أخرى تُتجاهل بأمان ولا تُسقط الجلسة.
"""
from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field

PROTOCOL_VERSION = "2024-11-05"
CLIENT_INFO = {"name": "new-cela", "version": "1.0"}
MAX_TOOLS = 50

# أسماء الأدوات في MCP: حروف/أرقام/شرطة/سطر سفلي، بحد أقصى 64
_TOOL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


class McpError(RuntimeError):
    """خطأ MCP يحمل رمزاً من البروتوكول — لا رسالة مبهمة."""

    def __init__(self, message: str, code: int | None = None, data: object = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data

    def __str__(self) -> str:
        if self.code is None:
            return super().__str__()
        return f"[{self.code}] {super().__str__()}"


class McpProtocolError(McpError):
    """رسالة غير متوافقة مع JSON-RPC (JSON تالف، شكل خاطئ)."""


class McpTimeout(McpError):
    """لم يردّ الخادم ضمن المهلة — لا ننتظر إلى الأبد."""


class McpServerClosed(McpError):
    """أُغلق stdio الخادم (انهيار أو إنهاء) — مع ذيل stderr للتشخيص."""


class McpTool(BaseModel):
    """أداة يعلنها الخادم — الاسم والوصف والمخطط كما وردت."""

    name: str
    description: str = ""
    input_schema: dict = Field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


def valid_tool_name(name: str) -> bool:
    return bool(_TOOL_NAME.match(name or ""))


# ================= الرسائل =================

def build_request(request_id: int, method: str, params: dict | None = None) -> dict:
    """يبني طلب JSON-RPC 2.0."""
    payload: dict = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        payload["params"] = params
    return payload


def build_notification(method: str, params: dict | None = None) -> dict:
    """إشعار JSON-RPC — بلا `id`، فلا ردّ متوقع."""
    payload: dict = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        payload["params"] = params
    return payload


def initialise_params() -> dict:
    """وسائط `initialize` — القدرات فارغة عمداً: لا نعلن ما لا ننفّذه."""
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": CLIENT_INFO,
    }


def parse_message(line: str) -> dict:
    """يحلّل سطراً واحداً من الخادم ويرفض ما ليس JSON-RPC صالحاً."""
    text = (line or "").strip()
    if not text:
        raise McpProtocolError("سطر فارغ من الخادم.")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise McpProtocolError(f"ردّ غير صالح (JSON): {exc.msg}") from exc
    if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
        raise McpProtocolError("ردّ ليس JSON-RPC 2.0.")
    return payload


def error_from(payload: dict) -> McpError:
    """يحوّل كائن `error` إلى استثناء نوعي."""
    code = payload.get("code")
    message = payload.get("message") or "خطأ غير معروف من الخادم"
    return McpError(str(message), code=code if isinstance(code, int) else None,
                    data=payload.get("data"))


# ================= النتائج =================

def parse_tools(result: dict) -> list[McpTool]:
    """يستخرج الأدوات من نتيجة `tools/list` ويتجاهل ما لا يصلح."""
    raw = result.get("tools") or []
    tools: list[McpTool] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        if not valid_tool_name(name):
            continue
        schema = item.get("inputSchema")
        tools.append(
            McpTool(
                name=name,
                description=str(item.get("description") or ""),
                input_schema=schema if isinstance(schema, dict) else {},
            )
        )
        if len(tools) >= MAX_TOOLS:
            break
    return tools


def text_from_result(result: dict) -> str:
    """
    يجمع محتوى النتيجة النصي.

    العناصر غير النصية (صور/موارد) تُوصف ولا تُحلَّل — نعلن الحد بدل تخمينه.
    """
    content = result.get("content") or []
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "text":
            parts.append(str(item.get("text") or ""))
        elif kind == "resource":
            resource = item.get("resource") or {}
            parts.append(f"[مورد: {resource.get('uri', '?')}]")
        elif kind:
            parts.append(f"[محتوى {kind} غير معروض]")
    text = "\n".join(part for part in parts if part).strip()
    if not text and result.get("structuredContent"):
        text = json.dumps(result["structuredContent"], ensure_ascii=False)
    return text


def is_error_result(result: dict) -> bool:
    return bool(result.get("isError"))
