"""
واجهة MCP سطرية — استكشاف وتشغيل مقصود
======================================
    python -m src.mcp.cli servers --config mcp.json
    python -m src.mcp.cli tools   --config mcp.json --server files --yes
    python -m src.mcp.cli call    --config mcp.json --server files \\
        --tool read_text --arguments '{"path": "a.md"}' --yes

`--yes` إلزامي لأي عملية تبدأ عملية فرعية: تشغيل خادم مجهول ليس فعلاً افتراضياً.
بدونه تعمل الأوامر في وضع «عرض فقط» وتخبرك بما كان سيحدث.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from src.mcp.bridge import McpBridge
from src.mcp.client import StdioMcpClient
from src.mcp.config import McpConfig, McpConfigError, ServerConfig, load_config
from src.mcp.protocol import McpError

logger = logging.getLogger("new-cela.mcp.cli")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.mcp.cli",
        description="إدارة خوادم MCP (Model Context Protocol) عبر stdio",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    servers = sub.add_parser("servers", help="اعرض الخوادم المعرَّفة (بلا تشغيل)")
    servers.add_argument("--config", required=True)
    servers.add_argument("--json", action="store_true")

    tools = sub.add_parser("tools", help="ابدأ خادماً واسرد أدواته")
    tools.add_argument("--config", required=True)
    tools.add_argument("--server", required=True)
    tools.add_argument("--yes", action="store_true", help="موافقة على تشغيل الخادم")
    tools.add_argument("--timeout", type=float, default=10.0)

    call = sub.add_parser("call", help="نفّذ أداة على خادم")
    call.add_argument("--config", required=True)
    call.add_argument("--server", required=True)
    call.add_argument("--tool", required=True)
    call.add_argument("--arguments", default="{}", help="JSON لوسائط الأداة")
    call.add_argument("--yes", action="store_true", help="موافقة على التنفيذ")
    call.add_argument("--timeout", type=float, default=30.0)
    return parser


def _require_approval(config: ServerConfig, yes: bool, action: str) -> bool:
    if yes:
        return True
    print(
        f"⛔ لم يُنفَّذ شيء: {action} يتطلب تشغيل «{config.command} {' '.join(config.args)}». "
        "أعد الأمر مع --yes بعد مراجعة الأمر.",
        file=sys.stderr,
    )
    return False


def _open(config: ServerConfig, name: str, timeout: float) -> StdioMcpClient:
    client = StdioMcpClient(config.argv(), name=name, env=config.env, timeout=timeout)
    client.start()
    client.initialize()
    return client


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    try:
        config: McpConfig = load_config(args.config)
    except McpConfigError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 2

    if args.command == "servers":
        if args.json:
            print(json.dumps(config.describe(), ensure_ascii=False, indent=2))
            return 0
        if not config.servers:
            print("لا خوادم معرَّفة.")
            return 0
        for item in config.describe():
            mark = "✅ مفعّل" if item["enabled"] else "• معطّل"
            print(f"{mark}  {item['name']}: {item['command']} {' '.join(item['args'])}")
            if item["description"]:
                print(f"        {item['description']}")
        return 0

    try:
        server = config.get(args.server)
    except McpConfigError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 2

    if not _require_approval(server, args.yes, args.command):
        return 3

    try:
        with _open(server, args.server, args.timeout) as client:
            if args.command == "tools":
                found = client.list_tools()
                print(f"{len(found)} أداة من «{args.server}»:")
                for tool in found:
                    print(f"  • {tool.name} — {tool.description[:100]}")
                    if tool.input_schema.get("properties"):
                        keys = ", ".join(tool.input_schema["properties"])
                        print(f"    الوسائط: {keys}")
                return 0

            bridge = McpBridge(client, args.server, client.list_tools())
            names = [tool.name for tool in bridge.tools]
            if args.tool not in names:
                print(f"❌ أداة غير معروفة: {args.tool}. المتاح: {', '.join(names)}",
                      file=sys.stderr)
                return 2
            try:
                arguments = json.loads(args.arguments)
            except json.JSONDecodeError as exc:
                print(f"❌ --arguments ليس JSON صالحاً: {exc.msg}", file=sys.stderr)
                return 2
            print(client.call_tool(args.tool, arguments, timeout=args.timeout))
            return 0
    except McpError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
