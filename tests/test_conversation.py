from datetime import datetime

from app.models import ExtractionResult, MeetingRequirements, Participant, TransportMode
from app.services.conversation import merge_requirements, next_missing_question


def test_merge_and_missing_question() -> None:
    current = MeetingRequirements()
    extracted = ExtractionResult(
        participants=[
            Participant(name="我", origin_text="上海五角场", transport_mode=TransportMode.TRANSIT),
            Participant(name="小张", origin_text="苏州园区", transport_mode=TransportMode.TRANSIT),
        ],
        meeting_time=datetime.fromisoformat("2026-07-25T14:00:00+08:00"),
        meeting_time_text="本周六下午两点",
        activity="聊天",
        atmosphere=["安静", "有设计感"],
        search_keywords=["咖啡馆", "书店"],
    )
    merged = merge_requirements(current, extracted)
    assert next_missing_question(merged) is None
    assert merged.participants[1].origin_text == "苏州园区"
    assert merged.search_keywords == ["咖啡馆", "书店"]


def test_asks_only_the_next_required_question() -> None:
    requirements = MeetingRequirements(
        participants=[
            Participant(name="我", origin_text="五角场"),
            Participant(name="A", origin_text="静安寺"),
        ]
    )
    assert next_missing_question(requirements) == "你们计划哪一天、几点左右见面？"


def test_keeps_confirmed_coordinates_when_only_preferences_change() -> None:
    current = MeetingRequirements(
        participants=[
            Participant(
                name="我",
                origin_text="五角场",
                transport_mode=TransportMode.TRANSIT,
                longitude=121.5,
                latitude=31.3,
                formatted_address="上海市杨浦区五角场",
            ),
            Participant(
                name="A",
                origin_text="静安寺",
                transport_mode=TransportMode.TRANSIT,
                longitude=121.44,
                latitude=31.22,
            ),
        ]
    )
    extracted = ExtractionResult(
        participants=[
            Participant(name="我", origin_text="五角场", transport_mode=TransportMode.TRANSIT),
            Participant(name="A", origin_text="静安寺", transport_mode=TransportMode.TRANSIT),
        ],
        atmosphere=["更安静"],
    )
    merged = merge_requirements(current, extracted)
    assert merged.participants[0].longitude == 121.5
    assert merged.participants[0].formatted_address == "上海市杨浦区五角场"


def test_favor_person_is_kept_as_structured_requirement() -> None:
    current = MeetingRequirements()
    extracted = ExtractionResult(priority="favor_person", favored_participant="小张")
    merged = merge_requirements(current, extracted)
    assert merged.priority == "favor_person"
    assert merged.favored_participant == "小张"


def test_confirmed_transport_modes_survive_null_model_output() -> None:
    current = MeetingRequirements(
        participants=[
            Participant(name="我", origin_text="回龙观", transport_mode=TransportMode.TRANSIT),
            Participant(name="小李", origin_text="亦庄文化园", transport_mode=TransportMode.TRANSIT),
        ]
    )
    extracted = ExtractionResult(
        participants=[
            Participant(name="我", origin_text="回龙观", transport_mode=None),
            Participant(name="小李", origin_text="亦庄文化园", transport_mode=None),
        ],
        atmosphere=["更安静"],
    )

    merged = merge_requirements(current, extracted, "环境更安静")

    assert all(item.transport_mode == TransportMode.TRANSIT for item in merged.participants)


def test_atmosphere_and_price_update_cannot_replace_locked_cafe_category() -> None:
    current = MeetingRequirements(
        activity="聊天",
        atmosphere=["有设计感"],
        search_keywords=["咖啡馆"],
    )
    extracted = ExtractionResult(
        activity="用餐",
        atmosphere=["环境更特别"],
        constraints=["可以稍微贵一点"],
        search_keywords=["特色餐厅"],
    )

    merged = merge_requirements(
        current,
        extracted,
        "可以稍微贵一点，但希望环境更特别",
    )

    assert merged.activity == "聊天"
    assert merged.search_keywords == ["咖啡馆"]
    assert merged.atmosphere == ["环境更特别"]


def test_explicit_category_change_can_unlock_cafe_request() -> None:
    current = MeetingRequirements(activity="喝咖啡", search_keywords=["咖啡馆"])
    extracted = ExtractionResult(activity="吃饭", search_keywords=["餐厅"])

    merged = merge_requirements(current, extracted, "不想喝咖啡了，改成餐厅吃饭")

    assert merged.activity == "吃饭"
    assert merged.search_keywords == ["餐厅"]
