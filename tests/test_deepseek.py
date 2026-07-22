import json
from datetime import datetime, timezone

import httpx

from app.models import MeetingRequirements
from app.services.deepseek import DeepSeekService, _normalize_extraction_payload


async def test_build_venue_profiles_uses_one_grounded_json_call() -> None:
    from app.models import PoiPlace, SearchEvidence

    async def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode("utf-8")
        assert "环境安静" in body
        return httpx.Response(
            200,
            json={
                "choices": [{
                    "message": {
                        "content": '{"profiles":[{"poi_id":"p1","quiet":0.9,"design":0.7,"conversation_friendly":0.9,"date_friendly":0.8,"quick_service":0.1,"confidence":0.8,"summary":"公开评价提到环境安静"}]}'
                    }
                }]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = DeepSeekService("test", "test", client=client)
    profiles = await service.build_venue_profiles(
        MeetingRequirements(activity="聊天", atmosphere=["安静"]),
        [(
            PoiPlace(poi_id="p1", name="测试店", longitude=1, latitude=1),
            [SearchEvidence(title="评价", url="https://review.test", snippet="环境安静", source="test")],
        )],
    )
    assert profiles[0].poi_id == "p1"
    assert profiles[0].quiet == 0.9
    await client.aclose()


async def test_null_participant_name_is_recovered_from_user_message() -> None:
    model_output = {
        "intent": "update",
        "participants": [
            {"name": None, "origin_text": "北京西站", "transport_mode": "transit"},
            {"name": "小王", "origin_text": "顺义", "transport_mode": "driving"},
        ],
        "meeting_time": "2026-07-25T12:00:00+08:00",
        "meeting_time_text": "本周六中午十二点",
        "activity": "吃北京菜",
        "atmosphere": "安静",
        "search_keywords": ["北京菜"],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        request_payload = json.loads(request.content)
        assert request_payload["thinking"] == {"type": "disabled"}
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(model_output, ensure_ascii=False)}}]},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = DeepSeekService("test", "test", client=client)
    result = await service.extract_requirements(
        MeetingRequirements(),
        "我从北京西站坐公共交通，小王从顺义驾车，本周六中午十二点见面，想吃环境安静的北京菜。",
    )
    assert result.participants is not None
    assert result.participants[0].name == "我"
    assert result.participants[0].transport_mode == "transit"
    assert result.atmosphere == ["安静"]
    await client.aclose()


async def test_garbled_participant_names_trigger_a_retry() -> None:
    call_count = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        names = ["ОТ", "РЎХЕ"] if call_count == 1 else ["我", "小张"]
        output = {
            "intent": "update",
            "participants": [
                {"name": names[0], "origin_text": "静安寺", "transport_mode": "transit"},
                {"name": names[1], "origin_text": "松江大学城", "transport_mode": "transit"},
            ],
            "meeting_time": "2026-07-24T14:00:00+08:00",
            "activity": "聊天",
            "search_keywords": ["咖啡馆"],
        }
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(output, ensure_ascii=False)}}]},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = DeepSeekService("test", "test", client=client)
    result = await service.extract_requirements(
        MeetingRequirements(),
        "我在静安寺，小张在松江大学城，本周五下午两点都乘公共交通，想找咖啡馆。",
    )
    assert call_count == 2
    assert [participant.name for participant in result.participants or []] == ["我", "小张"]
    await client.aclose()


async def test_empty_content_is_retried_without_restarting_conversation() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(200, json={"choices": [{"message": {"content": ""}}]})

        request_payload = json.loads(request.content)
        assert "上一次响应为空或无法校验" in request_payload["messages"][0]["content"]
        output = {
            "intent": "update",
            "participants": [
                {"name": "我", "origin_text": "北京西站", "transport_mode": "transit"},
                {"name": "小王", "origin_text": "顺义", "transport_mode": "driving"},
            ],
            "meeting_time": "2026-07-25T12:00:00+08:00",
            "activity": "吃北京菜",
            "search_keywords": ["北京菜"],
        }
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(output, ensure_ascii=False)}}]},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = DeepSeekService("test", "test", client=client, retry_delay_seconds=0)
    result = await service.extract_requirements(
        MeetingRequirements(),
        "我从北京西站坐公共交通，小王从顺义驾车，本周六中午十二点见面，想吃北京菜。",
    )

    assert call_count == 2
    assert [participant.name for participant in result.participants or []] == ["我", "小王"]
    await client.aclose()


def test_vague_tomorrow_evening_gets_stable_default_time() -> None:
    normalized = _normalize_extraction_payload(
        {
            "meeting_time": "2026-07-22T00:00:00+08:00",
            "meeting_time_text": "明天晚上",
        },
        MeetingRequirements(),
        "我和小王明天晚上见面",
        datetime(2026, 7, 21, 10, tzinfo=timezone.utc),
    )
    assert normalized["meeting_time"] == "2026-07-22T19:00:00+08:00"


def test_vague_time_can_be_resolved_even_when_model_omits_datetime() -> None:
    china_tz = timezone.utc
    normalized = _normalize_extraction_payload(
        {"meeting_time": None, "meeting_time_text": "明天晚上"},
        MeetingRequirements(),
        "明天晚上见",
        datetime(2026, 7, 21, 10, tzinfo=china_tz),
    )
    assert normalized["meeting_time"] == "2026-07-22T19:00:00+00:00"


def test_all_participants_inherit_explicit_shared_transit_mode() -> None:
    normalized = _normalize_extraction_payload(
        {
            "participants": [
                {"name": "我", "origin_text": "回龙观", "transport_mode": None},
                {"name": "小李", "origin_text": "亦庄文化园", "transport_mode": None},
            ]
        },
        MeetingRequirements(),
        "我在回龙观，小李在亦庄文化园，周日下午一点都坐公共交通",
    )

    assert [item["transport_mode"] for item in normalized["participants"]] == [
        "transit",
        "transit",
    ]
