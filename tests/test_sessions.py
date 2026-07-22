from pathlib import Path

from app.models import Participant
from app.repositories.sessions import SessionRepository


def test_session_round_trip_and_delete(tmp_path: Path) -> None:
    database = tmp_path / "test.db"
    repository = SessionRepository(database)
    repository.initialize()
    state = repository.create()
    state.requirements.participants.append(
        Participant(name="我", origin_text="上海五角场")
    )
    repository.save(state)

    loaded = repository.get(state.session_id)
    assert loaded is not None
    assert loaded.requirements.participants[0].origin_text == "上海五角场"
    assert repository.delete(state.session_id) is True
    assert repository.get(state.session_id) is None
    database.unlink()
    assert database.exists() is False
