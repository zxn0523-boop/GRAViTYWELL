import asyncio
from datetime import datetime
from statistics import mean
from time import perf_counter

from app.core.opening_hours import verify_open_for_visit
from app.core.venue import search_terms_for_requirements, venue_fit_score
from app.models import CandidatePlace, GeocodedOrigin, MeetingRequirements, Participant, PoiPlace, WeatherSummary
from app.services.amap import AmapError, AmapService
from app.services.atmosphere import AtmosphereService
from app.services.deepseek import DeepSeekService
from app.services.recommender import RecommendationError, haversine_km


class IntercityRecommendationService:
    """Independent first-round workflow for choosing between participants' cities."""

    def __init__(
        self,
        amap: AmapService,
        atmosphere: AtmosphereService | None = None,
        semantic_reviewer: DeepSeekService | None = None,
    ) -> None:
        self.amap = amap
        self.atmosphere = atmosphere
        self.semantic_reviewer = semantic_reviewer

    async def recommend(
        self,
        requirements: MeetingRequirements,
        timings_ms: dict[str, int] | None = None,
    ) -> list[CandidatePlace]:
        if len(requirements.participants) != 2:
            raise RecommendationError("邻城模式第一版只支持两个人")
        if any(item.longitude is None or item.latitude is None for item in requirements.participants):
            raise RecommendationError("所有出发地必须先完成地址确认")

        city_participants = _participants_by_city(requirements.participants)
        if requirements.preferred_area_text:
            preferred_city = requirements.preferred_area_city
            if not preferred_city:
                raise RecommendationError("无法确认指定会面区域所在的城市")
            city_participants = {preferred_city: requirements.participants}
        elif len(city_participants) < 2:
            raise RecommendationError("两个出发地属于同一城市，请切换到同城模式")

        gateway_started = perf_counter()
        gateways = await self._find_gateways(list(city_participants))
        _record_timing(timings_ms, "候选城市与门户", gateway_started)

        search_started = perf_counter()
        search_jobs: list[tuple[str, str | None, float, float, int]] = []
        for city, local_participants in city_participants.items():
            if requirements.preferred_area_text:
                search_jobs.append((
                    city,
                    requirements.preferred_area_formatted_address or requirements.preferred_area_text,
                    requirements.preferred_area_longitude or local_participants[0].longitude or 0,
                    requirements.preferred_area_latitude or local_participants[0].latitude or 0,
                    5_000,
                ))
                continue
            gateway = gateways.get(city)
            if gateway:
                search_jobs.append((city, gateway.formatted_address, gateway.longitude, gateway.latitude, 7_000))
            local = local_participants[0]
            search_jobs.append((city, gateway.formatted_address if gateway else None, local.longitude or 0, local.latitude or 0, 8_000))

        shortlisted = await self._search_shortlist(requirements, search_jobs, city_participants)

        if not shortlisted:
            raise RecommendationError("双方城市中都没有找到符合品类和营业时间要求的场所")
        _record_timing(timings_ms, "场所搜索与初筛", search_started)

        if self.semantic_reviewer:
            review_started = perf_counter()
            review = await self.semantic_reviewer.review_candidate_semantics(
                requirements,
                [place for _, place, _ in shortlisted],
            )
            if review.revised_search_keywords and (
                not review.acceptable or review.rejected_poi_ids
            ):
                requirements.search_keywords = review.revised_search_keywords
                if review.revised_target_place_kinds:
                    requirements.target_place_kinds = review.revised_target_place_kinds
                shortlisted = await self._search_shortlist(requirements, search_jobs, city_participants)
                if not shortlisted:
                    raise RecommendationError("语义复核后仍未找到符合主要活动的邻城候选")
            elif review.rejected_poi_ids:
                rejected = set(review.rejected_poi_ids)
                shortlisted = [item for item in shortlisted if item[1].poi_id not in rejected]
                if not shortlisted:
                    raise RecommendationError("语义复核剔除了全部跑题的邻城候选")
            _record_timing(timings_ms, "候选语义复核", review_started)

        if self.atmosphere and self.atmosphere.is_needed(requirements):
            atmosphere_started = perf_counter()
            try:
                await self.atmosphere.enrich([place for _, place, _ in shortlisted], requirements)
            except Exception:
                pass
            _record_timing(timings_ms, "场所氛围检索", atmosphere_started)

        routes_started = perf_counter()
        routed = await asyncio.gather(
            *(
                self._build_candidate(city, place, gateway, requirements.participants)
                for city, place, gateway in shortlisted
            ),
            return_exceptions=True,
        )
        candidates = [item for item in routed if isinstance(item, CandidatePlace)]
        if not candidates:
            raise RecommendationError("找到了邻城候选场所，但高德没有返回双方完整的跨城路线")
        _record_timing(timings_ms, "跨城路线矩阵", routes_started)

        scoring_started = perf_counter()
        weather_by_region = await self._load_weather(candidates, requirements.meeting_time)
        for candidate in candidates:
            weather = weather_by_region.get(candidate.adcode or candidate.city or "")
            score_intercity_candidate(candidate, requirements, weather)
        ranked = rank_intercity_results(candidates)
        _record_timing(timings_ms, "天气与邻城评分", scoring_started)
        return ranked[:3]

    async def _search_shortlist(
        self,
        requirements: MeetingRequirements,
        search_jobs: list[tuple[str, str | None, float, float, int]],
        city_participants: dict[str, list[Participant]],
    ) -> list[tuple[str, PoiPlace, str | None]]:
        keywords = search_terms_for_requirements(requirements) or ["休闲场所"]
        discovered = await asyncio.gather(
            *(
                self.amap.search_nearby(keywords, longitude, latitude, radius=radius, limit=10)
                for _, _, longitude, latitude, radius in search_jobs
            ),
            return_exceptions=True,
        )
        places_by_city: dict[str, dict[str, tuple[PoiPlace, str | None]]] = {
            city: {} for city in city_participants
        }
        for job, result in zip(search_jobs, discovered, strict=True):
            host_city, gateway_name, *_ = job
            if isinstance(result, Exception):
                continue
            for place in result:
                if place.city and _city_key(place.city) != _city_key(host_city):
                    continue
                places_by_city[host_city].setdefault(place.poi_id, (place, gateway_name))

        shortlisted: list[tuple[str, PoiPlace, str | None]] = []
        for city, place_map in places_by_city.items():
            fitted: list[tuple[float, PoiPlace, str | None]] = []
            for place, gateway_name in place_map.values():
                place.opening_verified = verify_open_for_visit(place.opening_hours, requirements.meeting_time)
                if place.opening_hours and place.opening_verified is not True:
                    continue
                fit = venue_fit_score(place, requirements)
                if fit >= 0.15:
                    fitted.append((fit, place, gateway_name))
            fitted.sort(key=lambda item: (-item[0], -(item[1].map_rating or 0)))
            shortlisted.extend((city, place, gateway) for _, place, gateway in fitted[:5])
        return shortlisted

    async def _find_gateways(self, cities: list[str]) -> dict[str, GeocodedOrigin]:
        results = await asyncio.gather(
            *(self.amap.origin_candidates("火车站", city) for city in cities),
            return_exceptions=True,
        )
        gateways: dict[str, GeocodedOrigin] = {}
        for city, result in zip(cities, results, strict=True):
            if isinstance(result, Exception):
                continue
            matches = [item for item in result if not item.city or _city_key(item.city) == _city_key(city)]
            if matches:
                gateways[city] = matches[0]
        return gateways

    async def _build_candidate(
        self,
        city: str,
        place: PoiPlace,
        gateway_name: str | None,
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
            meeting_city=city,
            gateway_name=gateway_name,
            intercity_note="跨城段为地图规划估算，具体铁路班次和车票需另行确认",
        )
        candidate.warnings.append("跨城班次、末班车和车票请在出发前通过铁路官方渠道复核")
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
        regions = list(dict.fromkeys(
            candidate.adcode or candidate.city
            for candidate in candidates
            if candidate.adcode or candidate.city
        ))[:3]
        results = await asyncio.gather(
            *(self.amap.weather(region) for region in regions),
            return_exceptions=True,
        )
        target_date = meeting_time.date().isoformat()
        weather: dict[str, WeatherSummary] = {}
        for region, result in zip(regions, results, strict=True):
            if isinstance(result, Exception):
                continue
            match = next((item for item in result if item.date == target_date), None)
            if match:
                weather[region] = match
        return weather


def score_intercity_candidate(
    candidate: CandidatePlace,
    requirements: MeetingRequirements,
    weather: WeatherSummary | None = None,
) -> CandidatePlace:
    durations = [route.duration_minutes for route in candidate.routes]
    transfers = [route.transfers for route in candidate.routes]
    if not durations:
        candidate.score = 0
        return candidate

    place = PoiPlace(**candidate.model_dump())
    venue_fit = venue_fit_score(place, requirements)
    longest = max(durations)
    average = mean(durations)
    spread = max(durations) - min(durations)
    accessibility = max(0.0, 1 - longest / 300)
    overall = max(0.0, 1 - average / 240)
    simplicity = max(0.0, 1 - mean(transfers) / 4)
    balance = max(0.0, 1 - spread / 180)
    rating = (candidate.map_rating / 5) if candidate.map_rating is not None else 0.7
    weather_fit = 1.0
    weather_text = "" if weather is None else f"{weather.day_weather or ''}{weather.night_weather or ''}"
    if candidate.place_kind in ("park", "district") and any(word in weather_text for word in ("雨", "雪", "雷", "沙")):
        weather_fit = 0.25
        candidate.warnings.append("计划日期可能不适合长时间户外活动")

    favor_bonus = 0.0
    if requirements.favored_participant:
        favored = next((item for item in requirements.participants if item.name == requirements.favored_participant), None)
        if favored and favored.city and candidate.meeting_city:
            if _city_key(favored.city) == _city_key(candidate.meeting_city):
                favor_bonus = 8.0

    candidate.weather = weather
    candidate.score_breakdown = {
        "场所匹配": round(venue_fit * 25, 1),
        "跨城可达": round(accessibility * 25, 1),
        "整体便利": round(overall * 15, 1),
        "换乘体验": round(simplicity * 10, 1),
        "负担平衡": round(balance * 10, 1),
        "地图评分": round(rating * 10, 1),
        "天气适配": round(weather_fit * 5, 1),
    }
    if favor_bonus:
        candidate.score_breakdown["照顾指定参与者"] = favor_bonus
    candidate.score = round(min(100.0, sum(candidate.score_breakdown.values())), 1)
    if longest > 240:
        candidate.warnings.append("至少一人的门到门预计耗时超过 4 小时")
    return candidate


def rank_intercity_results(candidates: list[CandidatePlace]) -> list[CandidatePlace]:
    """Keep the best city first while retaining one alternative-city comparison."""

    ranked = sorted(candidates, key=lambda item: item.score, reverse=True)
    if len(ranked) < 2 or not ranked[0].meeting_city:
        return ranked
    winning_city = _city_key(ranked[0].meeting_city)
    alternate = next(
        (item for item in ranked[1:] if _city_key(item.meeting_city or "") != winning_city),
        None,
    )
    selected = [ranked[0]]
    selected.extend(item for item in ranked[1:] if _city_key(item.meeting_city or "") == winning_city)
    if alternate:
        selected.insert(min(2, len(selected)), alternate)
    selected_ids = {item.poi_id for item in selected}
    selected.extend(item for item in ranked if item.poi_id not in selected_ids)
    return selected


def _participants_by_city(participants: list[Participant]) -> dict[str, list[Participant]]:
    grouped: dict[str, list[Participant]] = {}
    for participant in participants:
        if not participant.city:
            raise RecommendationError(f"无法确认{participant.name}所在的城市")
        existing_city = next((city for city in grouped if _city_key(city) == _city_key(participant.city)), None)
        grouped.setdefault(existing_city or participant.city, []).append(participant)
    return grouped


def _city_key(city: str) -> str:
    return city.strip().removesuffix("市")


def _record_timing(timings_ms: dict[str, int] | None, label: str, started: float) -> None:
    if timings_ms is not None:
        timings_ms[label] = round((perf_counter() - started) * 1000)
