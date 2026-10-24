"""Run a local real-file ingestion matrix; sample files must remain untracked."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.core.path_guard import UnsafePath
from src.ingestion.pipeline import IngestionLimits, IngestionPipeline

DEFAULT_SAMPLES_DIR = REPO_ROOT / ".local" / "file-ingestion-samples"
MAX_MANIFEST_BYTES = 1_000_000
MAX_CASES = 200


def load_manifest(samples_dir: Path) -> list[dict[str, Any]]:
    """Load and minimally validate the local JSON manifest."""
    manifest_path = samples_dir / "manifest.json"
    raw = manifest_path.read_bytes()
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError("manifest_too_large")
    decoded = json.loads(raw)
    cases = decoded.get("files") if isinstance(decoded, dict) else None
    if not isinstance(cases, list) or len(cases) > MAX_CASES:
        raise ValueError("manifest_files_must_be_list_with_at_most_200_cases")
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValueError(f"manifest_case_{index}_must_be_object")  # noqa: TRY004
        name = case.get("file")
        kind = case.get("kind")
        if (
            not isinstance(name, str)
            or not name
            or Path(name).is_absolute()
            or ".." in name.replace("\\", "/").split("/")
        ):
            raise ValueError(f"manifest_case_{index}_has_unsafe_file_path")
        if kind not in {"legit", "negative"}:
            raise ValueError(f"manifest_case_{index}_kind_must_be_legit_or_negative")
        mime = case.get("expected_mime")
        if mime is not None and not isinstance(mime, str):
            raise ValueError(f"manifest_case_{index}_expected_mime_must_be_string")
    return cases


def _case_result(case: dict[str, Any], root: Path, pipeline: IngestionPipeline) -> dict[str, Any]:
    relative_name = case["file"]
    sample_path = root / relative_name
    expected_mime = case.get("expected_mime")
    expected = "extracted or partial-with-reason" if case["kind"] == "legit" else "blocked"
    started = time.monotonic()
    actual = "error"
    detected_mime: str | None = None
    warnings: list[str] = []
    error: str | None = None
    if not sample_path.exists() and not sample_path.is_symlink():
        error = "sample_missing"
    else:
        try:
            result, _from_cache = pipeline.process(sample_path, allowed_root=root)
            actual = result.status
            detected_mime = result.mime
            warnings = result.warnings
        except (UnsafePath, ValueError) as exc:
            message = str(exc)
            guarded_rejection = isinstance(exc, UnsafePath) or any(
                marker in message
                for marker in ("outside_allowed_root", "allowed_root_symlink_rejected", "path_not_regular_or_symlink")
            )
            if guarded_rejection:
                actual = "blocked"
                warnings = [f"unsafe_path: {message}"]
            else:
                error = f"{type(exc).__name__}: {message}"
        except Exception as exc:  # noqa: BLE001 — preserve a failed row instead of aborting the matrix.
            error = f"{type(exc).__name__}: {exc}"
    elapsed = round(time.monotonic() - started, 3)

    if case["kind"] == "negative":
        passed = actual == "blocked"
    else:
        passed = actual == "extracted" or (actual == "partial" and bool(warnings))
    if expected_mime is not None and detected_mime != expected_mime:
        passed = False
    if error is not None:
        passed = False
    return {
        "file": Path(relative_name).name,
        "detected_mime": detected_mime,
        "expected": expected,
        "actual": actual,
        "elapsed_s": elapsed,
        "warnings": warnings,
        "pass": passed,
        "slow_flag": elapsed > 5,
        **({"error": error} if error is not None else {}),
    }


def run_matrix(samples_dir: Path) -> dict[str, Any]:
    """Run all manifest cases through the production subprocess-isolated pipeline."""
    root = samples_dir.expanduser().absolute()
    if root.is_symlink():
        raise ValueError("samples_directory_must_not_be_symlink")
    if not root.is_dir():
        raise ValueError("samples_directory_missing")
    cases = load_manifest(root)
    pipeline = IngestionPipeline(limits=IngestionLimits(isolate_in_subprocess=True))
    rows = [_case_result(case, root, pipeline) for case in cases]
    return {
        "samples_dir": root.name,
        "rules": {
            "legit_files": "extracted or partial with at least one warning",
            "negatives": "blocked",
            "slow_flag": "elapsed_s > 5",
            "fail_flag": "legit blocked/unexplained partial OR negative not blocked OR MIME mismatch",
        },
        "passed": sum(row["pass"] for row in rows),
        "failed": sum(not row["pass"] for row in rows),
        "rows": rows,
    }


def render_table(report: dict[str, Any]) -> str:
    columns = ("file", "detected_mime", "expected", "actual", "elapsed_s", "warnings", "pass", "slow_flag")
    lines = ["| " + " | ".join(columns) + " |", "|" + "|".join("---" for _ in columns) + "|"]
    for row in report["rows"]:
        values = []
        for column in columns:
            value = row.get(column, "")
            if isinstance(value, list):
                value = "; ".join(str(item) for item in value)
            values.append(str(value).replace("|", "\\|").replace("\n", " "))
        lines.append("| " + " | ".join(values) + " |")
    lines.append(f"\n**Result:** {report['passed']} passed, {report['failed']} failed.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples-dir", type=Path, default=DEFAULT_SAMPLES_DIR)
    parser.add_argument("--format", choices=("table", "json"), default="table")
    parser.add_argument("--output", type=Path, help="Write the report to a local path instead of stdout.")
    args = parser.parse_args(argv)
    try:
        report = run_matrix(args.samples_dir)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"matrix_setup_failed: {type(exc).__name__}: {exc}") from exc
    rendered = json.dumps(report, ensure_ascii=False, indent=2) if args.format == "json" else render_table(report)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
