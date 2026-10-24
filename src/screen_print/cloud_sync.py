"""Supabase email authentication and owner-scoped archive sync over HTTPS REST."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx


class CloudSyncError(RuntimeError):
    """A safe-to-display cloud operation error."""


@dataclass(frozen=True)
class SupabaseConfig:
    url: str
    anon_key: str

    def __post_init__(self) -> None:
        parsed = urlparse(self.url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("Supabase URL must be an HTTPS URL.")
        if not self.anon_key.strip():
            raise ValueError("Supabase anon key is required.")

    @property
    def base_url(self) -> str:
        return self.url.rstrip("/")


class SupabaseArchiveClient:
    """Uses only the public anon key plus the signed-in user's short-lived access token."""

    def __init__(self, config: SupabaseConfig, *, timeout: float = 12.0) -> None:
        self.config = config
        self.timeout = timeout

    def sign_up(self, email: str, password: str) -> dict[str, Any]:
        response = self._request(
            "POST", "/auth/v1/signup", json={"email": email.strip(), "password": password}
        )
        return response

    def sign_in(self, email: str, password: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/auth/v1/token?grant_type=password",
            json={"email": email.strip(), "password": password},
        )

    def sign_out(self, access_token: str) -> None:
        self._request("POST", "/auth/v1/logout", access_token=access_token)

    def pull_records(self, access_token: str) -> list[dict[str, Any]]:
        fields = "film_id,design_name,client_name,garment_type,fabric_shade,colors_count,color_layers,mesh_counts_t,storage_location,width_cm,height_cm,notes"
        records: list[dict[str, Any]] = []
        offset = 0
        page_size = 500
        while True:
            result = self._request(
                "GET",
                f"/rest/v1/film_archive?select={fields}&order=film_id&limit={page_size}&offset={offset}",
                access_token=access_token,
            )
            if not isinstance(result, list):
                raise CloudSyncError("Unexpected response from cloud archive.")
            records.extend(result)
            if len(result) < page_size:
                return records
            offset += page_size

    def push_records(self, access_token: str, user_id: str, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        payload = [{**record, "user_id": user_id} for record in records]
        for start in range(0, len(payload), 100):
            self._request(
                "POST",
                "/rest/v1/film_archive?on_conflict=user_id,film_id",
                access_token=access_token,
                json=payload[start : start + 100],
                prefer="resolution=ignore-duplicates,return=minimal",
            )

    def _request(
        self,
        method: str,
        path: str,
        *,
        access_token: str | None = None,
        json: Any = None,
        prefer: str | None = None,
    ) -> Any:
        headers = {"apikey": self.config.anon_key, "Accept": "application/json"}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        if prefer:
            headers["Prefer"] = prefer
        try:
            response = httpx.request(
                method,
                f"{self.config.base_url}{path}",
                headers=headers,
                json=json,
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise CloudSyncError("Cloud service could not be reached; local data is unchanged.") from exc
        if response.is_error:
            # Do not leak raw provider responses, tokens, or email details into logs/UI.
            if response.status_code in (401, 403):
                message = "Authentication failed or access was denied. Sign in again and check cloud policies."
            elif response.status_code == 429:
                message = "Cloud rate limit reached; wait briefly and retry."
            else:
                message = f"Cloud request failed (HTTP {response.status_code}); local data is unchanged."
            raise CloudSyncError(message)
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise CloudSyncError("Cloud service returned an invalid response.") from exc
