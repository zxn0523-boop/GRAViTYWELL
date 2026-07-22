from fastapi.testclient import TestClient

from app.main import app
from app.models import CandidatePlace, ChatRequest, MeetingMode, RouteExperience


def test_health_and_static_page() -> None:
    with TestClient(app) as client:
        health = client.get("/api/health")
        page = client.get("/")
        assert health.json() == {"status": "ok"}
        assert page.status_code == 200
        assert "推荐逻辑测试" in page.text
        assert "邻城碰面" in page.text
        assert "DEEPSEEK_API_KEY" not in page.text


def test_new_chat_defaults_to_automatic_mode_detection() -> None:
    request = ChatRequest(message="测试")
    assert request.mode == MeetingMode.AUTO


def test_accept_deletes_the_entire_session() -> None:
    with TestClient(app) as client:
        session_id = client.post("/api/sessions").json()["session_id"]
        state = app.state.sessions.get(session_id)
        state.candidates = [
            CandidatePlace(
                poi_id="test",
                name="测试地点",
                address="测试地址",
                longitude=121.0,
                latitude=31.0,
                routes=[RouteExperience(participant_name="A", duration_minutes=30)],
            )
        ]
        app.state.sessions.save(state)
        response = client.post(f"/api/sessions/{session_id}/accept")
        assert response.status_code == 200
        assert response.json()["cleared"] is True
        assert app.state.sessions.get(session_id) is None
