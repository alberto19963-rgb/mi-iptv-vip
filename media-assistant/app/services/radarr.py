from __future__ import annotations

from typing import Any, Optional

import httpx


class RadarrClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""

    def _headers(self) -> dict[str, str]:
        return {"X-Api-Key": self.api_key, "Accept": "application/json"}

    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        with httpx.Client(timeout=30.0) as client:
            r = client.get(f"{self.base_url}{path}", headers=self._headers(), params=params)
            r.raise_for_status()
            return r.json()

    def _post(self, path: str, payload: dict) -> Any:
        with httpx.Client(timeout=45.0) as client:
            r = client.post(f"{self.base_url}{path}", headers=self._headers(), json=payload)
            r.raise_for_status()
            return r.json() if r.content else {}

    def _delete(self, path: str, params: Optional[dict] = None) -> None:
        with httpx.Client(timeout=30.0) as client:
            r = client.delete(f"{self.base_url}{path}", headers=self._headers(), params=params)
            r.raise_for_status()

    def quality_profiles(self) -> list[dict]:
        return self._get("/api/v3/qualityprofile") if self.configured() else []

    def root_folders(self) -> list[dict]:
        return self._get("/api/v3/rootfolder") if self.configured() else []

    def lookup(self, term: str) -> list[dict]:
        return self._get("/api/v3/movie/lookup", {"term": term}) if self.configured() else []

    def lookup_tmdb(self, tmdb_id: int) -> Optional[dict]:
        results = self.lookup(f"tmdb:{tmdb_id}")
        return results[0] if results else None

    def get_movie(self, movie_id: int) -> dict:
        return self._get(f"/api/v3/movie/{movie_id}")

    def get_movies(self) -> list[dict]:
        return self._get("/api/v3/movie") if self.configured() else []

    def find_by_tmdb(self, tmdb_id: int) -> Optional[dict]:
        for m in self.get_movies():
            if m.get("tmdbId") == tmdb_id:
                return m
        return None

    def add_movie(
        self,
        tmdb_id: int,
        quality_profile_id: int,
        root_folder: str,
        *,
        monitored: bool = True,
        search: bool = True,
    ) -> dict:
        existing = self.find_by_tmdb(tmdb_id)
        if existing:
            if search and not existing.get("hasFile"):
                self._post("/api/v3/command", {"name": "MoviesSearch", "movieIds": [existing["id"]]})
            return existing
        lookup = self.lookup_tmdb(tmdb_id)
        if not lookup:
            raise ValueError(f"TMDb {tmdb_id} no encontrado en Radarr")
        payload = {
            **lookup,
            "qualityProfileId": quality_profile_id,
            "rootFolderPath": root_folder,
            "monitored": monitored,
            "minimumAvailability": "released",
            "addOptions": {"searchForMovie": search},
        }
        return self._post("/api/v3/movie", payload)

    def delete_movie(self, movie_id: int, *, delete_files: bool = True) -> None:
        self._delete(f"/api/v3/movie/{movie_id}", {"deleteFiles": str(delete_files).lower(), "addImportExclusion": "false"})

    def queue(self) -> list[dict]:
        data = self._get("/api/v3/queue", {"pageSize": 100}) if self.configured() else {}
        return data.get("records", []) if isinstance(data, dict) else data

    def history(self, page_size: int = 50) -> list[dict]:
        data = self._get("/api/v3/history", {"pageSize": page_size, "eventType": "downloadFolderImported"}) if self.configured() else {}
        return data.get("records", []) if isinstance(data, dict) else []

    def disk_space(self) -> list[dict]:
        return self._get("/api/v3/diskspace") if self.configured() else []
