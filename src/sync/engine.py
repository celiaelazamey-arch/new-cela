"""
مزامنة ثنائية الاتجاه مع Google Drive — بموافقة صريحة لكل كتابة
==============================================================
يجعل هذا المستودع ونظامك مترابطين فعلاً، لكن **لا يرفع شيئاً من تلقاء نفسه**:

  plan  ──►  جدول عمليات (رفع/تحديث/تنزيل/تعارض) بلا أي كتابة
  apply ──►  تنفيذ **بعد** موافقة لكل عملية، عبر دالة `approve`

القواعد الصلبة:
  1. **الرفض هو الافتراضي:** بلا `approve` تُرفض كل كتابة وتُسجَّل.
  2. **لا حذف:** الحذف يحتاج `allow_delete=True` صراحةً (وهو معطَّل في CLI أيضاً).
  3. **كشف التغيّر من الجانبين:** يُقارن md5 المحلي بسجل آخر مزامنة (`base`)
     مع `modifiedTime` البعيد — فالتعارض يُكتشف لا يُبتلع.
  4. **سياسة التعارض صريحة:** `newest` (الأحدث يفوز) أو `manual` (تعارض يُبلَّغ فقط).
  5. **المحتوى المُنزَّل بيانات لا تعليمات** — لا يُنفَّذ ولا يُوجَّه للنموذج إلا كسياق موثّق.
  6. **أقل صلاحية:** الكتابة تحتاج نطاق `drive.file` (ملفات أنشأها هذا التطبيق فقط)،
     لا `drive` الكامل. تُعلَن في `drive_write.py` وتُرفض غيرها.

الاستخدام:
    python -m src.sync.engine plan  --folder-id ID --dir corpus/shared
    python -m src.sync.engine apply --folder-id ID --dir corpus/shared --yes
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

logger = logging.getLogger("new-cela.sync")

TEXT_SUFFIXES = (".md", ".txt", ".markdown")
MAX_SYNC_BYTES = 2_000_000


# ================= النماذج =================

class RemoteEntry(BaseModel):
    """ملف بعيد ببيانات كافية لكشف التغيّر — بلا محتواه."""

    id: str
    name: str
    modified: str = ""
    md5: str = ""
    size: int | None = None


class Action(BaseModel):
    """عملية واحدة مقترحة/منفَّذة — قابلة للعرض والتدقيق."""

    op: Literal["upload", "update", "download", "conflict", "skip"]
    name: str
    reason: str
    local_path: str = ""
    remote_id: str = ""

    def render(self) -> str:
        icon = {"upload": "⬆️", "update": "🔁", "download": "⬇️",
                "conflict": "⚠️", "skip": "•"}[self.op]
        return f"{icon} {self.op:9s} {self.name} — {self.reason}"


class SyncPlan(BaseModel):
    """خطة كاملة — تُعرض قبل أي تنفيذ."""

    folder_id: str
    directory: str
    actions: list[Action] = Field(default_factory=list)
    at: str = ""

    @property
    def writes(self) -> list[Action]:
        return [a for a in self.actions if a.op in {"upload", "update"}]

    @property
    def conflicts(self) -> list[Action]:
        return [a for a in self.actions if a.op == "conflict"]

    def summary(self) -> str:
        counts: dict[str, int] = {}
        for action in self.actions:
            counts[action.op] = counts.get(action.op, 0) + 1
        detail = "، ".join(f"{key}={value}" for key, value in sorted(counts.items())) or "لا شيء"
        return f"خطة المزامنة ({len(self.actions)} عملية): {detail}"


class ApplyReport(BaseModel):
    """ما نُفِّذ فعلاً — والأهم: ما رُفض ولماذا."""

    applied: list[Action] = Field(default_factory=list)
    denied: list[Action] = Field(default_factory=list)
    failed: dict[str, str] = Field(default_factory=dict)
    at: str = ""

    def summary(self) -> str:
        return (
            f"تنفيذ المزامنة: نُفِّذ {len(self.applied)}، رُفض {len(self.denied)}، "
            f"فشل {len(self.failed)}"
        )


@runtime_checkable
class TwoWayClient(Protocol):
    """عقد العميل — يسمح باختبار كامل بلا شبكة."""

    def list_folder(self, folder_id: str) -> list[RemoteEntry]: ...

    def download(self, entry: RemoteEntry) -> str: ...

    def upload(self, folder_id: str, name: str, content: str) -> str: ...

    def update(self, remote_id: str, content: str) -> str: ...

    def delete(self, remote_id: str) -> None: ...


@dataclass
class LocalFile:
    name: str
    path: Path
    md5: str
    size: int
    mtime: float


# ================= أدوات =================

def md5_of_bytes(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def scan_local(directory: Path, suffixes: tuple[str, ...] = TEXT_SUFFIXES) -> dict[str, LocalFile]:
    """يقرأ الملفات النصية محلياً — الأسماء المفتاحية هي أسماء الملفات."""
    found: dict[str, LocalFile] = {}
    if not directory.exists():
        return found
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        raw = path.read_bytes()[:MAX_SYNC_BYTES]
        found[path.name] = LocalFile(
            name=path.name,
            path=path,
            md5=md5_of_bytes(raw),
            size=len(raw),
            mtime=path.stat().st_mtime,
        )
    return found


def load_base(path: Path) -> dict[str, dict[str, str]]:
    """سجل آخر مزامنة ناجحة — أساس كشف التغيّر من الجانبين."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("سجل مزامنة تالف — يُعاد بناؤه من الحالة الحالية.")
        return {}
    return data.get("files", {})


def save_base(path: Path, files: dict[str, dict[str, str]], folder_id: str) -> None:
    """كتابة ذرّية للسجل."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    payload = {
        "folder_id": folder_id,
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "files": files,
    }
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temp, path)


# ================= التخطيط =================

def plan_sync(
    folder_id: str,
    directory: str | os.PathLike[str],
    remotes: list[RemoteEntry],
    base: dict[str, dict[str, str]],
    conflict_policy: Literal["newest", "manual"] = "manual",
    allow_download: bool = True,
) -> SyncPlan:
    """
    يبني خطة بلا أي كتابة. كل قرار يحمل سبباً مقروءاً.

    حالات التغيّر (بمقارنة `base` بالحالة الحالية):
      • محلي جديد (لا أثر في base) + غير موجود بعيداً ⇒ رفع.
      • بعيد تغيّر ولم يتغيّر المحلي ⇒ تنزيل.
      • محلي تغيّر ولم يتغيّر البعيد ⇒ تحديث.
      • الاثنان تغيّرا ⇒ تعارض (يُحلّ حسب السياسة أو يُبلَّغ).
    """
    target = Path(directory)
    local = scan_local(target)
    remote_by_name = {entry.name: entry for entry in remotes}
    plan = SyncPlan(
        folder_id=folder_id,
        directory=str(target),
        at=datetime.now(UTC).isoformat(timespec="seconds"),
    )

    for name, item in local.items():
        record = base.get(name, {})
        entry = remote_by_name.get(name)
        remote_md5 = entry.md5 if entry else ""
        local_md5 = item.md5

        if entry is None:
            if record.get("remote_id"):
                plan.actions.append(
                    Action(
                        op="skip",
                        name=name,
                        reason="كان بعيداً سابقاً واختفى — لا نحذف تلقائياً.",
                    )
                )
            else:
                plan.actions.append(
                    Action(
                        op="upload",
                        name=name,
                        reason=f"ملف محلي جديد ({item.size} بايت).",
                        local_path=str(item.path),
                    )
                )
            continue

        local_changed = local_md5 != record.get("md5", "")
        remote_changed = bool(remote_md5) and remote_md5 != record.get("remote_md5", "")

        if not local_changed and not remote_changed:
            plan.actions.append(Action(op="skip", name=name, reason="لا تغيير في الجانبين."))
        elif local_changed and not remote_changed:
            plan.actions.append(
                Action(
                    op="update", name=name, reason="المحلي تغيّر والبعيد كما هو.",
                    local_path=str(item.path), remote_id=entry.id,
                )
            )
        elif remote_changed and not local_changed:
            plan.actions.append(
                Action(
                    op="download", name=name, reason="البعيد تغيّر والمحلي كما هو.",
                    local_path=str(item.path), remote_id=entry.id,
                )
            )
        else:
            newest_local = item.mtime > _parse_ts(entry.modified)
            winner = "المحلي" if newest_local else "البعيد"
            if conflict_policy == "newest":
                plan.actions.append(
                    Action(
                        op="update" if newest_local else "download",
                        name=name,
                        reason=f"تعارض حُلّ بسياسة الأحدث ({winner} أحدث).",
                        local_path=str(item.path),
                        remote_id=entry.id,
                    )
                )
            else:
                plan.actions.append(
                    Action(
                        op="conflict", name=name,
                        reason=f"تغيّر الجانبان — يحتاج قراراً يدوياً ({winner} أحدث).",
                        local_path=str(item.path), remote_id=entry.id,
                    )
                )

    # ملفات بعيدة لا وجود محلي لها
    for name, entry in remote_by_name.items():
        if name in local:
            continue
        if allow_download:
            plan.actions.append(
                Action(
                    op="download", name=name, reason="موجود بعيداً فقط.",
                    local_path=str(target / name), remote_id=entry.id,
                )
            )
        else:
            plan.actions.append(
                Action(op="skip", name=name, reason="بعيد فقط — التنزيل معطَّل.", remote_id=entry.id)
            )

    return plan


def _parse_ts(value: str) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return 0.0


# ================= التنفيذ =================

def deny_all(action: Action) -> bool:
    """المعالج الافتراضي: لا موافقة. كل كتابة تُرفض وتُسجَّل."""
    return False


@dataclass
class ApplyState:
    """حالة قابلة للتفتيش بعد التنفيذ — تُستخدم في الاختبارات والتدقيق."""

    written: dict[str, dict[str, str]] = field(default_factory=dict)


def apply_plan(
    plan: SyncPlan,
    client: TwoWayClient,
    base: dict[str, dict[str, str]],
    base_path: Path,
    approve: Callable[[Action], bool] = deny_all,
    allow_delete: bool = False,
) -> ApplyReport:
    """
    ينفّذ الخطة عملاً بعملية، بعد موافقة صريحة لكل منها.

    التنزيل ليس كتابة على Drive لكنه كتابة محلية ⇒ يمر أيضاً عبر `approve`
    كي لا يُدخل النظام محتوى لم يوافق عليه المستخدم.
    """
    report = ApplyReport(at=datetime.now(UTC).isoformat(timespec="seconds"))
    updated = dict(base)

    for action in plan.actions:
        if action.op in {"skip", "conflict"}:
            continue

        if not approve(action):
            report.denied.append(action)
            continue

        try:
            if action.op == "upload":
                content = Path(action.local_path).read_text(encoding="utf-8")
                remote_id = client.upload(plan.folder_id, action.name, content)
                updated[action.name] = {
                    "remote_id": remote_id,
                    "md5": md5_of_bytes(content.encode("utf-8")),
                    "remote_md5": md5_of_bytes(content.encode("utf-8")),
                }
            elif action.op == "update":
                content = Path(action.local_path).read_text(encoding="utf-8")
                remote_id = client.update(action.remote_id, content)
                updated[action.name] = {
                    "remote_id": remote_id or action.remote_id,
                    "md5": md5_of_bytes(content.encode("utf-8")),
                    "remote_md5": md5_of_bytes(content.encode("utf-8")),
                }
            elif action.op == "download":
                entry = RemoteEntry(id=action.remote_id, name=action.name)
                content = client.download(entry)
                Path(action.local_path).parent.mkdir(parents=True, exist_ok=True)
                temp = Path(action.local_path).with_suffix(
                    Path(action.local_path).suffix + ".tmp"
                )
                temp.write_text(content, encoding="utf-8")
                os.replace(temp, action.local_path)
                updated[action.name] = {
                    "remote_id": action.remote_id,
                    "md5": md5_of_bytes(content.encode("utf-8")),
                    "remote_md5": md5_of_bytes(content.encode("utf-8")),
                }
            elif action.op == "delete" and allow_delete:  # pragma: no cover - محمي
                client.delete(action.remote_id)
            else:
                report.failed[action.name] = "عملية غير مدعومة"
                continue
        except Exception as exc:  # noqa: BLE001 — فشل عملية لا يُسقط الباقي
            report.failed[action.name] = f"{type(exc).__name__}: {exc}"
            continue

        report.applied.append(action)

    save_base(base_path, updated, plan.folder_id)
    return report


# ================= واجهة CLI =================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.sync.engine",
        description="مزامنة ثنائية الاتجاه مع Drive — خطة أولاً، وموافقة قبل كل كتابة",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (("plan", "اعرض الخطة فقط (لا كتابة)"),
                            ("apply", "نفّذ الخطة بعد الموافقة")):
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("--folder-id", required=True)
        cmd.add_argument("--dir", default="corpus/shared", help="المجلد المحلي المزامن")
        cmd.add_argument("--base", default=None, help="سجل آخر مزامنة")
        cmd.add_argument(
            "--conflict-policy", choices=("manual", "newest"), default="manual",
            help="manual = بلّغ فقط (الافتراضي الآمن)، newest = الأحدث يفوز",
        )
        cmd.add_argument("--no-download", action="store_true", help="لا تنزيل من Drive")
        if name == "apply":
            cmd.add_argument("--yes", action="store_true", help="موافقة على كل عملية")
            cmd.add_argument("--allow-delete", action="store_true", help="تفعيل الحذف (مرفوض افتراضياً)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    target = Path(args.dir)
    base_path = Path(args.base) if args.base else target / "_sync_state.json"

    from src.integrations.drive_write import GoogleDriveWriter

    client = GoogleDriveWriter()
    remotes = client.list_entries(args.folder_id)
    plan = plan_sync(
        folder_id=args.folder_id,
        directory=target,
        remotes=remotes,
        base=load_base(base_path),
        conflict_policy=args.conflict_policy,
        allow_download=not args.no_download,
    )

    print(plan.summary())
    for action in plan.actions:
        print("  " + action.render())

    if args.command == "plan":
        print("\nلم يُكتب شيء. استخدم `apply` مع الموافقة للتنفيذ.")
        return 0

    approve = (lambda action: True) if args.yes else _interactive_approve
    report = apply_plan(
        plan,
        client,
        base=load_base(base_path),
        base_path=base_path,
        approve=approve,
        allow_delete=args.allow_delete,
    )
    print("\n" + report.summary())
    for action in report.denied:
        print(f"  🚫 رُفض: {action.name} ({action.op})")
    for name, reason in report.failed.items():
        print(f"  ❌ فشل: {name} — {reason}")
    return 0 if not report.failed else 1


def _interactive_approve(action: Action) -> bool:
    answer = input(f"موافقة على {action.op} لـ «{action.name}»؟ [y/N] ").strip().lower()
    return answer in {"y", "yes", "نعم"}


if __name__ == "__main__":
    raise SystemExit(main())
