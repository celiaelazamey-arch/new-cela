"""Smoke and local archive-flow tests for the Streamlit front end."""
from __future__ import annotations

import json
from pathlib import Path

from streamlit.testing.v1 import AppTest


def app_test() -> AppTest:
    app_path = Path(__file__).resolve().parents[1] / "src/screen_print/app.py"
    return AppTest.from_file(app_path).run(timeout=30)


def test_local_screen_print_app_renders_without_exceptions(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SCREEN_PRINT_DATA_DIR", str(tmp_path / "screen-print-data"))
    app = app_test()

    assert not app.exception
    assert len(app.tabs) == 3
    assert (tmp_path / "screen-print-data").exists() is False


def test_local_app_can_add_search_and_delete_archive_record(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "screen-print-data"
    monkeypatch.setenv("SCREEN_PRINT_DATA_DIR", str(data_dir))
    app = app_test()

    app.text_input[1].set_value("FLM-LOCAL-01")
    app.text_input[2].set_value("تصميم تجريبي")
    app.form_submit_button[0].click().run(timeout=30)

    archive_path = data_dir / "film_archive.jsonl"
    saved = json.loads(archive_path.read_text(encoding="utf-8"))
    assert saved["film_id"] == "FLM-LOCAL-01"
    assert any("حُفظت البطاقة محلياً" in item.value for item in app.success)

    app.text_input[0].set_value("FLM-LOCAL-01").run()
    assert len(app.dataframe) == 1
    app.checkbox[0].check()
    app.form_submit_button[0].click().run(timeout=30)

    assert archive_path.read_text(encoding="utf-8") == ""
    assert any("حُذفت بطاقة الفيلم" in item.value for item in app.success)
    assert not app.exception
