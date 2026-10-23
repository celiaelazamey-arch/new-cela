"""
إعداد خوادم MCP (mcp.json)
==========================
الملف يصف **كيف يُشغَّل** كل خادم، ولا يُشغّل شيئاً بنفسه:

```json
{
  "servers": {
    "files": {
      "command": "python",
      "args": ["-m", "my_server"],
      "enabled": false,
      "description": "خادم ملفات محلي"
    }
  }
}
```

قواعد أمان مقصودة:
  • `enabled` افتراضياً `false` — لا يبدأ خادم لأن ملفاً ذكره.
  • `command` يجب أن يكون معرّفاً صريحاً، ولا تُقبل أوامر مركّبة (`bash -c` تُقبل
    فقط إن كتبها المستخدم بنفسه؛ نحن لا نبنيها).
  • `env` لا يُملأ تلقائياً من بيئة النظام: ما لم يُذكر هنا لا يُمرَّر.
  • تحميل الملف لا يمنح أي صلاحية؛ الموافقة تحدث عند التشغيل في طبقة السياسة.
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError, field_validator


class McpConfigError(ValueError):
    """ملف إعداد غير صالح — برسالة عربية تعرف مكان المشكلة."""


class ServerConfig(BaseModel):
    """خادم واحد كما وُصف في الملف."""

    command: str = Field(..., min_length=1)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    enabled: bool = False
    description: str = ""

    @field_validator("args", mode="before")
    @classmethod
    def _coerce_args(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            raise TypeError("args يجب أن تكون قائمة نصوص، لا نصاً واحداً")
        return [str(item) for item in value]

    @field_validator("env", mode="before")
    @classmethod
    def _coerce_env(cls, value):
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise TypeError("env يجب أن تكون كائناً مفتاح/قيمة")
        return {str(key): str(item) for key, item in value.items()}

    def argv(self) -> list[str]:
        """الأمر الكامل الجاهز للتشغيل."""
        return [self.command, *self.args]


class McpConfig(BaseModel):
    """كل الخوادم المعرَّفة في ملف واحد."""

    servers: dict[str, ServerConfig] = Field(default_factory=dict)

    def get(self, name: str) -> ServerConfig:
        if name not in self.servers:
            raise McpConfigError(f"خادم غير معرَّف: {name}")
        return self.servers[name]

    def names(self) -> list[str]:
        return sorted(self.servers)

    def enabled_servers(self) -> dict[str, ServerConfig]:
        return {name: cfg for name, cfg in sorted(self.servers.items()) if cfg.enabled}

    def describe(self) -> list[dict]:
        return [
            {
                "name": name,
                "command": cfg.command,
                "args": cfg.args,
                "enabled": cfg.enabled,
                "description": cfg.description,
            }
            for name, cfg in sorted(self.servers.items())
        ]


def parse_config(data: dict) -> McpConfig:
    """يتحقق من البنية ويعيد كائناً موثوقاً."""
    if not isinstance(data, dict):
        raise McpConfigError("جذر الملف يجب أن يكون كائناً يحوي servers.")
    servers = data.get("servers", data)
    if not isinstance(servers, dict):
        raise McpConfigError("servers يجب أن يكون كائناً: {اسم: إعداد}.")
    try:
        config = McpConfig(servers=servers)
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first.get("loc", ()))
        raise McpConfigError(f"إعداد خادم غير صالح ({location}): {first.get('msg')}") from exc
    except TypeError as exc:
        raise McpConfigError(f"إعداد خادم غير صالح: {exc}") from exc
    for name in config.servers:
        if not name.replace("-", "_").isidentifier():
            raise McpConfigError(f"اسم خادم غير صالح: {name!r}")
    return config


def load_config(path: str | Path) -> McpConfig:
    """يقرأ ملفاً فعلياً — أخطاء القراءة تُترجم إلى رسالة صريحة."""
    target = Path(path)
    if not target.exists():
        raise McpConfigError(f"ملف إعداد MCP غير موجود: {target}")
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise McpConfigError(f"تعذّر قراءة {target}: {type(exc).__name__}") from exc
    return parse_config(data)


def example_config() -> dict:
    """مثال جاهز للنسخ — معطَّل افتراضياً كي لا يبدأ شيء بالخطأ."""
    return {
        "servers": {
            "files": {
                "command": "python",
                "args": ["-m", "my_mcp_server"],
                "enabled": False,
                "description": "خادم ملفات محلي (MCP stdio)",
            }
        }
    }
