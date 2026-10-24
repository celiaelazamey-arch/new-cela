from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.run_ingestion_matrix import render_table, run_matrix
from src.ingestion.pipeline import EICAR_SIGNATURE


def _manifest(root: Path, cases: list[dict[str, object]]) -> None:
    (root / "manifest.json").write_text(json.dumps({"files": cases}), encoding="utf-8")


def test_matrix_runner_executes_real_subprocess_and_reports_rows(tmp_path: Path) -> None:
    (tmp_path / "legit.txt").write_text("Arabic order: طلبية طباعة", encoding="utf-8")
    (tmp_path / "eicar.com").write_bytes(EICAR_SIGNATURE)
    _manifest(
        tmp_path,
        [
            {"file": "legit.txt", "kind": "legit", "expected_mime": "text/plain"},
            {"file": "eicar.com", "kind": "negative"},
        ],
    )

    report = run_matrix(tmp_path)

    assert report["passed"] == 2
    assert report["failed"] == 0
    legit, negative = report["rows"]
    assert legit["actual"] == "extracted"
    assert legit["detected_mime"] == "text/plain"
    assert negative["actual"] == "blocked"
    assert render_table(report).startswith("| file | detected_mime | expected | actual |")


def test_matrix_runner_classifies_unsafe_symlink_as_blocked(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text("must not be read", encoding="utf-8")
    link = tmp_path / "external.txt"
    try:
        link.symlink_to(outside)
    except (NotImplementedError, OSError) as exc:
        return pytest.skip(f"symlinks unavailable on this platform: {exc}")
    _manifest(tmp_path, [{"file": "external.txt", "kind": "negative"}])

    report = run_matrix(tmp_path)

    assert report["passed"] == 1
    row = report["rows"][0]
    assert row["actual"] == "blocked"
    assert row["warnings"][0].startswith("unsafe_path:")


def test_matrix_manifest_rejects_traversal_and_bounds_cases(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text(
        json.dumps({"files": [{"file": "../secret.txt", "kind": "negative"}]}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unsafe_file_path"):
        run_matrix(tmp_path)


def test_matrix_runner_flags_missing_samples(tmp_path: Path) -> None:
    _manifest(tmp_path, [{"file": "missing.docx", "kind": "legit"}])
    report = run_matrix(tmp_path)
    assert report["failed"] == 1
    assert report["rows"][0]["error"] == "sample_missing"
