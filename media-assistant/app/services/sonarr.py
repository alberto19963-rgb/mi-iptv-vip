from __future__ import annotations

from typing import Any, Optional

import httpx


class SonarrClient:
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
        return self._get("/api/v3/series/lookup", {"term": term}) if self.configured() else []

    def lookup_tmdb(self, tmdb_id: int) -> Optional[dict]:
        results = self.lookup(f"tmdb:{tmdb_id}")
        return results[0] if results else None

    def get_series_list(self) -> list[dict]:
        return self._get("/api/v3/series") if self.configured() else []

    def find_by_tmdb(self, tmdb_id: int) -> Optional[dict]:
        for s in self.get_series_list():
            if s.get("tmdbId") == tmdb_id:
                return s
        return None

    def add_series(
        self,
        tmdb_id: int,
        quality_profile_id: int,
        root_folder: str,
        *,
        season_number: int = 1,
        monitored: bool = True,
        search: bool = True,
    ) -> dict:
        existing = self.find_by_tmdb(tmdb_id)
        if existing:
            sid = existing["id"]
            # monitor season and search
            seasons = existing.get("seasons") or []
            for season in seasons:
                if season.get("seasonNumber") == season_number:
                    season["monitored"] = True
            self._post(f"/api/v3/series/{sid}", {**existing, "monitored": True, "seasons": seasons}) if False else None
            # Use PUT via post workaround — Sonarr expects PUT
            with httpx.Client(timeout=45.0) as client:
                client.put(
                    f"{self.base_url}/api/v3/series/{sid}",
                    headers=self._headers(),
                    json={**existing, "monitored": True, "seasons": seasons},
                ).raise_for_status()
            if search:
                self._post("/api/v3/command", {"name": "SeasonSearch", "seriesId": sid, "seasonNumber": season_number})
            return existing

        lookup = self.lookup_tmdb(tmdb_id)
        if not lookup:
            raise ValueError(f"TMDb {tmdb_id} no encontrado en Sonarr")
        seasons = []
        for season in lookup.get("seasons") or []:
            sn = season.get("seasonNumber", 0)
            seasons.append({**season, "monitored": sn == season_number})
        payload = {
            **lookup,
            "qualityProfileId": quality_profile_id,
            "rootFolderPath": root_folder,
            "monitored": monitored,
            "seasonFolder": True,
            "seriesType": lookup.get("seriesType") or "standard",
            "seasons": seasons,
            "addOptions": {
                "searchForMissingEpisodes": search,
                "monitor": "firstSeason" if season_number == 1 else "existing",
            },
        }
        return self._post("/api/v3/series", payload)

    def delete_series(self, series_id: int, *, delete_files: bool = True) -> None:
        self._delete(
            f"/api/v3/series/{series_id}",
            {"deleteFiles": str(delete_files).lower(), "addImportExclusion": "false"},
        )

    def delete_episode_file(self, episode_file_id: int) -> None:
        self._delete(f"/api/v3/episodefile/{episode_file_id}")

    def episodes(self, series_id: int) -> list[dict]:
        return self._get("/api/v3/episode", {"seriesId": series_id}) if self.configured() else []

    def monitor_episodes(self, episode_ids: list[int], *, monitored: bool = True) -> None:
        if not episode_ids or not self.configured():
            return
        with httpx.Client(timeout=30.0) as client:
            r = client.put(
                f"{self.base_url}/api/v3/episode/monitor",
                headers=self._headers(),
                json={"episodeIds": episode_ids, "monitored": monitored},
            )
            r.raise_for_status()

    def search_episode(self, episode_ids: list[int]) -> dict:
        if not episode_ids:
            return {}
        return self._post("/api/v3/command", {"name": "EpisodeSearch", "episodeIds": episode_ids})

    def search_season(self, series_id: int, season_number: int) -> dict:
        return self._post(
            "/api/v3/command",
            {"name": "SeasonSearch", "seriesId": series_id, "seasonNumber": season_number},
        )

    def queue(self) -> list[dict]:
        data = self._get("/api/v3/queue", {"pageSize": 100}) if self.configured() else {}
        return data.get("records", []) if isinstance(data, dict) else data

    def history(self, page_size: int = 50) -> list[dict]:
        data = (
            self._get("/api/v3/history", {"pageSize": page_size, "eventType": "downloadFolderImported"})
            if self.configured()
            else {}
        )
        return data.get("records", []) if isinstance(data, dict) else []
