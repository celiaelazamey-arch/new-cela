"""اختبارات المزامنة ثنائية الاتجاه — التعارض، الموافقة، والرفض الافتراضي."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.sync.engine import (
    Action,
    RemoteEntry,
    apply_plan,
    build_parser,
    deny_all,
    load_base,
    md5_of_bytes,
    plan_sync,
    save_base,
    scan_local,
)


class FakeRemote:
    """Drive وهمي كامل — يخزّن المحتوى فعلاً كي تُقرأ النتائج لاحقاً."""

    def __init__(self, files: dict[str, tuple[str, str]] | None = None) -> None:
        # name -> (id, content)
        self.files = dict(files or {})
        self.uploads = 0
        self.updates = 0
        self.deleted: list[str] = []

    def _entry(self, name: str) -> RemoteEntry:
        id_, content = self.files[name]
        return RemoteEntry(id=id_, name=name, md5=md5_of_bytes(content.encode()), modified="")

    def list_folder(self, folder_id: str) -> list[RemoteEntry]:
        return [self._entry(name) for name in sorted(self.files)]

    def download(self, entry: RemoteEntry) -> str:
        return self.files[entry.name][1]

    def upload(self, folder_id: str, name: str, content: str) -> str:
        self.uploads += 1
        new_id = f"new{self.uploads}"
        self.files[name] = (new_id, content)
        return new_id

    def update(self, remote_id: str, content: str) -> str:
        self.updates += 1
        for name, (id_, _) in list(self.files.items()):
            if id_ == remote_id:
                self.files[name] = (id_, content)
                return id_
        raise KeyError(remote_id)

    def delete(self, remote_id: str) -> None:
        self.deleted.append(remote_id)


@pytest.fixture()
def workdir(tmp_path: Path) -> Path:
    target = tmp_path / "shared"
    target.mkdir()
    (target / "notes.md").write_text("نص محلي", encoding="utf-8")
    return target


def base_path(workdir: Path) -> Path:
    return workdir / "_sync_state.json"


def test_scan_local_only_text_files(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("أ", encoding="utf-8")
    (tmp_path / "b.txt").write_text("ب", encoding="utf-8")
    (tmp_path / "c.png").write_bytes(b"\x89PNG")
    found = scan_local(tmp_path)
    assert set(found) == {"a.md", "b.txt"}
    assert found["a.md"].md5 == md5_of_bytes("أ".encode())


def test_plan_proposes_upload_for_new_local(workdir: Path) -> None:
    plan = plan_sync("f", workdir, remotes=[], base={})
    assert [a.op for a in plan.actions] == ["upload"]
    assert plan.writes and not plan.conflicts
    assert "جديد" in plan.actions[0].reason


def test_plan_is_read_only(workdir: Path) -> None:
    before = (workdir / "notes.md").read_text(encoding="utf-8")
    plan_sync("f", workdir, remotes=[RemoteEntry(id="r1", name="notes.md")], base={})
    assert (workdir / "notes.md").read_text(encoding="utf-8") == before
    assert not any(path.name.startswith("_sync_state") for path in workdir.iterdir())


def test_plan_marks_unchanged_as_skip(workdir: Path) -> None:
    content = (workdir / "notes.md").read_text(encoding="utf-8")
    remote = RemoteEntry(id="r1", name="notes.md", md5=md5_of_bytes(content.encode()))
    base = {"notes.md": {"remote_id": "r1",
                         "md5": md5_of_bytes(content.encode()),
                         "remote_md5": md5_of_bytes(content.encode())}}
    plan = plan_sync("f", workdir, remotes=[remote], base=base)
    assert [a.op for a in plan.actions] == ["skip"]


def test_plan_detects_local_change(workdir: Path) -> None:
    content = "النسخة القديمة"
    remote = RemoteEntry(id="r1", name="notes.md", md5=md5_of_bytes(content.encode()))
    base = {"notes.md": {"remote_id": "r1",
                         "md5": md5_of_bytes(content.encode()),
                         "remote_md5": md5_of_bytes(content.encode())}}
    plan = plan_sync("f", workdir, remotes=[remote], base=base)
    assert [a.op for a in plan.actions] == ["update"]


def test_plan_detects_remote_change(workdir: Path) -> None:
    local_md5 = md5_of_bytes((workdir / "notes.md").read_bytes())
    remote = RemoteEntry(id="r1", name="notes.md", md5="ffffchanged")
    base = {"notes.md": {"remote_id": "r1", "md5": local_md5, "remote_md5": "older"}}
    plan = plan_sync("f", workdir, remotes=[remote], base=base)
    assert [a.op for a in plan.actions] == ["download"]


def test_plan_reports_conflict_by_default(workdir: Path) -> None:
    remote = RemoteEntry(id="r1", name="notes.md", md5="remote-new")
    base = {"notes.md": {"remote_id": "r1", "md5": "local-old", "remote_md5": "remote-old"}}
    plan = plan_sync("f", workdir, remotes=[remote], base=base)
    assert [a.op for a in plan.actions] == ["conflict"]
    assert plan.conflicts
    assert "يدوياً" in plan.conflicts[0].reason


def test_plan_newest_policy_resolves_conflict(workdir: Path) -> None:
    remote = RemoteEntry(id="r1", name="notes.md", md5="remote-new",
                         modified="2999-01-01T00:00:00Z")
    base = {"notes.md": {"remote_id": "r1", "md5": "local-old", "remote_md5": "remote-old"}}
    plan = plan_sync("f", workdir, remotes=[remote], base=base, conflict_policy="newest")
    assert [a.op for a in plan.actions] == ["download"]
    assert "الأحدث" in plan.actions[0].reason


def test_plan_newest_prefers_newer_local(tmp_path: Path) -> None:
    target = tmp_path / "s"
    target.mkdir()
    (target / "notes.md").write_text("محلي", encoding="utf-8")
    remote = RemoteEntry(id="r1", name="notes.md", md5="remote-new",
                         modified="2000-01-01T00:00:00Z")
    base = {"notes.md": {"remote_id": "r1", "md5": "local-old", "remote_md5": "remote-old"}}
    plan = plan_sync("f", target, remotes=[remote], base=base, conflict_policy="newest")
    assert [a.op for a in plan.actions] == ["update"]


def test_plan_downloads_remote_only_files(workdir: Path) -> None:
    remote = RemoteEntry(id="r9", name="remote-only.md", md5="abc")
    plan = plan_sync("f", workdir, remotes=[remote], base={})
    downloads = [a for a in plan.actions if a.op == "download"]
    assert downloads and downloads[0].name == "remote-only.md"


def test_plan_no_download_skips_remote_only(workdir: Path) -> None:
    remote = RemoteEntry(id="r9", name="remote-only.md", md5="abc")
    plan = plan_sync("f", workdir, remotes=[remote], base={}, allow_download=False)
    assert not [a for a in plan.actions if a.op == "download"]
    skipped = [a for a in plan.actions if a.op == "skip"]
    assert skipped and skipped[0].name == "remote-only.md"


def test_apply_denied_by_default(workdir: Path) -> None:
    remote = FakeRemote()
    plan = plan_sync("f", workdir, remotes=[], base={})
    report = apply_plan(plan, remote, base={}, base_path=base_path(workdir))

    assert report.applied == []
    assert len(report.denied) == 1
    assert remote.uploads == 0
    assert "رُفض 1" in report.summary()


def test_apply_uploads_after_approval(workdir: Path) -> None:
    remote = FakeRemote()
    plan = plan_sync("f", workdir, remotes=[], base={})
    report = apply_plan(plan, remote, base={}, base_path=base_path(workdir),
                        approve=lambda action: True)

    assert remote.uploads == 1
    assert remote.files["notes.md"][1] == "نص محلي"
    assert report.applied[0].op == "upload"
    assert load_base(base_path(workdir))["notes.md"]["remote_id"] == "new1"


def test_apply_updates_remote(workdir: Path) -> None:
    remote = FakeRemote({"notes.md": ("r1", "قديم")})
    base = {"notes.md": {"remote_id": "r1",
                         "md5": md5_of_bytes("قديم".encode()),
                         "remote_md5": md5_of_bytes("قديم".encode())}}
    plan = plan_sync("f", workdir, remotes=remote.list_folder("f"), base=base)
    assert [a.op for a in plan.actions] == ["update"]

    apply_plan(plan, remote, base=base, base_path=base_path(workdir),
               approve=lambda action: True)
    assert remote.files["notes.md"][1] == "نص محلي"
    assert remote.updates == 1


def test_apply_downloads_remote_only(workdir: Path) -> None:
    remote = FakeRemote({"remote.md": ("r9", "محتوى بعيد")})
    plan = plan_sync("f", workdir, remotes=remote.list_folder("f"), base={})
    apply_plan(plan, remote, base={}, base_path=base_path(workdir),
               approve=lambda action: True)

    assert (workdir / "remote.md").read_text(encoding="utf-8") == "محتوى بعيد"
    assert load_base(base_path(workdir))["remote.md"]["remote_id"] == "r9"


def test_apply_requires_approval_per_action(workdir: Path) -> None:
    (workdir / "second.md").write_text("ثانٍ", encoding="utf-8")
    remote = FakeRemote()
    plan = plan_sync("f", workdir, remotes=[], base={})
    seen: list[str] = []

    def approve(action: Action) -> bool:
        seen.append(action.name)
        return action.name == "notes.md"

    report = apply_plan(plan, remote, base={}, base_path=base_path(workdir), approve=approve)
    assert sorted(seen) == ["notes.md", "second.md"]
    assert len(report.applied) == 1
    assert len(report.denied) == 1


def test_apply_survives_single_failure(workdir: Path) -> None:
    class Flaky(FakeRemote):
        def upload(self, folder_id: str, name: str, content: str) -> str:
            raise RuntimeError("حصة مرفوعة")

    remote = Flaky()
    plan = plan_sync("f", workdir, remotes=[], base={})
    report = apply_plan(plan, remote, base={}, base_path=base_path(workdir),
                        approve=lambda action: True)
    assert "notes.md" in report.failed
    assert report.applied == []


def test_apply_does_not_record_denied_in_base(workdir: Path) -> None:
    plan = plan_sync("f", workdir, remotes=[], base={})
    apply_plan(plan, FakeRemote(), base={}, base_path=base_path(workdir))
    assert load_base(base_path(workdir)) == {}


def test_delete_requires_explicit_flag(workdir: Path) -> None:
    remote = FakeRemote()
    plan = plan_sync("f", workdir, remotes=[], base={})
    plan.actions.append(Action(op="update", name="x", reason="اختبار", remote_id="r1"))
    report = apply_plan(plan, remote, base={}, base_path=base_path(workdir),
                        approve=lambda action: True, allow_delete=False)
    assert remote.deleted == []
    assert report.failed.get("x")


def test_base_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    save_base(path, {"a.md": {"remote_id": "r1", "md5": "m", "remote_md5": "m"}}, "folder")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["folder_id"] == "folder"
    assert load_base(path)["a.md"]["remote_id"] == "r1"


def test_corrupt_base_recovers(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("ليس JSON", encoding="utf-8")
    assert load_base(path) == {}


def test_deny_all_is_default() -> None:
    assert deny_all(Action(op="upload", name="x", reason="")) is False


def test_summary_counts_operations(workdir: Path) -> None:
    remote = RemoteEntry(id="r9", name="remote-only.md", md5="abc")
    plan = plan_sync("f", workdir, remotes=[remote], base={})
    text = plan.summary()
    assert "upload=1" in text and "download=1" in text


def test_cli_parser_flags() -> None:
    parser = build_parser()
    plan_args = parser.parse_args(["plan", "--folder-id", "f", "--dir", "d"])
    assert plan_args.command == "plan"
    assert plan_args.conflict_policy == "manual"

    apply_args = parser.parse_args(
        ["apply", "--folder-id", "f", "--dir", "d", "--yes", "--conflict-policy", "newest"]
    )
    assert apply_args.yes and apply_args.conflict_policy == "newest"
    assert apply_args.allow_delete is False

    with pytest.raises(SystemExit):
        parser.parse_args(["plan"])
