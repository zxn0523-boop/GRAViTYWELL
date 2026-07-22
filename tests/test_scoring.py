from datetime import datetime

from app.core.scoring import score_candidate
from app.models import (
    CandidatePlace,
    MeetingRequirements,
    Participant,
    RouteExperience,
    TransportMode,
    WeatherSummary,
    GeocodedOrigin,
    PoiPlace,
)
from app.core.venue import infer_place_kind, search_terms_for_requirements, venue_fit_score
from app.services.recommender import RecommendationService, choose_origin_combination, choose_preferred_area, generate_search_seeds


def requirements() -> MeetingRequirements:
    return MeetingRequirements(
        participants=[
            Participant(name="A", origin_text="甲地", longitude=121.0, latitude=31.0, transport_mode=TransportMode.TRANSIT),
            Participant(name="B", origin_text="乙地", longitude=121.8, latitude=31.4, transport_mode=TransportMode.TRANSIT),
        ],
        meeting_time=datetime.fromisoformat("2026-07-25T14:00:00+08:00"),
        activity="聊天",
        search_keywords=["咖啡馆"],
    )


def test_fair_candidate_beats_unfair_candidate() -> None:
    req = requirements()
    weather = WeatherSummary(city="上海", date="2026-07-25", day_weather="晴")
    fair = CandidatePlace(
        poi_id="fair", name="中点咖啡馆", address="中点", longitude=121.4, latitude=31.2,
        routes=[
            RouteExperience(participant_name="A", duration_minutes=42, transfers=1),
            RouteExperience(participant_name="B", duration_minutes=46, transfers=1),
        ],
    )
    unfair = CandidatePlace(
        poi_id="unfair", name="另一家咖啡馆", address="偏远", longitude=121.1, latitude=31.0,
        routes=[
            RouteExperience(participant_name="A", duration_minutes=12, transfers=0),
            RouteExperience(participant_name="B", duration_minutes=78, transfers=2),
        ],
    )
    scored_fair, fair_passed = score_candidate(fair, req, weather)
    scored_unfair, _ = score_candidate(unfair, req, weather)
    assert fair_passed is True
    assert scored_fair.score > scored_unfair.score


def test_geometric_points_are_only_search_seeds() -> None:
    seeds = generate_search_seeds(requirements().participants)
    assert seeds[0] == (121.4, 31.2)
    assert len(seeds) == 3


def test_same_city_landmark_combination_is_preferred() -> None:
    participants = [
        Participant(name="A", origin_text="静安寺"),
        Participant(name="B", origin_text="松江大学城"),
    ]
    groups = [
        [
            GeocodedOrigin(
                query="静安寺", formatted_address="浙江省台州市临海市静安寺村",
                longitude=121.1, latitude=28.8, city="台州市",
            ),
            GeocodedOrigin(
                query="静安寺", formatted_address="上海市静安区静安寺",
                longitude=121.44, latitude=31.22, city="上海市",
            ),
        ],
        [
            GeocodedOrigin(
                query="松江大学城", formatted_address="上海市松江区松江大学城",
                longitude=121.23, latitude=31.05, city="上海市",
            )
        ],
    ]
    selected = choose_origin_combination(participants, groups)
    assert selected[0].formatted_address == "上海市静安区静安寺"

    auto_selected = choose_origin_combination(participants, groups, prefer_same_city=None)
    assert auto_selected[0].formatted_address == "上海市静安区静安寺"


async def test_manual_intercity_expectation_never_distorts_geocoding() -> None:
    groups = {
        "静安寺": [
            GeocodedOrigin(query="静安寺", formatted_address="湖北省某地静安寺", longitude=112.0, latitude=30.0, city="荆州市"),
            GeocodedOrigin(query="静安寺", formatted_address="上海市静安区静安寺", longitude=121.44, latitude=31.22, city="上海市"),
        ],
        "松江大学城": [
            GeocodedOrigin(query="松江大学城", formatted_address="上海市松江区松江大学城", longitude=121.23, latitude=31.05, city="上海市"),
        ],
    }

    class FakeAmap:
        async def origin_candidates(self, query: str, preferred_city=None):
            return groups[query]

    req = MeetingRequirements(
        mode="intercity",
        participants=[
            Participant(name="我", origin_text="静安寺"),
            Participant(name="小李", origin_text="松江大学城"),
        ],
    )
    resolved = await RecommendationService(FakeAmap()).resolve_origins(req)
    assert [item.city for item in resolved.participants] == ["上海市", "上海市"]


def test_favor_person_changes_the_ranking() -> None:
    req = requirements()
    req.priority = "favor_person"
    req.favored_participant = "B"
    weather = WeatherSummary(city="上海", date="2026-07-25", day_weather="晴")
    near_b = CandidatePlace(
        poi_id="near-b", name="独立咖啡空间", address="近B", longitude=121.7, latitude=31.3,
        routes=[
            RouteExperience(participant_name="A", duration_minutes=72, transfers=1),
            RouteExperience(participant_name="B", duration_minutes=18, transfers=0),
        ],
    )
    balanced = CandidatePlace(
        poi_id="balanced", name="独立咖啡空间", address="中间", longitude=121.4, latitude=31.2,
        routes=[
            RouteExperience(participant_name="A", duration_minutes=42, transfers=1),
            RouteExperience(participant_name="B", duration_minutes=42, transfers=1),
        ],
    )
    scored_near_b, _ = score_candidate(near_b, req, weather)
    scored_balanced, _ = score_candidate(balanced, req, weather)
    assert scored_near_b.score > scored_balanced.score
    assert "照顾偏向" in scored_near_b.score_breakdown


def test_experiential_request_penalizes_takeaway_coffee() -> None:
    req = requirements()
    req.atmosphere = ["有设计感", "安静", "适合聊天"]
    takeaway = PoiPlace(
        poi_id="quick", name="NOWWA挪瓦咖啡(美宜佳店)", address="便利店内",
        longitude=121.4, latitude=31.2, type_name="咖啡厅",
    )
    experiential = PoiPlace(
        poi_id="slow", name="建筑师艺术空间咖啡", address="创意园区",
        longitude=121.4, latitude=31.2, type_name="咖啡厅",
    )
    assert venue_fit_score(experiential, req) > venue_fit_score(takeaway, req)


def test_explicit_cafe_request_rejects_pure_restaurant() -> None:
    req = requirements()
    restaurant = PoiPlace(
        poi_id="restaurant",
        name="花园西餐厅",
        address="商场三层",
        longitude=121.4,
        latitude=31.2,
        type_name="餐饮服务;外国餐厅",
    )

    assert venue_fit_score(restaurant, req) == 0


def test_favor_person_adds_a_search_seed_near_that_person() -> None:
    req = requirements()
    regular = generate_search_seeds(req.participants)
    favored = generate_search_seeds(req.participants, "B")
    assert len(favored) == len(regular) + 1


def test_walking_request_expands_to_parks_and_districts() -> None:
    req = requirements()
    req.activity = "散步聊天"
    req.atmosphere = ["自然", "适合拍照"]
    terms = search_terms_for_requirements(req)
    assert "公园" in terms
    assert "绿道" in terms
    assert infer_place_kind("亮马河国际风情水岸", "风景名胜;公园广场") == "park"


def test_design_atmosphere_does_not_replace_explicit_cafe_category() -> None:
    req = requirements()
    req.activity = "聊天"
    req.atmosphere = ["有设计感", "安静"]
    req.search_keywords = ["咖啡馆", "艺术空间"]
    terms = search_terms_for_requirements(req)
    assert all("咖啡" in term for term in terms)
    assert "艺术空间" not in terms


def test_map_rating_affects_otherwise_equal_candidates() -> None:
    req = requirements()
    weather = WeatherSummary(city="上海", date="2026-07-25", day_weather="晴")

    def candidate(poi_id: str, rating: float) -> CandidatePlace:
        return CandidatePlace(
            poi_id=poi_id, name="独立咖啡空间", address="中间", longitude=121.4, latitude=31.2,
            map_rating=rating,
            routes=[
                RouteExperience(participant_name="A", duration_minutes=40, transfers=1),
                RouteExperience(participant_name="B", duration_minutes=42, transfers=1),
            ],
        )

    high, _ = score_candidate(candidate("high", 4.8), req, weather)
    low, _ = score_candidate(candidate("low", 3.5), req, weather)
    assert high.score > low.score
    assert high.score_breakdown["地图口碑"] == 96.0


def test_explicit_area_requirement_overrides_fairness_ranking() -> None:
    req = requirements()
    req.priority = "location_first"
    req.preferred_area_text = "国贸"
    req.preferred_area_longitude = 116.46
    req.preferred_area_latitude = 39.91
    weather = WeatherSummary(city="北京", date="2026-07-25", day_weather="晴")
    near_target = CandidatePlace(
        poi_id="near", name="国贸附近餐厅", address="国贸", longitude=116.461, latitude=39.91,
        routes=[
            RouteExperience(participant_name="A", duration_minutes=70, transfers=2),
            RouteExperience(participant_name="B", duration_minutes=25, transfers=0),
        ],
    )
    fair_but_elsewhere = CandidatePlace(
        poi_id="fair", name="中点餐厅", address="别处", longitude=116.40, latitude=39.91,
        routes=[
            RouteExperience(participant_name="A", duration_minutes=40, transfers=1),
            RouteExperience(participant_name="B", duration_minutes=42, transfers=1),
        ],
    )
    scored_near, _ = score_candidate(near_target, req, weather)
    scored_fair, _ = score_candidate(fair_but_elsewhere, req, weather)
    assert scored_near.score > scored_fair.score
    assert "位置要求" in scored_near.score_breakdown


def test_explicit_area_becomes_the_only_search_center() -> None:
    seeds = generate_search_seeds(requirements().participants, preferred_center=(116.46, 39.91))
    assert seeds == [(116.46, 39.91)]


def test_preferred_area_uses_participants_city_for_ambiguous_name() -> None:
    matches = [
        GeocodedOrigin(query="国贸", formatted_address="厦门市国贸", longitude=118.1, latitude=24.4, city="厦门市"),
        GeocodedOrigin(query="国贸", formatted_address="北京市国贸", longitude=116.46, latitude=39.91, city="北京市"),
    ]
    selected = choose_preferred_area(matches, ["北京市", "北京市"])
    assert selected.city == "北京市"


def test_intercity_origin_resolution_prefers_two_explicit_cities() -> None:
    participants = [
        Participant(name="我", origin_text="上海徐汇"),
        Participant(name="小王", origin_text="苏州园区"),
    ]
    groups = [
        [
            GeocodedOrigin(query="上海徐汇", formatted_address="上海市徐汇区", longitude=121.4, latitude=31.2, city="上海市"),
            GeocodedOrigin(query="上海徐汇", formatted_address="苏州市徐汇路", longitude=120.6, latitude=31.3, city="苏州市"),
        ],
        [
            GeocodedOrigin(query="苏州园区", formatted_address="上海市某园区", longitude=121.5, latitude=31.2, city="上海市"),
            GeocodedOrigin(query="苏州园区", formatted_address="苏州市工业园区", longitude=120.7, latitude=31.3, city="苏州市"),
        ],
    ]
    selected = choose_origin_combination(participants, groups, prefer_same_city=False)
    assert [item.city for item in selected] == ["上海市", "苏州市"]

    auto_selected = choose_origin_combination(participants, groups, prefer_same_city=None)
    assert [item.city for item in auto_selected] == ["上海市", "苏州市"]
