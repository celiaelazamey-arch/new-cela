"""
جسر MCP → سجل الأدوات
======================
أدوات الخادم البعيد تصبح أدوات نظامية كاملة الحقوق **والواجبات**:

  • تُسجَّل باسم مُنَطَّق `mcp__{server}__{tool}` فلا تتصادم مع الأدوات المحلية.
  • تمرّ بنفس المسار: تحقق وسائط ← سياسة/موافقة ← ميزانية ← تنفيذ ← تدقيق.
  • خطرها الافتراضي `execute`: لا نعرف ما تفعله الأداة البعيدة، فالأصل هو الحذر.
  • نطاقها `mcp.call`، وهو نطاق يُسأل عنه دائماً ( في ALWAYS_ASK_SCOPES).
  • النتيجة محدودة الحجم كي لا تُغرق سياق النموذج.

ما لا نفعله: لا نثق بمخطط الأداة كمصدر صلاحية، ولا نُمرّر إليها مسارات أو
أسراراً من عندنا. الوسائط تُمرَّر كما أعطاها النموذج/المستخدم.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, create_model

from src.core.policy import ToolRisk
from src.mcp.client import StdioMcpClient
from src.mcp.protocol import McpError, McpTool

logger = logging.getLogger("new-cela.mcp.bridge")

MCP_SCOPE = "mcp.call"
MAX_REMOTE_OUTPUT = 20_000
_UNSAFE = re.compile(r"[^A-Za-z0-9_]")

_ALLOWED_NAMESPACES = (
    "src.mcp.api",  # مساحة مستقبلية للتوسعة
)


def namespaced(server: str, tool: str) -> str:
    """اسم نظامي آمن: `mcp__server__tool` بحروف إنجليزية فقط."""
    clean_server = _UNSAFE.sub("_", server)
    clean_tool = _UNSAFE.sub("_", tool)
    return f"mcp__{clean_server}__{clean_tool}"


def _python_type(schema_type: str) -> Any:
    return {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "array": list,
        "object": dict,
    }.get(schema_type, Any)


def params_model(tool: McpTool) -> type[BaseModel]:
    """
    يبني نموذج وسائط من `inputSchema`.

    الحقول غير المعروفة تُرفض (extra='forbid') كما في بقية الأدوات؛
    وإن كان المخطط فارغاً نقبل كائن `arguments` حراً بدل تخمين شكل.
    """
    properties = (tool.input_schema or {}).get("properties") or {}
    required = set((tool.input_schema or {}).get("required") or [])

    fields: dict[str, Any] = {}
    for name, spec in properties.items():
        if not isinstance(name, str) or not name.isidentifier():
            continue
        kind = _python_type(str((spec or {}).get("type", "")))
        description = str((spec or {}).get("description") or "")
        if name in required:
            fields[name] = (kind, Field(..., description=description))
        else:
            fields[name] = (kind | None, Field(default=None, description=description))

    if not fields:
        fields["arguments"] = (
            dict,
            Field(default_factory=dict, description="وسائط حرة كما يتوقعها الخادم"),
        )

    model_name = f"McpParams_{_UNSAFE.sub('_', tool.name)[:40]}"
    return create_model(
        model_name, __config__=ConfigDict(extra="forbid"), **fields
    )


class McpBridge:
    """
    يربط خادماً واحداً بسجل أدوات واحد.

    الجسر لا يبدأ الخادم ولا يوقفه: من يملك دورة الحياة هو المستدعي،
    فيبقى الاتصال تحت سيطرة صريحة لا مخفية داخل تسجيل الأدوات.
    """

    def __init__(
        self,
        client: StdioMcpClient,
        server: str,
        tools: list[McpTool] | None = None,
        risk: ToolRisk = ToolRisk.EXECUTE,
        scope: str = MCP_SCOPE,
    ) -> None:
        self.client = client
        self.server = server
        self.tools = list(tools or [])
        self.risk = risk
        self.scope = scope

    # ---------------- التسجيل ----------------
    def register_into(self, registry) -> list[str]:
        """يسجّل كل أداة (أو أدوات مختارة) ويعيد أسماءها النظامية."""
        registered: list[str] = []
        for tool in self.tools:
            name = namespaced(self.server, tool.name)
            if name in registry:
                logger.warning("تخطّي أداة MCP مكرّرة: %s", name)
                continue
            description = (
                f"[MCP:{self.server}] {tool.description}".strip()
                or f"أداة من خادم MCP «{self.server}»"
            )
            from src.tools.registry import ToolSpec

            registry.register(
                ToolSpec(
                    name=name,
                    description=description[:400],
                    params=params_model(tool),
                    handler=self._handler(tool.name),
                    risk=self.risk,
                    scope=self.scope,
                )
            )
            registered.append(name)
        return registered

    # ---------------- التنفيذ ----------------
    def _handler(self, remote_tool: str):
        """ينتج معالجاً غير متزامن ينفّذ الأداة البعيدة في خيط منفصل."""

        async def handler(params: BaseModel, ctx) -> str:
            payload = params.model_dump(exclude_none=True)
            arguments = payload.get("arguments") if list(payload) == ["arguments"] else payload

            timeout = getattr(ctx, "timeout", None) or self.client.timeout
            try:
                text = await asyncio.to_thread(
                    self.client.call_tool, remote_tool, dict(arguments or {}), timeout
                )
            except McpError as exc:
                raise RuntimeError(f"خطأ من خادم «{self.server}»: {exc}") from exc

            if len(text) > MAX_REMOTE_OUTPUT:
                text = text[:MAX_REMOTE_OUTPUT] + "\n…[اقتُطع الناتج البعيد]"
            return text or "(أعاد الخادم نتيجة فارغة)"

        return handler

    def describe(self) -> dict:
        return {
            "server": self.server,
            "scope": self.scope,
            "risk": self.risk.value,
            "tools": [namespaced(self.server, tool.name) for tool in self.tools],
        }
