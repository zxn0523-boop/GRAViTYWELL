import asyncio
from datetime import datetime
from itertools import product
from math import asin, cos, radians, sin, sqrt
from time import perf_counter

from app.core.opening_hours import verify_open_for_visit
from app.core.scoring import score_candidate
from app.core.venue import search_terms_for_requirements, venue_fit_score
from app.models import CandidatePlace, GeocodedOrigin, MeetingRequirements, Participant, PoiPlace, WeatherSummary
from app.services.amap import AmapError, AmapService
from app.services.atmosphere import AtmosphereService
from app.services.deepseek import DeepSeekService


class RecommendationError(RuntimeError):
    pass


class RecommendationService:
    def __init__(
        self,
        amap: AmapService,
        atmosphere: AtmosphereService | None = None,
        semantic_reviewer: DeepSeekService | None = None,
    ) -> None:
        self.amap = amap
        self.atmosphere = atmosphere
        self.semantic_reviewer = semantic_reviewer

    async def resolve_origins(self, requirements: MeetingRequirements) -> MeetingRequirements:
        updated = requirements.model_copy(deep=True)
        match_groups = await asyncio.gather(
            *(self.amap.origin_candidates(participant.origin_text) for participant in updated.participants)
        )
        selected = choose_origin_combination(
            updated.participants,
            match_groups,
            prefer_same_city=None,
        )
        for participant, best in zip(updated.participants, selected, strict=True):
            participant.longitude = best.longitude
            participant.latitude = best.latitude
            participant.formatted_address = best.formatted_address
            participant.city = best.city
            participant.city_code = best.city_code
            participant.adcode = best.adcode
        if updated.preferred_area_text:
            participant_cities = [participant.city for participant in updated.participants if participant.city]
            preferred_city = participant_cities[0] if participant_cities and len(set(participant_cities)) == 1 else None
            area_matches = await self.amap.origin_candidates(updated.preferred_area_text, preferred_city)
            area = choose_preferred_area(area_matches, participant_cities)
            updated.preferred_area_longitude = area.longitude
            updated.preferred_area_latitude = area.latitude
            updated.preferred_area_formatted_address = area.formatted_address
            updated.preferred_area_city = area.city
            updated.preferred_area_adcode = area.adcode
        return updated

    async def recommend(
        self,
        requirements: MeetingRequirements,
        timings_ms: dict[str, int] | None = None,
    ) -> list[CandidatePlace]:
        if any(p.longitude is None or p.latitude is None for p in requirements.participants):
            raise RecommendationError("所有出发地必须先完成地址确认")

        seeds = generate_search_seeds(
            requirements.participants,
            requirements.favored_participant,
            (
                requirements.preferred_area_longitude,
                requirements.preferred_area_latitude,
            )
            if requirements.preferred_area_longitude is not None
            and requirements.preferred_area_latitude is not None
            else None,
        )
        keywords = search_terms_for_requirements(requirements) or ["休闲场所"]
        search_started = perf_counter()
        available_places = await self._discover_available(requirements, seeds, keywords)

        if self.semantic_reviewer:
            review_started = perf_counter()
            review_input = sorted(
                available_places,
                key=lambda place: (-venue_fit_score(place, requirements), -(place.map_rating or 0)),
            )[:12]
            review = await self.semantic_reviewer.review_candidate_semantics(requirements, review_input)
            if review.revised_search_keywords and (
                not review.acceptable or review.rejected_poi_ids
            ):
                requirements.search_keywords = review.revised_search_keywords
                if review.revised_target_place_kinds:
                    requirements.target_place_kinds = review.revised_target_place_kinds
                keywords = search_terms_for_requirements(requirements)
                available_places = await self._discover_available(requirements, seeds, keywords)
            elif review.rejected_poi_ids:
                rejected = set(review.rejected_poi_ids)
                available_places = [place for place in available_places if place.poi_id not in rejected]
                if not available_places:
                    raise RecommendationError("语义复核剔除了全部跑题候选，请换一种活动描述后再试")
            _record_timing(timings_ms, "候选语义复核", review_started)

        center = seeds[0]
        if self.atmosphere and self.atmosphere.is_needed(requirements):
            atmosphere_started = perf_counter()
            preliminary = sorted(
                available_places,
                key=lambda place: (
                    -venue_fit_score(place, requirements),
                    -(place.map_rating or 0),
                ),
            )
            try:
                await self.atmosphere.enrich(preliminary, requirements)
            except Exception:
                # Public search is optional enrichment; map-based recommendations remain usable.
                pass
            _record_timing(timings_ms, "场所氛围检索", atmosphere_started)
        fitted_places = [
            (venue_fit_score(place, requirements), place)
            for place in available_places
        ]
        suitable_places = [item for item in fitted_places if item[0] >= 0.15]
        if not suitable_places and requirements.target_place_kinds:
            raise RecommendationError("附近没有找到符合主要活动和场所形态的候选，不会用其他品类替代")
        suitable_places = suitable_places or fitted_places
        shortlist = [
            place
            for _, place in sorted(
                suitable_places,
                key=lambda item: (
                    -item[0],
                    haversine_km(center[1], center[0], item[1].latitude, item[1].longitude),
                ),
            )[:8]
        ]
        _record_timing(timings_ms, "场所搜索与初筛", search_started)

        routes_started = perf_counter()
        routed = await asyncio.gather(
            *(self._build_candidate(place, requirements.participants) for place in shortlist),
            return_exceptions=True,
        )
        candidates = [item for item in routed if isinstance(item, CandidatePlace)]
        if not candidates:
            raise RecommendationError("找到了场所，但无法取得所有参与者的完整路线")
        _record_timing(timings_ms, "路线矩阵", routes_started)

        weather_started = perf_counter()
        weather_by_region = await self._load_weather(candidates, requirements.meeting_time)
        eligible: list[CandidatePlace] = []
        fallback: list[CandidatePlace] = []
        for candidate in candidates:
            weather = weather_by_region.get(candidate.adcode or candidate.city or "")
            scored, passed = score_candidate(candidate, requirements, weather)
            fallback.append(scored)
            if passed:
                eligible.append(scored)

        ranked = sorted(eligible or fallback, key=lambda item: item.score, reverse=True)
        if not eligible:
            for item in ranked:
                item.warnings.append("没有候选点完全满足默认硬限制，以下为相对更公平的备选")
        _record_timing(timings_ms, "天气与评分", weather_started)
        return ranked[:3]

    async def _discover_available(
        self,
        requirements: MeetingRequirements,
        seeds: list[tuple[float, float]],
        keywords: list[str],
    ) -> list[PoiPlace]:
        discovered = await asyncio.gather(
            *(
                self.amap.search_nearby(
                    keywords,
                    longitude,
                    latitude,
                    radius=5_000 if requirements.preferred_area_text else 15_000,
                    limit=10,
                )
                for longitude, latitude in seeds
            ),
            return_exceptions=True,
        )
        unique_places: dict[str, PoiPlace] = {}
        for result in discovered:
            if isinstance(result, Exception):
                continue
            for place in result:
                unique_places.setdefault(place.poi_id, place)
        if not unique_places:
            raise RecommendationError("候选区域附近没有找到符合需求的真实场所")

        available_places: list[PoiPlace] = []
        for place in unique_places.values():
            if (
                requirements.preferred_area_longitude is not None
                and requirements.preferred_area_latitude is not None
                and haversine_km(
                    requirements.preferred_area_latitude,
                    requirements.preferred_area_longitude,
                    place.latitude,
                    place.longitude,
                ) > 5
            ):
                continue
            place.opening_verified = verify_open_for_visit(place.opening_hours, requirements.meeting_time)
            if place.opening_hours and place.opening_verified is not True:
                continue
            available_places.append(place)
        if not available_places:
            raise RecommendationError("找到了候选地点，但没有能够确认在计划到达后仍营业的地点")
        return available_places

    async def _build_candidate(
        self,
        place: PoiPlace,
        participants: list[Participant],
    ) -> CandidatePlace:
        routes = await asyncio.gather(
            *(self.amap.route(participant, place) for participant in participants),
            return_exceptions=True,
        )
        if any(isinstance(route, Exception) for route in routes):
            failures = [str(route) for route in routes if isinstance(route, Exception)]
            raise AmapError("；".join(failures))
        candidate = CandidatePlace(
            **place.model_dump(),
            routes=list(routes),
        )
        if not candidate.opening_hours and candidate.place_kind in ("venue", "attraction"):
            candidate.warnings.append("高德未提供营业时间，请在出发前向场所确认")
        return candidate

    async def _load_weather(
        self,
        candidates: list[CandidatePlace],
        meeting_time: datetime | None,
    ) -> dict[str, WeatherSummary]:
        if meeting_time is None:
            return {}
        regions = list(
            dict.fromkeys(
                candidate.adcode or candidate.city
                for candidate in candidates
                if candidate.adcode or candidate.city
            )
        )[:3]
        results = await asyncio.gather(
            *(self.amap.weather(region) for region in regions),
            return_exceptions=True,
        )
        target_date = meeting_time.date().isoformat()
        weather_by_region: dict[str, WeatherSummary] = {}
        for region, result in zip(regions, results, strict=True):
            if isinstance(result, Exception):
                continue
            match = next((item for item in result if item.date == target_date), None)
            if match:
                weather_by_region[region] = match
        return weather_by_region


def generate_search_seeds(
    participants: list[Participant],
    favored_participant: str | None = None,
    preferred_center: tuple[float, float] | None = None,
) -> list[tuple[float, float]]:
    if preferred_center is not None:
        return [(round(preferred_center[0], 6), round(preferred_center[1], 6))]
    points = [(p.longitude or 0, p.latitude or 0) for p in participants]
    center = (
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points),
    )
    seeds = [center]
    if len(points) == 2:
        first, second = points
        seeds.extend([
            interpolate(first, second, 0.4),
            interpolate(first, second, 0.6),
        ])
    else:
        farthest = sorted(
            points,
            key=lambda point: haversine_km(center[1], center[0], point[1], point[0]),
            reverse=True,
        )[:2]
        seeds.extend(interpolate(point, center, 0.65) for point in farthest)
    if favored_participant:
        favored = next(
            (point for participant, point in zip(participants, points, strict=True) if participant.name == favored_participant),
            None,
        )
        if favored:
            seeds.append(interpolate(center, favored, 0.8))
    return list(dict.fromkeys((round(lon, 6), round(lat, 6)) for lon, lat in seeds))


def choose_preferred_area(
    matches: list[GeocodedOrigin],
    participant_cities: list[str],
) -> GeocodedOrigin:
    """Prefer the target-area match in the participants' city when names are ambiguous."""

    if not matches:
        raise RecommendationError("指定的目标区域没有可确认的地图结果")
    city_counts = {city: participant_cities.count(city) for city in set(participant_cities)}
    return max(
        matches,
        key=lambda item: city_counts.get(item.city, 0) * 10
        + MAJOR_CITY_BONUS.get(item.city or "", 0)
        + (4 if item.query in item.formatted_address else 0),
    )


MAJOR_CITY_BONUS = {
    "北京市": 4,
    "上海市": 4,
    "广州市": 3,
    "深圳市": 3,
    "杭州市": 3,
    "南京市": 3,
    "苏州市": 3,
    "成都市": 3,
    "重庆市": 3,
    "武汉市": 3,
    "西安市": 3,
}


def choose_origin_combination(
    participants: list[Participant],
    match_groups: list[list],
    prefer_same_city: bool | None = True,
) -> tuple:
    """Prefer combinations that are mutually city-consistent, without making it a hard rule."""

    if any(not group for group in match_groups):
        raise RecommendationError("至少有一个出发地没有可确认的地图结果")

    if prefer_same_city is None:
        explicit_cities: set[str] = set()
        for participant, group in zip(participants, match_groups, strict=True):
            for item in group:
                city = (item.city or "").removesuffix("市")
                if city and city in participant.origin_text:
                    explicit_cities.add(city)
        prefer_same_city = len(explicit_cities) < 2

    def combination_score(combination: tuple) -> float:
        score = 0.0
        cities = [item.city for item in combination]
        for participant, item in zip(participants, combination, strict=True):
            city = item.city or ""
            score += MAJOR_CITY_BONUS.get(city, 0)
            if participant.origin_text in item.formatted_address:
                score += 4
            city_short = city.removesuffix("市")
            if city_short and city_short in participant.origin_text:
                score += 10
        for index, city in enumerate(cities):
            for other_city in cities[index + 1:]:
                if not city or not other_city:
                    continue
                if prefer_same_city and city == other_city:
                    score += 12
                if not prefer_same_city and city != other_city:
                    score += 12
        return score

    return max(product(*match_groups), key=combination_score)


def interpolate(
    start: tuple[float, float],
    end: tuple[float, float],
    ratio: float,
) -> tuple[float, float]:
    return (
        start[0] + (end[0] - start[0]) * ratio,
        start[1] + (end[1] - start[1]) * ratio,
    )


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    earth_radius = 6371
    d_lat = radians(lat2 - lat1)
    d_lon = radians(lon2 - lon1)
    a = sin(d_lat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(d_lon / 2) ** 2
    return 2 * earth_radius * asin(sqrt(a))


def _record_timing(
    timings_ms: dict[str, int] | None,
    label: str,
    started: float,
) -> None:
    if timings_ms is not None:
        timings_ms[label] = round((perf_counter() - started) * 1000)
