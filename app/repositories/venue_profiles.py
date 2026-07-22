import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock

from app.models import VenueAtmosphereProfile


class VenueProfileRepository:
    def __init__(self, database_path: Path, ttl_days: int = 30) -> None:
        self.database_path = database_path
        self.ttl = timedelta(days=ttl_days)
        self._lock = Lock()

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS venue_profiles (
                    profile_key TEXT PRIMARY KEY,
                    profile_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def get(self, poi_id: str, provider: str) -> VenueAtmosphereProfile | None:
        key = self._key(poi_id, provider)
        cutoff = (datetime.now() - self.ttl).isoformat()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT profile_json FROM venue_profiles WHERE profile_key = ? AND updated_at >= ?",
                (key, cutoff),
            ).fetchone()
        if not row:
            return None
        profile = VenueAtmosphereProfile.model_validate_json(row[0])
        profile.cached = True
        return profile

    def save(
        self,
        poi_id: str,
        profile: VenueAtmosphereProfile,
        cache_provider: str | None = None,
    ) -> None:
        stored = profile.model_copy(update={"cached": False})
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO venue_profiles(profile_key, profile_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(profile_key) DO UPDATE SET
                    profile_json = excluded.profile_json,
                    updated_at = excluded.updated_at
                """,
                (
                    self._key(poi_id, cache_provider or profile.provider),
                    stored.model_dump_json(),
                    datetime.now().isoformat(),
                ),
            )

    @staticmethod
    def _key(poi_id: str, provider: str) -> str:
        return f"{provider}:{poi_id}"

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=10)
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()
