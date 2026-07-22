from datetime import datetime

from app.models import (
    CandidateSemanticReview,
    MeetingRequirements,
    Participant,
    PoiPlace,
    RouteExperience,
    TransportMode,
)
from app.services.recommender import RecommendationService


class FakeSemanticAmap:
    def __init__(self) -> None:
        self.search_calls: list[list[str]] = []

    async def search_nearby(self, keywords, longitude, latitude, radius, limit):
        self.search_calls.append(list(keywords))
        if "老街" not in keywords:
            return [PoiPlace(
                poi_id=f"cafe-{longitude}",
                name="连锁快取咖啡",
                longitude=longitude,
                latitude=latitude,
                type_name="咖啡厅",
                place_kind="venue",
            )]
        return [PoiPlace(
            poi_id=f"street-{longitude}",
            name="城市历史文化老街",
            longitude=longitude,
            latitude=latitude,
            type_name="风景名胜;历史街区",
            place_kind="district",
        )]

    async def route(self, participant, place):
        return RouteExperience(
            participant_name=participant.name,
            duration_minutes=35,
            transfers=1,
            summary="公共交通约35分钟",
        )

    async def weather(self, city):
        return []


class FakeSemanticReviewer:
    def __init__(self) -> None:
        self.calls = 0

    async def review_candidate_semantics(self, requirements, places):
        self.calls += 1
        assert all(place.place_kind == "venue" for place in places)
        return CandidateSemanticReview(
            acceptable=True,
            reason="个别候选是咖啡馆，需要补足街区候选",
            rejected_poi_ids=[place.poi_id for place in places],
            revised_search_keywords=["老街"],
            revised_target_place_kinds=["district", "attraction"],
        )


async def test_semantic_review_researches_once_before_route_matrix() -> None:
    amap = FakeSemanticAmap()
    reviewer = FakeSemanticReviewer()
    service = RecommendationService(amap, semantic_reviewer=reviewer)  # type: ignore[arg-type]
    requirements = MeetingRequirements(
        participants=[
            Participant(
                name="我", origin_text="静安寺", longitude=121.44, latitude=31.22,
                city="上海市", adcode="310106", transport_mode=TransportMode.TRANSIT,
            ),
            Participant(
                name="小李", origin_text="松江大学城", longitude=121.23, latitude=31.05,
                city="上海市", adcode="310117", transport_mode=TransportMode.TRANSIT,
            ),
        ],
        meeting_time=datetime.fromisoformat("2026-07-26T13:00:00+08:00"),
        activity="逛有历史感的街区",
        activity_category="street_walk",
        target_place_kinds=["district", "attraction"],
        atmosphere=["有历史感"],
        search_keywords=["历史文化街区"],
    )
    timings: dict[str, int] = {}

    candidates = await service.recommend(requirements, timings)

    assert reviewer.calls == 1
    assert len(amap.search_calls) == 6  # Three geographic seeds, at most two search rounds.
    assert all(candidate.place_kind == "district" for candidate in candidates)
    assert requirements.search_keywords == ["老街"]
    assert "候选语义复核" in timings
