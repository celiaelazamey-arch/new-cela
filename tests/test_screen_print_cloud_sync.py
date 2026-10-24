from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from src.screen_print.cloud_sync import CloudSyncError, SupabaseArchiveClient, SupabaseConfig


def make_client() -> SupabaseArchiveClient:
    return SupabaseArchiveClient(SupabaseConfig("https://example.supabase.co", "public-anon-key"))


def test_config_rejects_non_https_and_empty_key() -> None:
    with pytest.raises(ValueError):
        SupabaseConfig("http://example.supabase.co", "key")
    with pytest.raises(ValueError):
        SupabaseConfig("https://example.supabase.co", " ")


def test_signin_sends_public_key_and_does_not_log_password(monkeypatch) -> None:
    captured = {}

    def fake_request(method, url, **kwargs):
        captured.update(method=method, url=url, **kwargs)
        return httpx.Response(200, json={"access_token": "access", "user": {"id": "u1"}})

    monkeypatch.setattr(httpx, "request", fake_request)
    result = make_client().sign_in(" sample-user [at] invalid [dot] test ", "secret-pass")
    assert result["access_token"] == "access"
    assert captured["url"].endswith("/auth/v1/token?grant_type=password")
    assert captured["headers"]["apikey"] == "public-anon-key"
    assert captured["json"] == {"email": "sample-user [at] invalid [dot] test", "password": "secret-pass"}


def test_push_uses_user_jwt_and_user_scoped_upsert(monkeypatch) -> None:
    captured = {}

    def fake_request(method, url, **kwargs):
        captured.update(method=method, url=url, **kwargs)
        return httpx.Response(204)

    monkeypatch.setattr(httpx, "request", fake_request)
    make_client().push_records("user-jwt", "user-id", [{"film_id": "F-1", "design_name": "Flower"}])
    assert captured["url"].endswith("on_conflict=user_id,film_id")
    assert captured["headers"]["Authorization"] == "Bearer user-jwt"
    assert captured["json"] == [{"film_id": "F-1", "design_name": "Flower", "user_id": "user-id"}]


def test_http_failure_is_sanitized(monkeypatch) -> None:
    def fake_request(*args, **kwargs):
        return httpx.Response(401, text="private auth details")

    monkeypatch.setattr(httpx, "request", fake_request)
    with pytest.raises(CloudSyncError) as err:
        make_client().sign_in("sample-user [at] invalid [dot] test", "secret-pass")
    assert "private auth details" not in str(err.value)
    assert "secret-pass" not in str(err.value)


def test_sync_adds_one_sided_records_and_refuses_conflicts(tmp_path: Path, monkeypatch) -> None:
    from src.screen_print import FabricShade, FilmArchiveStore, FilmSetRecord, app

    store = FilmArchiveStore(tmp_path / "archive.jsonl", allowed_root=tmp_path)
    store.add_film_set(FilmSetRecord(
        film_id="local", design_name="Local", client_name="", garment_type="تيشيرت",
        fabric_shade=FabricShade.DARK, colors_count=1, color_layers=(), mesh_counts_t=(),
        storage_location="", notes="",
    ))
    monkeypatch.setattr(app, "ARCHIVE_PATH", tmp_path / "archive.jsonl")

    class FakeClient:
        def __init__(self):
            self.pushed = []
            self.rows = [{
                "film_id": "remote", "design_name": "Remote", "client_name": "",
                "garment_type": "تيشيرت", "fabric_shade": "dark", "colors_count": 1,
                "color_layers": [], "mesh_counts_t": [], "storage_location": "",
                "width_cm": 28, "height_cm": 38, "notes": "",
            }]

        def pull_records(self, token):
            return list(self.rows)

        def push_records(self, token, user_id, records):
            self.pushed.extend(records)
            self.rows.extend({**record, "user_id": user_id} for record in records)

    fake = FakeClient()
    uploaded, downloaded, conflicts = app._sync_archive(fake, "jwt", "uid")
    assert (uploaded, downloaded, conflicts) == (1, 1, [])
    assert {record.film_id for record in store.list_all()} == {"local", "remote"}
    assert len(fake.pushed) == 1

    fake.rows[0] = {**fake.rows[0], "design_name": "Cloud edit"}
    uploaded, downloaded, conflicts = app._sync_archive(fake, "jwt", "uid")
    assert (uploaded, downloaded) == (0, 0)
    assert conflicts == ["remote"]
    assert json.loads(store.path.read_text().splitlines()[1])["design_name"] == "Remote"
