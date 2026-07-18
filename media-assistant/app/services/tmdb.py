from __future__ import annotations

from typing import Any, Optional

import httpx


class TMDbClient:
    BASE = "https://api.themoviedb.org/3"
    IMG = "https://image.tmdb.org/t/p/w500"

    def __init__(self, api_key: str, language: str = "es-ES"):
        # Accepts classic v3 API key (query api_key=) or v4 Read Access Token (Bearer JWT).
        self.api_key = (api_key or "").strip()
        self.language = language

    def configured(self) -> bool:
        return bool(self.api_key)

    def _uses_bearer(self) -> bool:
        # TMDb v4 Read Access Token is a JWT (typically starts with eyJ).
        return self.api_key.startswith("eyJ") and self.api_key.count(".") >= 2

    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        q: dict[str, Any] = {"language": self.language}
        if params:
            q.update(params)
        headers: dict[str, str] = {}
        if self._uses_bearer():
            headers["Authorization"] = f"Bearer {self.api_key}"
        else:
            q["api_key"] = self.api_key
        with httpx.Client(timeout=25.0) as client:
            r = client.get(f"{self.BASE}{path}", params=q, headers=headers)
            r.raise_for_status()
            return r.json()

    def movie(self, tmdb_id: int) -> dict:
        return self._get(f"/movie/{tmdb_id}", {"append_to_response": "credits"})

    def tv(self, tmdb_id: int) -> dict:
        return self._get(f"/tv/{tmdb_id}", {"append_to_response": "credits"})

    def discover_movies(
        self,
        *,
        with_genres: Optional[str] = None,
        without_genres: Optional[str] = None,
        primary_release_date_gte: Optional[str] = None,
        primary_release_date_lte: Optional[str] = None,
        vote_average_gte: float = 6.0,
        page: int = 1,
        sort_by: str = "popularity.desc",
    ) -> list[dict]:
        params: dict[str, Any] = {
            "sort_by": sort_by,
            "vote_average.gte": vote_average_gte,
            "vote_count.gte": 80,
            "include_adult": "false",
            "page": page,
        }
        if with_genres:
            params["with_genres"] = with_genres
        if without_genres:
            params["without_genres"] = without_genres
        if primary_release_date_gte:
            params["primary_release_date.gte"] = primary_release_date_gte
        if primary_release_date_lte:
            params["primary_release_date.lte"] = primary_release_date_lte
        data = self._get("/discover/movie", params)
        return data.get("results", [])

    def discover_tv(
        self,
        *,
        with_genres: Optional[str] = None,
        without_genres: Optional[str] = None,
        vote_average_gte: float = 6.5,
        page: int = 1,
    ) -> list[dict]:
        params: dict[str, Any] = {
            "sort_by": "popularity.desc",
            "vote_average.gte": vote_average_gte,
            "vote_count.gte": 50,
            "include_adult": "false",
            "page": page,
        }
        if with_genres:
            params["with_genres"] = with_genres
        if without_genres:
            params["without_genres"] = without_genres
        data = self._get("/discover/tv", params)
        return data.get("results", [])

    def poster_url(self, poster_path: Optional[str]) -> Optional[str]:
        if not poster_path:
            return None
        if poster_path.startswith("http"):
            return poster_path
        return f"{self.IMG}{poster_path}"

    # TMDb genre ids (movies)
    GENRE_NAME_TO_ID = {
        "acción": 28,
        "action": 28,
        "aventura": 12,
        "adventure": 12,
        "animación": 16,
        "animation": 16,
        "comedia": 35,
        "comedy": 35,
        "crimen": 80,
        "crime": 80,
        "documental": 99,
        "documentary": 99,
        "drama": 18,
        "familia": 10751,
        "family": 10751,
        "fantasía": 14,
        "fantasy": 14,
        "historia": 36,
        "history": 36,
        "terror": 27,
        "horror": 27,
        "música": 10402,
        "music": 10402,
        "misterio": 9648,
        "mystery": 9648,
        "romance": 10749,
        "ciencia ficción": 878,
        "science fiction": 878,
        "película de tv": 10770,
        "thriller": 53,
        "suspense": 53,
        "bélica": 10752,
        "war": 10752,
        "western": 37,
    }
