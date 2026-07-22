import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock
from uuid import uuid4

from app.models import SessionState


class SessionRepository:
    """Stores only unfinished appointment sessions in one local SQLite file."""

    def __init__(self, database_path: Path, ttl_hours: int = 24) -> None:
        self.database_path = database_path
        self.ttl = timedelta(hours=ttl_hours)
        self._lock = Lock()

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def create(self) -> SessionState:
        state = SessionState(session_id=str(uuid4()))
        self.save(state)
        return state

    def get(self, session_id: str) -> SessionState | None:
        self.purge_expired()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state_json FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return SessionState.model_validate_json(row[0]) if row else None

    def save(self, state: SessionState) -> None:
        state.updated_at = datetime.now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO sessions(session_id, state_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at
                """,
                (state.session_id, state.model_dump_json(), state.updated_at.isoformat()),
            )

    def delete(self, session_id: str) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM sessions WHERE session_id = ?", (session_id,)
            )
        return cursor.rowcount > 0

    def purge_expired(self) -> int:
        cutoff = (datetime.now() - self.ttl).isoformat()
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM sessions WHERE updated_at < ?", (cutoff,)
            )
        return cursor.rowcount

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=10)
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()
