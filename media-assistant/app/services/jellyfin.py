from __future__ import annotations

from typing import Any, Optional

import httpx


class JellyfinClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""

    def _headers(self) -> dict[str, str]:
        return {
            "X-Emby-Token": self.api_key,
            "Accept": "application/json",
        }

    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    def get_users(self) -> list[dict[str, Any]]:
        if not self.configured():
            return []
        with httpx.Client(timeout=20.0) as client:
            r = client.get(f"{self.base_url}/Users", headers=self._headers())
            r.raise_for_status()
            return r.json()

    def get_user_items(
        self,
        user_id: str,
        *,
        include_types: str = "Movie,Episode",
        recursive: bool = True,
        filters: Optional[str] = "IsPlayed",
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        if not self.configured():
            return []
        params: dict[str, Any] = {
            "IncludeItemTypes": include_types,
            "Recursive": str(recursive).lower(),
            "Fields": "Genres,ProviderIds,ProductionYear,UserData,SeriesName,ParentIndexNumber,IndexNumber",
            "Limit": limit,
            "SortBy": "DatePlayed",
            "SortOrder": "Descending",
        }
        if filters:
            params["Filters"] = filters
        with httpx.Client(timeout=40.0) as client:
            r = client.get(
                f"{self.base_url}/Users/{user_id}/Items",
                headers=self._headers(),
                params=params,
            )
            r.raise_for_status()
            return r.json().get("Items", [])

    def get_user_library_movies(self, user_id: str, *, limit: int = 2000) -> list[dict[str, Any]]:
        """Movies visible to a user (library presence), not only Played ones."""
        return self.get_user_items(
            user_id,
            include_types="Movie",
            recursive=True,
            filters=None,
            limit=limit,
        )

    def get_library_movies(self) -> list[dict[str, Any]]:
        if not self.configured():
            return []
        with httpx.Client(timeout=60.0) as client:
            r = client.get(
                f"{self.base_url}/Items",
                headers=self._headers(),
                params={
                    "IncludeItemTypes": "Movie",
                    "Recursive": "true",
                    "Fields": "ProviderIds,ProductionYear,Genres",
                    "Limit": 10000,
                },
            )
            r.raise_for_status()
            return r.json().get("Items", [])

    def get_library_series(self) -> list[dict[str, Any]]:
        if not self.configured():
            return []
        with httpx.Client(timeout=60.0) as client:
            r = client.get(
                f"{self.base_url}/Items",
                headers=self._headers(),
                params={
                    "IncludeItemTypes": "Series",
                    "Recursive": "true",
                    "Fields": "ProviderIds,ProductionYear",
                    "Limit": 5000,
                },
            )
            r.raise_for_status()
            return r.json().get("Items", [])
