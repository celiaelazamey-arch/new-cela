"""خادم MCP وهمي حقيقي (عملية فرعية) لاختبار العميل عبر stdio.

ليس محاكاة داخلية: هذا خادم يعمل فعلياً ويستخدم JSON-RPC سطراً بسطر،
فالاختبار يغطي الأنابيب والمصافحة والمهلة والانهيار كما تحدث في الواقع.
"""
from __future__ import annotations

import json
import os
import sys
import time

TOOLS = [
    {
        "name": "echo",
        "description": "يعيد النص الذي يُمرَّر إليه",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "نص"},
                "times": {"type": "integer", "description": "تكرار"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "env_keys",
        "description": "يسرد أسماء متغيرات البيئة المرئية للخادم",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "boom",
        "description": "ينتهي بخطأ معلن (isError)",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "slow",
        "description": "يتأخر قبل الردّ (لاختبار المهلة)",
        "inputSchema": {"type": "object", "properties": {"seconds": {"type": "number"}}},
    },
]


def send(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def text_result(text: str, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def handle(message: dict) -> None:
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params") or {}

    if method == "initialize":
        send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fake-mcp", "version": "0.1.0"},
                },
            }
        )
        return

    if method == "notifications/initialized":
        return  # إشعار: بلا ردّ

    if method == "tools/list":
        send({"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}})
        return

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name == "echo":
            times = int(arguments.get("times") or 1)
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": text_result(f"echo: {arguments.get('text', '')}" * times),
                }
            )
            return
        if name == "env_keys":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": text_result(",".join(sorted(os.environ))),
                }
            )
            return
        if name == "boom":
            send({"jsonrpc": "2.0", "id": request_id, "result": text_result("فشل مقصود", True)})
            return
        if name == "slow":
            time.sleep(float(arguments.get("seconds") or 2))
            send({"jsonrpc": "2.0", "id": request_id, "result": text_result("تأخير انتهى")})
            return
        if name == "crash":
            sys.exit(3)
        send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32602, "message": f"أداة غير معروفة: {name}"},
            }
        )
        return

    if request_id is not None:
        send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"method غير مدعوم: {method}"},
            }
        )


def main() -> int:
    # إشعار غير متعلق بطلب: يجب أن يتجاهله العميل بلا انهيار
    send({"jsonrpc": "2.0", "method": "notifications/message",
          "params": {"level": "info", "data": "starting"}})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            send({"jsonrpc": "2.0", "id": None,
                  "error": {"code": -32700, "message": "JSON غير صالح"}})
            continue
        handle(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
