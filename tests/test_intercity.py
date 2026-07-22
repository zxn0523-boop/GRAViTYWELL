from datetime import datetime

from app.models import CandidatePlace, MeetingMode, MeetingRequirements, Participant, RouteExperience, TransportMode
from app.services.conversation import next_missing_question
from app.services.intercity import rank_intercity_results, score_intercity_candidate
from app.services.orchestrator import ConversationOrchestrator
from app.models import SessionState


def requirements() -> MeetingRequirements:
    return MeetingRequirements(
        mode=MeetingMode.INTERCITY,
        participants=[
            Participant(name="我", origin_text="上海徐汇", city="上海市", transport_mode=TransportMode.TRANSIT),
            Participant(name="小王", origin_text="苏州园区", city="苏州市", transport_mode=TransportMode.TRANSIT),
        ],
        meeting_time=datetime.fromisoformat("2026-07-25T14:00:00+08:00"),
        activity="看展",
        search_keywords=["美术馆"],
    )


def candidate(poi_id: str, city: str, first: int, second: int) -> CandidatePlace:
    return CandidatePlace(
        poi_id=poi_id,
        name=f"{city}测试场所",
        address="测试地址",
        longitude=121.0,
        latitude=31.0,
        city=city,
        meeting_city=city,
        type_name="科教文化服务;美术馆",
        place_kind="attraction",
        map_rating=4.6,
        routes=[
            RouteExperience(participant_name="我", duration_minutes=first, transfers=1),
            RouteExperience(participant_name="小王", duration_minutes=second, transfers=2),
        ],
    )


def test_intercity_first_round_requires_exactly_two_people() -> None:
    req = requirements()
    req.participants.append(Participant(name="第三人", origin_text="昆山", transport_mode=TransportMode.TRANSIT))
    assert "第一版先支持两个人" in (next_missing_question(req) or "")


def test_intercity_scoring_uses_door_to_door_routes() -> None:
    req = requirements()
    easier = score_intercity_candidate(candidate("easy", "上海市", 35, 95), req)
    harder = score_intercity_candidate(candidate("hard", "苏州市", 190, 30), req)
    assert easier.score > harder.score
    assert "跨城可达" in easier.score_breakdown
    assert "负担平衡" in easier.score_breakdown


def test_intercity_results_retain_an_alternative_city() -> None:
    shanghai_one = candidate("sh-1", "上海市", 30, 80)
    shanghai_two = candidate("sh-2", "上海市", 35, 85)
    suzhou = candidate("sz-1", "苏州市", 90, 25)
    shanghai_one.score = 90
    shanghai_two.score = 88
    suzhou.score = 80
    ranked = rank_intercity_results([shanghai_one, shanghai_two, suzhou])
    assert ranked[0].poi_id == "sh-1"
    assert ranked[:3][2].meeting_city == "苏州市"


def test_mode_is_detected_from_resolved_cities() -> None:
    state = SessionState(session_id="mode-detection")
    state.requirements.participants = requirements().participants
    assert ConversationOrchestrator._detect_mode(state) == MeetingMode.INTERCITY

    state.requirements.participants[1].city = "上海市"
    assert ConversationOrchestrator._detect_mode(state) == MeetingMode.SAME_CITY

    state.requirements.preferred_area_city = "苏州市"
    assert ConversationOrchestrator._detect_mode(state) == MeetingMode.INTERCITY
