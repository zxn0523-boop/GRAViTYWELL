from datetime import datetime

from app.models import ExtractionResult, Participant, SessionPhase, SessionState, TransportMode
from app.services.orchestrator import ConversationOrchestrator


class FakeSessions:
    def __init__(self) -> None:
        self.state = SessionState(session_id="timing-test")

    def get(self, session_id: str):
        return self.state

    def create(self):
        return self.state

    def save(self, state: SessionState) -> None:
        self.state = state


class FakeDeepSeek:
    async def extract_requirements(self, *args, **kwargs) -> ExtractionResult:
        return ExtractionResult(question="请补充参与者和出发地。")


async def test_each_chat_response_contains_stage_and_total_timings() -> None:
    orchestrator = ConversationOrchestrator(FakeSessions(), FakeDeepSeek(), None)

    response = await orchestrator.chat("timing-test", "周末见面")

    assert response.timings_ms.keys() >= {"需求理解", "会话保存", "总耗时"}
    assert all(value >= 0 for value in response.timings_ms.values())


class DeepSeekMustNotRun:
    async def extract_requirements(self, *args, **kwargs) -> ExtractionResult:
        raise AssertionError("明确的地址确认不应调用 DeepSeek")


async def test_origin_confirmation_uses_local_fast_path() -> None:
    sessions = FakeSessions()
    sessions.state.phase = SessionPhase.CONFIRMING_ORIGINS
    sessions.state.requirements.participants = [
        Participant(name="我", origin_text="回龙观", transport_mode=TransportMode.TRANSIT),
        Participant(name="小李", origin_text="亦庄文化园", transport_mode=TransportMode.TRANSIT),
    ]
    sessions.state.requirements.meeting_time = datetime.fromisoformat("2026-07-26T13:00:00+08:00")
    orchestrator = ConversationOrchestrator(sessions, DeepSeekMustNotRun(), None)

    response = await orchestrator.chat("timing-test", "确认")

    assert "本地意图判断" in response.timings_ms
    assert "需求理解" not in response.timings_ms
    assert sessions.state.origins_confirmed is True
