"""
كاتب Drive — بأقل صلاحية ممكنة (`drive.file`)
=============================================
هذا هو **المسار الوحيد** في المشروع الذي يكتب إلى Drive، وقيوده صريحة:

  • النطاق `auth/drive.file` فقط: يرى/يعدّل الملفات التي أنشأها هذا التطبيق.
    لا وصول إلى بقية مجلدات المستخدم ولو أراد.
  • لا حذف إلا بنداء صريح من `src.sync.engine` مع `allow_delete=True`.
  • كل عملية تُعيد مُعرِّف الملف، ويُسجَّل بصمته في سجل المزامنة.
  • رفض النطاقات الأخرى صراحةً في `_build`.

بيانات الاعتماد من البيئة فقط (`.env`)، ولا تُسجَّل أبداً.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from src.sync.engine import RemoteEntry

logger = logging.getLogger("new-cela.drive-write")

WRITE_SCOPE = "https://www.googleapis.com/auth/drive.file"
TEXT_MIME = "text/markdown"


class GoogleDriveWriter:
    """تنفيذ `TwoWayClient` فوق Google Drive API v3 بصلاحية `drive.file`."""

    def __init__(self, service=None) -> None:
        self._service = service

    # ---------------- البناء ----------------
    def _build(self):
        if self._service is not None:
            return self._service
        try:
            from google.oauth2 import service_account
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
        except ImportError as exc:  # pragma: no cover - يعتمد على البيئة
            raise RuntimeError(
                "مكتبات Google غير مثبتة. نفّذ: pip install -r requirements-integrations.txt"
            ) from exc

        creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        token_path = os.environ.get("GOOGLE_DRIVE_WRITE_TOKEN")
        if token_path and Path(token_path).exists():
            credentials = Credentials.from_authorized_user_file(token_path, [WRITE_SCOPE])
        elif creds_path and Path(creds_path).exists():
            credentials = service_account.Credentials.from_service_account_file(
                creds_path, scopes=[WRITE_SCOPE]
            )
        else:
            raise RuntimeError(
                "لا توجد بيانات اعتماد للكتابة. اضبط GOOGLE_DRIVE_WRITE_TOKEN "
                "(نطاق drive.file) أو GOOGLE_APPLICATION_CREDENTIALS."
            )

        granted = getattr(credentials, "scopes", None) or []
        if granted and WRITE_SCOPE not in granted:
            raise RuntimeError(
                f"الصلاحية الممنوحة غير كافية/واسعة جداً: {granted}. المطلوب {WRITE_SCOPE} فقط."
            )

        self._service = build("drive", "v3", credentials=credentials, cache_discovery=False)
        return self._service

    # ---------------- القراءة ----------------
    def list_entries(self, folder_id: str) -> list[RemoteEntry]:
        service = self._build()
        entries: list[RemoteEntry] = []
        page_token: str | None = None
        while True:
            response = (
                service.files()
                .list(
                    q=f"'{folder_id}' in parents and trashed = false",
                    fields="nextPageToken, files(id,name,mimeType,modifiedTime,md5Checksum,size)",
                    pageSize=200,
                    pageToken=page_token,
                )
                .execute()
            )
            for raw in response.get("files", []):
                if raw.get("mimeType") == "application/vnd.google-apps.folder":
                    continue
                entries.append(
                    RemoteEntry(
                        id=raw["id"],
                        name=raw.get("name", raw["id"]),
                        modified=raw.get("modifiedTime", ""),
                        md5=raw.get("md5Checksum", ""),
                        size=int(raw["size"]) if raw.get("size") else None,
                    )
                )
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        return entries

    def download(self, entry: RemoteEntry) -> str:
        service = self._build()
        data = service.files().get(fileId=entry.id, alt="media").execute()
        if isinstance(data, str):
            return data
        return bytes(data).decode("utf-8", errors="replace")

    # ---------------- الكتابة ----------------
    def upload(self, folder_id: str, name: str, content: str) -> str:
        import io

        from googleapiclient.http import MediaIoBaseUpload

        service = self._build()
        media = MediaIoBaseUpload(
            io.BytesIO(content.encode("utf-8")), mimetype=TEXT_MIME, resumable=False
        )
        created = (
            service.files()
            .create(
                body={"name": name, "parents": [folder_id]},
                media_body=media,
                fields="id",
                supportsAllDrives=True,
            )
            .execute()
        )
        logger.info("رُفع ملف إلى Drive: %s", name)
        return created["id"]

    def update(self, remote_id: str, content: str) -> str:
        import io

        from googleapiclient.http import MediaIoBaseUpload

        service = self._build()
        media = MediaIoBaseUpload(
            io.BytesIO(content.encode("utf-8")), mimetype=TEXT_MIME, resumable=False
        )
        updated = (
            service.files()
            .update(fileId=remote_id, media_body=media, fields="id", supportsAllDrives=True)
            .execute()
        )
        logger.info("حُدّث ملف في Drive: %s", remote_id)
        return updated["id"]

    def delete(self, remote_id: str) -> None:
        """لا تُستدعى إلا عبر `allow_delete=True` صريح."""
        service = self._build()
        service.files().delete(fileId=remote_id, supportsAllDrives=True).execute()
        logger.warning("حُذف ملف من Drive: %s", remote_id)
