"""اختبارات مزامنة Drive — بعميل وهمي، بلا شبكة ولا بيانات اعتماد."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.integrations.drive import (
    GOOGLE_DOC,
    DriveItem,
    SyncReport,
    build_parser,
    ingest_synced,
    load_manifest,
    safe_name,
    save_manifest,
    sync_folder,
)
from src.knowledge.corpus import CorpusIndex


class FakeClient:
    """عميل Drive وهمي — يُمثّل مصدر الحقيقة القابل للتغيير في الاختبار."""

    def __init__(self, items: dict[str, tuple[DriveItem, str]]) -> None:
        self.items = items
        self.reads: list[str] = []
        self.fail_on: set[str] = set()

    def list_folder(self, folder_id: str) -> list[DriveItem]:
        return sorted((item for item, _ in self.items.values()), key=lambda i: i.name)

    def read_text(self, item: DriveItem, max_bytes: int = 2_000_000) -> str:
        self.reads.append(item.id)
        if item.id in self.fail_on:
            raise RuntimeError("تعذّر التنزيل")
        return self.items[item.id][1][:max_bytes]


def doc(id_: str, name: str, text: str, modified: str = "2026-01-01T00:00:00Z") -> tuple[DriveItem, str]:
    return (
        DriveItem(id=id_, name=name, mime_type=GOOGLE_DOC, modified_time=modified),
        text,
    )


def test_safe_name_blocks_path_traversal() -> None:
    item = DriveItem(id="abc12345xyz", name="../../../etc/passwd", mime_type=GOOGLE_DOC)
    name = safe_name(item)
    assert "/" not in name
    assert ".." not in name
    assert name.endswith(".md")


def test_safe_name_keeps_arabic_and_is_unique() -> None:
    one = safe_name(DriveItem(id="aaaaaaaa1", name="ملاحظات هامة", mime_type=GOOGLE_DOC))
    two = safe_name(DriveItem(id="bbbbbbbb2", name="ملاحظات هامة", mime_type=GOOGLE_DOC))
    assert one != two
    assert "ملاحظات" in one


def test_sync_first_run_adds_files(tmp_path: Path) -> None:
    client = FakeClient({"1": doc("1", "plan", "خطة المشروع\n\nتفاصيل")})
    report = sync_folder(client, "folder", tmp_path / "drive")

    assert report.added and not report.updated
    assert (tmp_path / "drive" / report.added[0]).exists()
    assert report.total_written == 1


def test_sync_is_incremental(tmp_path: Path) -> None:
    client = FakeClient({"1": doc("1", "plan", "نص")})
    sync_folder(client, "folder", tmp_path / "drive")
    client.reads.clear()

    report = sync_folder(client, "folder", tmp_path / "drive")
    assert report.unchanged == 1
    assert report.added == []
    assert client.reads == []  # لم يُنزَّل شيء مرتين


def test_sync_updates_when_modified_time_changes(tmp_path: Path) -> None:
    client = FakeClient({"1": doc("1", "plan", "نسخة أولى")})
    first = sync_folder(client, "folder", tmp_path / "drive")
    name = first.added[0]

    client.items["1"] = doc("1", "plan", "نسخة ثانية", modified="2026-02-02T00:00:00Z")
    report = sync_folder(client, "folder", tmp_path / "drive")

    assert report.updated == [name]
    assert "ثانية" in (tmp_path / "drive" / name).read_text(encoding="utf-8")


def test_sync_reports_unsupported_types(tmp_path: Path) -> None:
    unsupported = DriveItem(id="9", name="photo.png", mime_type="image/png")
    client = FakeClient({"9": (unsupported, "binary")})
    report = sync_folder(client, "folder", tmp_path / "drive")
    assert report.unsupported
    assert report.added == []


def test_sync_continues_after_single_file_failure(tmp_path: Path) -> None:
    client = FakeClient(
        {
            "1": doc("1", "good", "محتوى سليم"),
            "2": doc("2", "bad", "لن يُقرأ"),
        }
    )
    client.fail_on = {"2"}
    report = sync_folder(client, "folder", tmp_path / "drive")

    assert report.added
    assert "bad" in report.failed
    assert report.summary().startswith("مزامنة Drive")


def test_sync_list_failure_is_reported_not_raised(tmp_path: Path) -> None:
    class Broken(FakeClient):
        def list_folder(self, folder_id: str):
            raise RuntimeError("شبكة مقطوعة")

    report = sync_folder(Broken({}), "folder", tmp_path / "drive")
    assert "<list>" in report.failed
    assert report.added == []


def test_prune_removes_only_vanished_drive_files(tmp_path: Path) -> None:
    client = FakeClient({"1": doc("1", "a", "أ"), "2": doc("2", "b", "ب")})
    dest = tmp_path / "drive"
    first = sync_folder(client, "folder", dest)
    assert len(first.added) == 2

    (dest / "local-note.md").write_text("ملف محلي", encoding="utf-8")
    removed_item = next(item for item, _ in client.items.values() if item.name == "b")
    del client.items[removed_item.id]

    report = sync_folder(client, "folder", dest, prune=True)
    assert len(report.removed) == 1
    assert (dest / "local-note.md").exists()  # المحلي لا يُمس


def test_manifest_persists_metadata(tmp_path: Path) -> None:
    client = FakeClient({"1": doc("1", "plan", "نص")})
    dest = tmp_path / "drive"
    sync_folder(client, "folder", dest)

    manifest = load_manifest(dest / "_manifest.json")
    assert manifest["folder_id"] == "folder"
    record = manifest["files"]["1"]
    assert record["name"] == "plan"
    assert len(record["sha256"]) == 16
    assert record["chars"] == len("نص")


def test_manifest_corrupt_file_is_recovered(tmp_path: Path) -> None:
    store = tmp_path / "m.json"
    store.write_text("{ غير صالح", encoding="utf-8")
    assert load_manifest(store) == {"files": {}}

    save_manifest(store, {"files": {}, "folder_id": "f"})
    assert json.loads(store.read_text(encoding="utf-8"))["folder_id"] == "f"


def test_ingest_synced_feeds_corpus(tmp_path: Path) -> None:
    client = FakeClient({"1": doc("1", "arch", "المعمارية الموحدة\n\nثلاث عقول")})
    dest = tmp_path / "drive"
    report = sync_folder(client, "folder", dest)

    corpus = CorpusIndex()
    added = ingest_synced(report, corpus)
    assert added > 0
    assert any(source.startswith("drive:") for source in corpus.sources())


def test_cli_parser_requires_folder_id() -> None:
    parser = build_parser()
    args = parser.parse_args(["sync", "--folder-id", "abc", "--dest", "x"])
    assert args.folder_id == "abc"
    assert args.prune is False

    with pytest.raises(SystemExit):
        parser.parse_args(["sync"])


def test_report_summary_counts() -> None:
    report = SyncReport(
        folder_id="f",
        dest="d",
        added=["a"],
        updated=["b", "c"],
        unchanged=4,
        removed=["z"],
        failed={"x": "خطأ"},
        unsupported=["img.png"],
    )
    text = report.summary()
    assert "+1" in text and "~2" in text and "=4" in text and "-1" in text
    assert report.total_written == 3
