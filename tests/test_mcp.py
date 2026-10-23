"""
اختبارات MCP — بعملية خادم حقيقية عبر stdio (لا محاكاة داخلية).

يغطي: المصافحة، سرد الأدوات، الاستدعاء، أخطاء الخادم، المهلة، الانهيار،
عزل البيئة (عدم تسريب المفاتيح)، ثم الجسر إلى سجل الأدوات مع السياسة والميزانية.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.core.policy import PolicyEngine, ToolRisk
from src.core.quota import QuotaGuard
from src.mcp.bridge import McpBridge, namespaced, params_model
from src.mcp.client import StdioMcpClient, sanitized_env
from src.mcp.config import (
    McpConfigError,
    example_config,
    load_config,
    parse_config,
)
from src.mcp.protocol import (
    McpError,
    McpProtocolError,
    McpServerClosed,
    McpTimeout,
    McpTool,
    build_notification,
    build_request,
    parse_message,
    parse_tools,
    text_from_result,
    valid_tool_name,
)
from src.tools.builtin import build_default_registry
from src.tools.registry import ToolContext

SERVER = Path(__file__).parent / "fixtures" / "fake_mcp_server.py"


def start_client(timeout: float = 6.0, env: dict[str, str] | None = None) -> StdioMcpClient:
    client = StdioMcpClient(
        [sys.executable, str(SERVER)], name="fake", timeout=timeout, env=env
    )
    client.start()
    client.initialize()
    return client


@pytest.fixture()
def client():
    instance = start_client()
    try:
        yield instance
    finally:
        instance.close()


# ================= البروتوكول (بلا عمليات) =================

def test_build_request_shape() -> None:
    payload = build_request(7, "tools/list", {})
    assert payload["jsonrpc"] == "2.0"
    assert payload["id"] == 7
    assert payload["params"] == {}


def test_notification_has_no_id() -> None:
    assert "id" not in build_notification("notifications/initialized")


def test_parse_message_rejects_bad_json() -> None:
    with pytest.raises(McpProtocolError):
        parse_message("{ليس JSON")


def test_parse_message_rejects_wrong_version() -> None:
    with pytest.raises(McpProtocolError):
        parse_message('{"id": 1, "result": {}}')


def test_parse_tools_skips_invalid_names() -> None:
    tools = parse_tools(
        {
            "tools": [
                {"name": "ok_tool", "description": "d", "inputSchema": {"type": "object"}},
                {"name": "bad name!", "description": "x"},
                {"name": "", "description": "y"},
            ]
        }
    )
    assert [tool.name for tool in tools] == ["ok_tool"]


def test_text_from_result_marks_non_text() -> None:
    text = text_from_result(
        {
            "content": [
                {"type": "text", "text": "أهلاً"},
                {"type": "image", "data": "..."},
                {"type": "resource", "resource": {"uri": "file:///a"}},
            ]
        }
    )
    assert "أهلاً" in text
    assert "غير معروض" in text
    assert "file:///a" in text


def test_valid_tool_name_rules() -> None:
    assert valid_tool_name("echo")
    assert valid_tool_name("read-file_2")
    assert not valid_tool_name("1abc")
    assert not valid_tool_name("a b")
    assert not valid_tool_name("x" * 65)


# ================= العميل الحقيقي =================

def test_initialize_reports_server_info(client: StdioMcpClient) -> None:
    assert client.server_info.get("name") == "fake-mcp"
    assert client.protocol_version
    assert client.running


def test_list_tools(client: StdioMcpClient) -> None:
    tools = client.list_tools()
    names = [tool.name for tool in tools]
    assert names == ["echo", "env_keys", "boom", "slow"]
    echo = next(tool for tool in tools if tool.name == "echo")
    assert echo.input_schema["required"] == ["text"]


def test_call_tool_returns_text(client: StdioMcpClient) -> None:
    assert client.call_tool("echo", {"text": "مرحباً"}) == "echo: مرحباً"
    assert client.call_tool("echo", {"text": "x", "times": 3}) == "echo: xecho: xecho: x"


def test_call_tool_without_arguments(client: StdioMcpClient) -> None:
    assert client.call_tool("env_keys")  # أسماء متغيرات فقط


def test_tool_error_raises_mcp_error(client: StdioMcpClient) -> None:
    with pytest.raises(McpError, match="فشل مقصود"):
        client.call_tool("boom", {})


def test_unknown_tool_error_code(client: StdioMcpClient) -> None:
    with pytest.raises(McpError) as excinfo:
        client.call_tool("does_not_exist", {})
    assert excinfo.value.code == -32602


def test_invalid_tool_name_rejected_locally(client: StdioMcpClient) -> None:
    with pytest.raises(McpError, match="غير صالح"):
        client.call_tool("bad name", {})


def test_timeout_does_not_hang() -> None:
    instance = start_client(timeout=0.5)
    try:
        with pytest.raises(McpTimeout):
            instance.request("tools/call", {"name": "slow", "arguments": {"seconds": 3}},
                             timeout=0.4)
    finally:
        instance.close()


def test_secrets_are_not_passed_to_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "secret-value")
    monkeypatch.setenv("GROQ_API_KEY", "another-secret")
    env = sanitized_env()
    assert "GEMINI_API_KEY" not in env
    assert "GROQ_API_KEY" not in env
    assert "PATH" in env


def test_sanitized_env_allows_explicit_values() -> None:
    env = sanitized_env({"MY_TOKEN": "ok"})
    assert env["MY_TOKEN"] == "ok"


def test_server_env_is_isolated_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_SHOULD_NOT_LEAK", "1")
    instance = start_client()
    try:
        keys = instance.call_tool("env_keys", {}).split(",")
    finally:
        instance.close()
    assert "SECRET_SHOULD_NOT_LEAK" not in keys


def test_start_failure_is_reported() -> None:
    client = StdioMcpClient(["/nonexistent/binary/xyz"], name="missing")
    with pytest.raises(McpServerClosed, match="تعذّر تشغيل"):
        client.start()


def test_call_after_close_is_rejected() -> None:
    instance = start_client()
    instance.close()
    with pytest.raises(McpServerClosed):
        instance.call_tool("echo", {"text": "x"})


def test_context_manager_closes() -> None:
    with start_client() as inner:
        assert inner.running
    assert not inner.running


def test_describe_has_no_env_values(client: StdioMcpClient) -> None:
    described = client.describe()
    assert described["server"]["name"] == "fake-mcp"
    assert "env" not in described


# ================= الإعداد =================

def test_parse_config_validates_structure() -> None:
    config = parse_config(
        {"servers": {"files": {"command": "python", "args": ["-m", "x"], "enabled": True}}}
    )
    assert config.enabled_servers()
    assert config.get("files").argv() == ["python", "-m", "x"]


def test_parse_config_rejects_bad_name() -> None:
    with pytest.raises(McpConfigError, match="اسم خادم"):
        parse_config({"servers": {"bad name": {"command": "python"}}})


def test_parse_config_rejects_missing_command() -> None:
    with pytest.raises(McpConfigError):
        parse_config({"servers": {"x": {}}})


def test_args_as_string_is_rejected() -> None:
    with pytest.raises(McpConfigError):
        parse_config({"servers": {"x": {"command": "python", "args": "-m x"}}})


def test_disabled_by_default() -> None:
    config = parse_config({"servers": {"x": {"command": "python"}}})
    assert config.get("x").enabled is False
    assert config.enabled_servers() == {}


def test_load_missing_file() -> None:
    with pytest.raises(McpConfigError, match="غير موجود"):
        load_config("/tmp/definitely-not-here-mcp.json")


def test_load_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "mcp.json"
    path.write_text("{ ليس JSON", encoding="utf-8")
    with pytest.raises(McpConfigError, match="تعذّر قراءة"):
        load_config(path)


def test_load_round_trip(tmp_path: Path) -> None:
    import json

    path = tmp_path / "mcp.json"
    path.write_text(json.dumps(example_config()), encoding="utf-8")
    config = load_config(path)
    assert config.names() == ["files"]
    assert config.get("files").enabled is False


def test_describe_hides_env() -> None:
    config = parse_config(
        {"servers": {"x": {"command": "python", "env": {"TOKEN": "s3cret"}}}}
    )
    assert "s3cret" not in str(config.describe())


# ================= الجسر إلى سجل الأدوات =================

def test_namespaced_names_are_identifiers() -> None:
    assert namespaced("my server", "read-file").isidentifier()
    assert namespaced("s", "t") == "mcp__s__t"


def test_params_model_uses_schema() -> None:
    tool = McpTool(
        name="echo",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}, "times": {"type": "integer"}},
            "required": ["text"],
        },
    )
    model = params_model(tool)
    instance = model(text="a")
    assert instance.text == "a"
    assert instance.times is None
    with pytest.raises(ValidationError):
        model()  # text مطلوب


def test_params_model_without_schema_accepts_arguments() -> None:
    model = params_model(McpTool(name="free"))
    instance = model(arguments={"a": 1})
    assert instance.arguments == {"a": 1}


def test_params_model_forbids_unknown_fields() -> None:
    model = params_model(
        McpTool(
            name="echo",
            input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
        )
    )
    with pytest.raises(ValidationError):
        model(text="a", extra_field=1)


def test_bridge_registers_tools(client: StdioMcpClient) -> None:
    registry = build_default_registry(
        ToolContext(policy=PolicyEngine(), quota=QuotaGuard(max_tool_calls=5))
    )
    bridge = McpBridge(client, "fake", client.list_tools())
    names = bridge.register_into(registry)

    assert "mcp__fake__echo" in names
    assert registry.get("mcp__fake__echo").risk is ToolRisk.EXECUTE
    assert registry.get("mcp__fake__echo").scope == "mcp.call"


def test_bridge_call_denied_without_scope(client: StdioMcpClient) -> None:
    """بلا نطاق mcp.call: النداء مرفوض قبل أي تنفيذ."""
    policy = PolicyEngine(granted_scopes={"fs.read"})
    registry = build_default_registry(ToolContext(policy=policy, quota=QuotaGuard()))
    McpBridge(client, "fake", client.list_tools()).register_into(registry)

    result = asyncio.run(registry.invoke("mcp__fake__echo", {"text": "x"}))
    assert not result.ok
    assert result.denied
    assert "mcp.call" in result.error


def test_bridge_call_allowed_with_scope(client: StdioMcpClient) -> None:
    from src.core.policy import AutoApprove

    policy = PolicyEngine(
        granted_scopes={"fs.read", "mcp.call"}, approval_handler=AutoApprove()
    )
    quota = QuotaGuard(max_tool_calls=3)
    registry = build_default_registry(ToolContext(policy=policy, quota=quota))
    McpBridge(client, "fake", client.list_tools()).register_into(registry)

    result = asyncio.run(registry.invoke("mcp__fake__echo", {"text": "من البعيد"}))
    assert result.ok, result.error
    assert result.output == "echo: من البعيد"
    assert quota.usage.tool_calls == 1


def test_bridge_remote_error_is_a_result_not_a_crash(client: StdioMcpClient) -> None:
    from src.core.policy import AutoApprove

    policy = PolicyEngine(
        granted_scopes={"mcp.call"}, approval_handler=AutoApprove()
    )
    registry = build_default_registry(ToolContext(policy=policy, quota=QuotaGuard()))
    McpBridge(client, "fake", client.list_tools()).register_into(registry)

    result = asyncio.run(registry.invoke("mcp__fake__boom", {}))
    assert not result.ok
    assert "فشل مقصود" in result.error


def test_bridge_quota_stops_calls(client: StdioMcpClient) -> None:
    from src.core.policy import AutoApprove

    policy = PolicyEngine(granted_scopes={"mcp.call"}, approval_handler=AutoApprove())
    registry = build_default_registry(
        ToolContext(policy=policy, quota=QuotaGuard(max_tool_calls=1))
    )
    McpBridge(client, "fake", client.list_tools()).register_into(registry)

    first = asyncio.run(registry.invoke("mcp__fake__echo", {"text": "1"}))
    second = asyncio.run(registry.invoke("mcp__fake__echo", {"text": "2"}))
    assert first.ok
    assert not second.ok
    assert "تج��وز" in second.error or "حد" in second.error


def test_bridge_does_not_register_duplicates(client: StdioMcpClient) -> None:
    registry = build_default_registry(ToolContext(policy=PolicyEngine(), quota=QuotaGuard()))
    bridge = McpBridge(client, "fake", client.list_tools())
    first = bridge.register_into(registry)
    second = bridge.register_into(registry)
    assert first and second == []


def test_bridge_describe_lists_namespaced_tools(client: StdioMcpClient) -> None:
    bridge = McpBridge(client, "fake", client.list_tools())
    info = bridge.describe()
    assert info["server"] == "fake"
    assert info["scope"] == "mcp.call"
    assert "mcp__fake__echo" in info["tools"]


# ================= الربط بواجهة التشغيل =================

FIXTURE_CONFIG = {
    "servers": {
        "fake": {"command": sys.executable, "args": [str(SERVER)], "enabled": True},
        "other": {"command": "/nonexistent/binary", "enabled": False},
    }
}


def _write_config(tmp_path: Path) -> Path:
    import json

    path = tmp_path / "mcp.json"
    path.write_text(json.dumps(FIXTURE_CONFIG), encoding="utf-8")
    return path


def _args(tmp_path: Path, **overrides):
    import argparse

    base = {
        "mcp_config": str(_write_config(tmp_path)),
        "mcp_server": ["fake"],
        "yes": False,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_start_mcp_servers_requires_explicit_yes(tmp_path: Path) -> None:
    from src.main import start_mcp_servers

    registry = build_default_registry()
    clients = start_mcp_servers(_args(tmp_path), registry)

    assert clients == []
    assert "mcp__fake__echo" not in registry
    assert len(registry) == 5  # الأدوات المدمجة فقط، وبينها replace_text الجديدة


def test_start_mcp_servers_registers_tools_when_approved(tmp_path: Path) -> None:
    from src.main import start_mcp_servers

    registry = build_default_registry()
    clients = start_mcp_servers(_args(tmp_path, yes=True), registry)
    try:
        assert clients and clients[0].running
        assert "mcp__fake__echo" in registry
        assert registry.get("mcp__fake__echo").scope == "mcp.call"
    finally:
        for client in clients:
            client.close()


def test_start_mcp_servers_skips_unknown(tmp_path: Path) -> None:
    from src.main import start_mcp_servers

    registry = build_default_registry()
    clients = start_mcp_servers(_args(tmp_path, yes=True, mcp_server=["ghost"]), registry)
    assert clients == []


def test_start_mcp_servers_survives_broken_command(tmp_path: Path) -> None:
    from src.main import start_mcp_servers

    registry = build_default_registry()
    clients = start_mcp_servers(_args(tmp_path, yes=True, mcp_server=["other"]), registry)
    assert clients == []


def test_start_mcp_servers_without_flags_is_noop() -> None:
    import argparse

    from src.main import start_mcp_servers

    registry = build_default_registry()
    assert start_mcp_servers(argparse.Namespace(), registry) == []
