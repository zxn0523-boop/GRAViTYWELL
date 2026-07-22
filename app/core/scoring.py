from statistics import mean
from math import asin, cos, radians, sin, sqrt

from app.core.venue import venue_fit_score
from app.models import CandidatePlace, MeetingRequirements, PoiPlace, WeatherSummary


WEIGHT_PROFILES = {
    "balanced": {"match": 0.25, "rating": 0.10, "fairness": 0.30, "convenience": 0.20, "transfers": 0.10, "weather": 0.05},
    "shortest": {"match": 0.20, "rating": 0.10, "fairness": 0.15, "convenience": 0.40, "transfers": 0.10, "weather": 0.05},
    "few_transfers": {"match": 0.20, "rating": 0.10, "fairness": 0.20, "convenience": 0.15, "transfers": 0.30, "weather": 0.05},
    "venue_first": {"match": 0.40, "rating": 0.15, "fairness": 0.20, "convenience": 0.10, "transfers": 0.10, "weather": 0.05},
    "favor_person": {"match": 0.18, "rating": 0.08, "fairness": 0.10, "convenience": 0.10, "transfers": 0.04, "weather": 0.05, "care": 0.45},
    "location_first": {"match": 0.20, "rating": 0.07, "fairness": 0.10, "convenience": 0.05, "transfers": 0.05, "weather": 0.03, "location": 0.50},
}
LABELS = {
    "match": "需求匹配",
    "rating": "地图口碑",
    "fairness": "交通公平",
    "convenience": "整体便利",
    "transfers": "换乘体验",
    "weather": "天气适配",
    "care": "照顾偏向",
    "location": "位置要求",
}
MAX_DURATION_MINUTES = 90
MAX_TRANSFERS = 3


def score_candidate(
    candidate: CandidatePlace,
    requirements: MeetingRequirements,
    weather: WeatherSummary | None = None,
) -> tuple[CandidatePlace, bool]:
    durations = [route.duration_minutes for route in candidate.routes]
    transfers = [route.transfers for route in candidate.routes]
    if not durations:
        candidate.score = 0
        candidate.warnings.append("缺少路线数据")
        return candidate, False

    poi = PoiPlace(
        poi_id=candidate.poi_id,
        name=candidate.name,
        address=candidate.address,
        longitude=candidate.longitude,
        latitude=candidate.latitude,
        city=candidate.city,
        adcode=candidate.adcode,
        type_name=candidate.type_name,
        place_kind=candidate.place_kind,
        map_rating=candidate.map_rating,
        average_cost=candidate.average_cost,
        opening_hours=candidate.opening_hours,
        opening_verified=candidate.opening_verified,
    )
    duration_gap = max(durations) - min(durations)
    average_duration = mean(durations)
    components = {
        "match": venue_fit_score(poi, requirements),
        "rating": candidate.map_rating / 5 if candidate.map_rating is not None else 0.7,
        "fairness": max(0.0, 1 - duration_gap / 60),
        "convenience": max(0.0, 1 - average_duration / 120),
        "transfers": max(0.0, 1 - mean(transfers) / 3),
    }
    weather_score, weather_warning = _weather_score(requirements, weather)
    components["weather"] = weather_score

    if requirements.priority == "favor_person" and requirements.favored_participant:
        favored_route = next(
            (
                route
                for route in candidate.routes
                if route.participant_name == requirements.favored_participant
            ),
            None,
        )
        components["care"] = (
            max(0.0, 1 - favored_route.duration_minutes / 90) * 0.8
            + max(0.0, 1 - favored_route.transfers / 3) * 0.2
            if favored_route
            else 0.0
        )

    if (
        requirements.priority == "location_first"
        and requirements.preferred_area_longitude is not None
        and requirements.preferred_area_latitude is not None
    ):
        distance = _haversine_km(
            requirements.preferred_area_latitude,
            requirements.preferred_area_longitude,
            candidate.latitude,
            candidate.longitude,
        )
        components["location"] = max(0.0, 1 - distance / 5)

    weights = WEIGHT_PROFILES[requirements.priority]
    if any(key not in components for key in weights):
        weights = WEIGHT_PROFILES["balanced"]
    if weather_warning:
        candidate.warnings.append(weather_warning)
    candidate.weather = weather
    candidate.score_breakdown = {
        LABELS[key]: round(value * 100, 1)
        for key, value in components.items()
        if key in weights
    }
    candidate.score = round(
        sum(components[key] * weight for key, weight in weights.items()) * 100,
        1,
    )

    eligible = True
    if max(durations) > MAX_DURATION_MINUTES:
        candidate.warnings.append(f"有人通勤超过默认上限 {MAX_DURATION_MINUTES} 分钟")
        eligible = False
    if max(transfers) > MAX_TRANSFERS:
        candidate.warnings.append(f"有人换乘超过默认上限 {MAX_TRANSFERS} 次")
        eligible = False
    return candidate, eligible


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    earth_radius = 6371
    d_lat = radians(lat2 - lat1)
    d_lon = radians(lon2 - lon1)
    value = sin(d_lat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(d_lon / 2) ** 2
    return 2 * earth_radius * asin(sqrt(value))


def _weather_score(
    requirements: MeetingRequirements,
    weather: WeatherSummary | None,
) -> tuple[float, str | None]:
    if weather is None:
        return 0.8, "约会日期超出近期天气预报范围，建议临近时重新计算"
    text = " ".join(filter(None, [weather.day_weather, weather.night_weather]))
    activity_text = " ".join(
        filter(None, [requirements.activity, *requirements.atmosphere, *requirements.search_keywords])
    )
    outdoor_markers = ("户外", "公园", "散步", "骑行", "露营", "街区")
    bad_weather = any(marker in text for marker in ("雨", "雪", "雷", "沙尘"))
    if bad_weather and any(marker in activity_text for marker in outdoor_markers):
        return 0.2, f"预报为{text}，不适合纯户外安排"
    temperatures = [
        int(value)
        for value in (weather.day_temperature, weather.night_temperature)
        if value and value.lstrip("-").isdigit()
    ]
    if temperatures and max(temperatures) >= 35 and any(marker in activity_text for marker in outdoor_markers):
        return 0.4, "预报高温，建议增加室内备选"
    return 1.0, None
