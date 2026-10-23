"""
مزامنة Google Drive (قراءة فقط)
===============================
تجلب مستندات مجلد Drive إلى مساحة عمل محلية **مفهرسة** يمكن للنظام الاستشهاد بها:

    Google Drive (مجلد)  ──►  dest/*.md  ──►  CorpusIndex  ──►  إجابات بمراجع

ضمانات التصميم:
  • **قراءة فقط:** النطاق المطلوب `drive.readonly`، ولا يوجد أي مسار كتابة إلى Drive.
    (مطابق لسياسة `private_only: true` في إعدادات New33.)
  • **تزايدية:** يُقارن `modifiedTime` بسجل المزامنة (manifest) فلا يُعاد تنزيل ما لم يتغيّر.
  • **لا حذف صامت:** الحذف المحلي يحدث فقط عند `prune=True` وللملفات التي جاءت من Drive.
  • **حدود صلبة:** حجم أقصى لكل ملف، وأسماء ملفات مُطهَّرة (لا خروج عن المجلد).

الاعتماديات (اختيارية): `pip install -r requirements-integrations.txt`
بدونها يعمل النظام كما هو؛ تُستخدم الأدوات المحلية فقط.

الاستخدام:
    export GOOGLE_APPLICATION_CREDENTIALS=/path/service-account.json   # أو GOOGLE_DRIVE_TOKEN
    python -m src.integrations.drive sync --folder-id <ID> --dest corpus/drive
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

logger = logging.getLogger("new-cela.drive")

SCOPES = ("https://www.googleapis.com/auth/drive.readonly",)
MAX_FILE_BYTES = 2_000_000
GOOGLE_DOC = "application/vnd.google-apps.document"
SUPPORTED_TEXT = (
    "text/plain",
    "text/markdown",
    "text/x-markdown",
    "text/csv",
    "application/json",
    "application/x-yaml",
    "text/yaml",
)
_UNSAFE = re.compile(r"[^\w\u0600-\u06FF.\-]+", re.UNICODE)


# ================= النماذج =================

class DriveItem(BaseModel):
    """عنصر في Drive — البيانات التي يحتاجها القرار فقط."""

    id: str
    name: str
    mime_type: str = ""
    modified_time: str = ""
    size: int | None = None

    @property
    def supported(self) -> bool:
        return self.mime_type == GOOGLE_DOC or self.mime_type in SUPPORTED_TEXT


class SyncReport(BaseModel):
    """نتيجة مزامنة واحدة — قابلة للعرض في اللوحة والسجل."""

    folder_id: str
    dest: str
    added: list[str] = Field(default_factory=list)
    updated: list[str] = Field(default_factory=list)
    unchanged: int = 0
    unsupported: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    failed: dict[str, str] = Field(default_factory=dict)
    at: str = ""

    @property
    def total_written(self) -> int:
        return len(self.added) + len(self.updated)

    def summary(self) -> str:
        return (
            f"مزامنة Drive: +{len(self.added)} جديد، ~{len(self.updated)} محدَّث، "
            f"={self.unchanged} بلا تغيير، -{len(self.removed)} محذوف، "
            f"!{len(self.failed)} فشل، ({len(self.unsupported)} غير مدعوم)"
        )


@runtime_checkable
class DriveClient(Protocol):
    """عقد عميل Drive — يتيح الاختبار الكامل بلا شبكة ولا بيانات اعتماد."""

    def list_folder(self, folder_id: str) -> list[DriveItem]: ...

    def read_text(self, item: DriveItem, max_bytes: int = MAX_FILE_BYTES) -> str: ...


# ================= عميل Google الحقيقي =================

class GoogleDriveClient:
    """
    عميل حقيقي عبر google-api-python-client — قراءة فقط.

    المصادقة (أحد الخيارين):
      • `GOOGLE_APPLICATION_CREDENTIALS` → حساب خدمة (service account)
      • `GOOGLE_DRIVE_TOKEN` → ملف رمز OAuth (token.json) مُصرَّح مسبقاً
    """

    def __init__(self, service=None, page_size: int = 100) -> None:
        self._service = service
        self.page_size = max(1, min(1000, page_size))

    # ---------------- البناء ----------------
    def _build(self):
        if self._service is not None:
            return self._service
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
        except ImportError as exc:  # pragma: no cover - يعتمد على البيئة
            raise RuntimeError(
                "مكتبات Google غير مثبتة. نفّذ: pip install -r requirements-integrations.txt"
            ) from exc

        creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        token_path = os.environ.get("GOOGLE_DRIVE_TOKEN")
        if creds_path and Path(creds_path).exists():
            credentials = service_account.Credentials.from_service_account_file(
                creds_path, scopes=list(SCOPES)
            )
        elif token_path and Path(token_path).exists():
            from google.oauth2.credentials import Credentials

            credentials = Credentials.from_authorized_user_file(token_path, list(SCOPES))
        else:
            raise RuntimeError(
                "لا توجد بيانات اعتماد Drive. اضبط GOOGLE_APPLICATION_CREDENTIALS "
                "أو GOOGLE_DRIVE_TOKEN (قراءة فقط)."
            )

        self._service = build("drive", "v3", credentials=credentials, cache_discovery=False)
        return self._service

    # ---------------- القراءة ----------------
    def list_folder(self, folder_id: str) -> list[DriveItem]:
        """كل العناصر المباشرة داخل المجلد (بلا مجلدات فرعية — سلوك صريح)."""
        service = self._build()
        items: list[DriveItem] = []
        page_token: str | None = None

        while True:
            response = (
                service.files()
                .list(
                    q=f"'{folder_id}' in parents and trashed = false",
                    fields="nextPageToken, files(id,name,mimeType,modifiedTime,size)",
                    pageSize=self.page_size,
                    pageToken=page_token,
                    includeItemsFromAllDrives=True,
                    supportsAllDrives=True,
                )
                .execute()
            )
            for raw in response.get("files", []):
                if raw.get("mimeType") == "application/vnd.google-apps.folder":
                    continue
                items.append(
                    DriveItem(
                        id=raw["id"],
                        name=raw.get("name", raw["id"]),
                        mime_type=raw.get("mimeType", ""),
                        modified_time=raw.get("modifiedTime", ""),
                        size=int(raw["size"]) if raw.get("size") else None,
                    )
                )
            page_token = response.get("nextPageToken")
            if not page_token:
                break

        return sorted(items, key=lambda item: item.name)

    def read_text(self, item: DriveItem, max_bytes: int = MAX_FILE_BYTES) -> str:
        """يصدّر مستندات Google نصاً، ويُنزّل الملفات النصية مباشرةً."""
        service = self._build()
        if item.mime_type == GOOGLE_DOC:
            data = service.files().export(fileId=item.id, mimeType="text/plain").execute()
        else:
            data = service.files().get(fileId=item.id, alt="media").execute()

        if isinstance(data, str):
            return data[:max_bytes]
        return bytes(data[:max_bytes]).decode("utf-8", errors="replace")


# ================= المزامنة =================

def safe_name(item: DriveItem, suffix: str = ".md") -> str:
    """
    اسم ملف آمن: يمنع `../` والفواصل والرموز الغريبة.

    يُستخدم مُعرِّف الملف كسابقة قصيرة لمنع التصادم بين الأسماء المتشابهة.
    """
    stem = _UNSAFE.sub("-", item.name).strip(".-") or "document"
    stem = stem[:80]
    if stem.lower().endswith(suffix):
        stem = stem[: -len(suffix)]
    return f"{stem}-{item.id[:8]}{suffix}"


def load_manifest(path: str | os.PathLike[str]) -> dict:
    target = Path(path)
    if not target.exists():
        return {"files": {}}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("بيان مزامنة تالف — سيُعاد بناؤه.")
        return {"files": {}}
    data.setdefault("files", {})
    return data


def save_manifest(path: str | os.PathLike[str], manifest: dict) -> None:
    """كتابة ذرّية للبيان."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, target)


def sync_folder(
    client: DriveClient,
    folder_id: str,
    dest: str | os.PathLike[str],
    manifest_path: str | os.PathLike[str] | None = None,
    prune: bool = False,
    max_bytes: int = MAX_FILE_BYTES,
) -> SyncReport:
    """
    يزامن مجلد Drive إلى `dest` بشكل تزايدي، ويعيد تقريراً تفصيلياً.

    لا يرفع استثناء عند فشل ملف واحد: يُسجَّل في `failed` ويُكمَل الباقي.
    """
    destination = Path(dest)
    destination.mkdir(parents=True, exist_ok=True)
    manifest_file = Path(manifest_path) if manifest_path else destination / "_manifest.json"
    manifest = load_manifest(manifest_file)
    known: dict = manifest.get("files", {})

    report = SyncReport(
        folder_id=folder_id,
        dest=str(destination),
        at=datetime.now(UTC).isoformat(timespec="seconds"),
    )

    try:
        items = client.list_folder(folder_id)
    except Exception as exc:  # noqa: BLE001 — تعذّر الاتصال بالكامل
        report.failed["<list>"] = f"{type(exc).__name__}: {exc}"
        save_manifest(manifest_file, manifest)
        return report

    seen_ids: set[str] = set()
    for item in items:
        seen_ids.add(item.id)
        if not item.supported:
            report.unsupported.append(f"{item.name} ({item.mime_type})")
            continue

        record = known.get(item.id, {})
        target = destination / record.get("path", "") if record.get("path") else None
        if target is None:
            target = destination / safe_name(item)

        if (
            record
            and record.get("modified") == item.modified_time
            and target.exists()
        ):
            report.unchanged += 1
            continue

        try:
            text = client.read_text(item, max_bytes=max_bytes)
        except Exception as exc:  # noqa: BLE001 — فشل ملف لا يُسقط المزامنة
            report.failed[item.name] = f"{type(exc).__name__}: {exc}"
            continue

        existing = target.exists()
        temp = target.with_suffix(target.suffix + ".tmp")
        temp.write_text(text[:max_bytes], encoding="utf-8")
        os.replace(temp, target)

        known[item.id] = {
            "name": item.name,
            "path": target.name,
            "mime": item.mime_type,
            "modified": item.modified_time,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
            "chars": len(text),
        }
        (report.updated if existing else report.added).append(target.name)

    # ---- الحذف المحلي (اختياري وصريح) ----
    if prune:
        for file_id, record in list(known.items()):
            if file_id in seen_ids:
                continue
            local = destination / record.get("path", "")
            try:
                if local.is_file() and destination.resolve() in local.resolve().parents:
                    local.unlink()
                    report.removed.append(local.name)
            except OSError as exc:
                report.failed[record.get("name", file_id)] = f"حذف: {type(exc).__name__}"
            known.pop(file_id, None)

    manifest["files"] = known
    manifest["folder_id"] = folder_id
    manifest["updated_at"] = report.at
    save_manifest(manifest_file, manifest)
    return report


def ingest_synced(report: SyncReport, corpus) -> int:
    """
    يضيف ما نُسِخ إلى فهرس المعرفة — يجعل المزامنة «معرفة قابلة للاستشهاد».

    يعيد عدد القطع المضافة.
    """
    destination = Path(report.dest)
    added = 0
    for name in [*report.added, *report.updated]:
        path = destination / name
        if not path.is_file():
            continue
        try:
            added += corpus.ingest_file(path, source=f"drive:{name}", title=name)
        except (OSError, UnicodeError) as exc:
            logger.warning("تعذّر فهرسة %s (%s).", name, type(exc).__name__)
    return added


# ================= واجهة CLI =================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.integrations.drive",
        description="مزامنة مجلد Google Drive (قراءة فقط) إلى مساحة عمل محلية",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sync = sub.add_parser("sync", help="مزامنة مجلد")
    sync.add_argument("--folder-id", required=True, help="مُعرِّف مجلد Drive")
    sync.add_argument("--dest", default="corpus/drive", help="المجلد المحلي الهدف")
    sync.add_argument("--manifest", default=None, help="مسار سجل المزامنة")
    sync.add_argument("--prune", action="store_true", help="حذف المحلي الذي اختفى من Drive")
    sync.add_argument("--max-bytes", type=int, default=MAX_FILE_BYTES)
    sync.add_argument("--corpus", default=".agent_knowledge/corpus.jsonl", help="فهرس المعرفة")
    sync.add_argument("--dry-run", action="store_true", help="فحص فقط بلا كتابة ملفات")

    status = sub.add_parser("status", help="حالة آخر مزامنة")
    status.add_argument("--manifest", default="corpus/drive/_manifest.json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    if args.command == "status":
        manifest = load_manifest(args.manifest)
        files = manifest.get("files", {})
        print(f"مجلد: {manifest.get('folder_id', '—')}")
        print(f"آخر تحديث: {manifest.get('updated_at', '—')}")
        print(f"ملفات متتبَّعة: {len(files)}")
        for record in list(files.values())[:20]:
            print(f"  • {record.get('path')} ({record.get('chars', 0)} حرفاً)")
        return 0

    client = GoogleDriveClient()

    if args.dry_run:
        items = client.list_folder(args.folder_id)
        report = SyncReport(folder_id=args.folder_id, dest=args.dest)
        for item in items:
            (report.unsupported if not item.supported else report.added).append(item.name)
        print(report.summary())
        for name in report.added:
            print(f"  + {name}")
        for name in report.unsupported:
            print(f"  ! {name}")
        return 0

    report = sync_folder(
        client,
        folder_id=args.folder_id,
        dest=args.dest,
        manifest_path=args.manifest,
        prune=args.prune,
        max_bytes=args.max_bytes,
    )
    print(report.summary())
    for name, reason in report.failed.items():
        print(f"  ❌ {name}: {reason}", file=sys.stderr)

    from src.knowledge.corpus import CorpusIndex

    corpus = CorpusIndex(args.corpus)
    chunks = ingest_synced(report, corpus)
    print(f"فهرس المعرفة: {len(corpus)} قطعة ({chunks} مضافة الآن) — {args.corpus}")
    return 0 if not report.failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
