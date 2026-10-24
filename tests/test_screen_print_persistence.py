"""Local archive persistence behavior and restrictive file mode."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.screen_print import FabricShade, FilmArchiveStore, FilmSetRecord


def sample_record() -> FilmSetRecord:
    return FilmSetRecord(
        film_id="FILM-001",
        design_name="تصميم تجريبي",
        client_name="",
        garment_type="تيشيرت",
        fabric_shade=FabricShade.DARK,
        colors_count=1,
        color_layers=("أسود",),
        mesh_counts_t=(62,),
        storage_location="درج 1",
    )


def test_archive_write_is_atomic_result_and_restrictive_mode(tmp_path: Path) -> None:
    store = FilmArchiveStore(tmp_path / "archive.jsonl", allowed_root=tmp_path)
    store.add_film_set(sample_record())

    assert store.list_all() == [sample_record()]
    assert list(tmp_path.glob(".archive.jsonl.*.tmp")) == []
    if os.name != "nt":
        assert store.path.stat().st_mode & 0o077 == 0


def test_archive_delete_removes_local_record(tmp_path: Path) -> None:
    store = FilmArchiveStore(tmp_path / "archive.jsonl", allowed_root=tmp_path)
    store.add_film_set(sample_record())

    assert store.delete_film_set("FILM-001") is True
    assert store.list_all() == []
    assert store.delete_film_set("FILM-001") is False


def test_archive_write_error_is_not_silently_swallowed(tmp_path: Path, monkeypatch) -> None:
    store = FilmArchiveStore(tmp_path / "archive.jsonl", allowed_root=tmp_path)

    def fail_replace(*args, **kwargs):
        raise OSError("simulated replace failure")

    monkeypatch.setattr("src.screen_print.engine.os.replace", fail_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        store.add_film_set(sample_record())
    assert list(tmp_path.glob(".archive.jsonl.*.tmp")) == []
