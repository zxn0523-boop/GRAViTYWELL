import asyncio
from collections.abc import Iterable

import httpx

from app.core.venue import infer_place_kind
from app.models import (
    GeocodedOrigin,
    Participant,
    PoiPlace,
    RouteExperience,
    TransportMode,
    WeatherSummary,
)


class AmapError(RuntimeError):
    pass


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    return ""


def _first_text(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, list) and value and isinstance(value[0], str):
        return value[0]
    return None


def _number(value: object, default: float = 0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _optional_number(value: object) -> float | None:
    if isinstance(value, list):
        value = value[0] if value else None
    try:
        number = float(value)
        return number if number >= 0 else None
    except (TypeError, ValueError):
        return None


class AmapService:
    BASE_URL = "https://restapi.amap.com/v3"

    def __init__(
        self,
        api_key: str,
        timeout_seconds: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._route_semaphore = asyncio.Semaphore(6)

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def geocode(self, address: str, city: str | None = None) -> list[GeocodedOrigin]:
        params = {"address": address}
        if city:
            params["city"] = city
        payload = await self._get("/geocode/geo", params)
        results: list[GeocodedOrigin] = []
        for item in payload.get("geocodes", [])[:3]:
            location = _text(item.get("location")).split(",")
            if len(location) != 2:
                continue
            results.append(
                GeocodedOrigin(
                    query=address,
                    formatted_address=_text(item.get("formatted_address")) or address,
                    longitude=_number(location[0]),
                    latitude=_number(location[1]),
                    city=_first_text(item.get("city")) or _first_text(item.get("province")),
                    city_code=_first_text(item.get("citycode")),
                    adcode=_first_text(item.get("adcode")),
                )
            )
        if not results:
            raise AmapError(f"高德无法识别出发地“{address}”")
        return results

    async def origin_candidates(
        self,
        query: str,
        preferred_city: str | None = None,
    ) -> list[GeocodedOrigin]:
        """Combine address geocoding and POI search for landmark-like origins."""

        geocoded, poi_matches = await asyncio.gather(
            self.geocode(query, preferred_city),
            self._search_origin_pois(query, preferred_city),
            return_exceptions=True,
        )
        combined: list[GeocodedOrigin] = []
        for result in (geocoded, poi_matches):
            if not isinstance(result, Exception):
                combined.extend(result)
        unique: dict[tuple[float, float], GeocodedOrigin] = {}
        for item in combined:
            unique.setdefault((round(item.longitude, 5), round(item.latitude, 5)), item)
        if not unique:
            raise AmapError(f"高德无法识别出发地“{query}”")
        return list(unique.values())[:6]

    async def search_nearby(
        self,
        keywords: Iterable[str],
        longitude: float,
        latitude: float,
        radius: int = 12_000,
        limit: int = 8,
    ) -> list[PoiPlace]:
        keyword_text = "|".join(keyword for keyword in keywords if keyword) or "商场|咖啡馆|公园"
        payload = await self._get(
            "/place/around",
            {
                "keywords": keyword_text,
                "location": f"{longitude:.6f},{latitude:.6f}",
                "radius": min(radius, 50_000),
                "sortrule": "distance",
                "offset": min(limit, 20),
                "page": 1,
                "extensions": "all",
            },
        )
        places: list[PoiPlace] = []
        for item in payload.get("pois", []):
            location = _text(item.get("location")).split(",")
            if len(location) != 2:
                continue
            biz_ext = item.get("biz_ext") if isinstance(item.get("biz_ext"), dict) else {}
            business = item.get("business") if isinstance(item.get("business"), dict) else {}
            name = _text(item.get("name"))
            type_name = _first_text(item.get("type"))
            opening_hours = (
                _first_text(biz_ext.get("open_time"))
                or _first_text(business.get("opentime_week"))
                or _first_text(business.get("opentime_today"))
            )
            rating = _optional_number(biz_ext.get("rating"))
            if rating is None:
                rating = _optional_number(business.get("rating"))
            cost = _optional_number(biz_ext.get("cost"))
            if cost is None:
                cost = _optional_number(business.get("cost"))
            places.append(
                PoiPlace(
                    poi_id=_text(item.get("id")) or f"{location[0]},{location[1]}",
                    name=name,
                    address=_first_text(item.get("address")) or "地址信息暂缺",
                    longitude=_number(location[0]),
                    latitude=_number(location[1]),
                    city=_first_text(item.get("cityname")),
                    adcode=_first_text(item.get("adcode")),
                    type_name=type_name,
                    place_kind=infer_place_kind(name, type_name),
                    map_rating=rating,
                    average_cost=cost,
                    opening_hours=opening_hours,
                )
            )
        return places

    async def route(self, participant: Participant, destination: PoiPlace) -> RouteExperience:
        if participant.longitude is None or participant.latitude is None:
            raise AmapError(f"{participant.name} 的出发地还没有坐标")
        origin = f"{participant.longitude:.6f},{participant.latitude:.6f}"
        target = f"{destination.longitude:.6f},{destination.latitude:.6f}"
        async with self._route_semaphore:
            if participant.transport_mode == TransportMode.DRIVING:
                return await self._driving_route(participant.name, origin, target)
            return await self._transit_route(participant, destination, origin, target)

    async def weather(self, city: str) -> list[WeatherSummary]:
        payload = await self._get("/weather/weatherInfo", {"city": city, "extensions": "all"})
        forecasts = payload.get("forecasts", [])
        if not forecasts:
            return []
        city_name = _text(forecasts[0].get("city")) or city
        return [
            WeatherSummary(
                city=city_name,
                date=_text(cast.get("date")),
                day_weather=_text(cast.get("dayweather")) or None,
                night_weather=_text(cast.get("nightweather")) or None,
                day_temperature=_text(cast.get("daytemp")) or None,
                night_temperature=_text(cast.get("nighttemp")) or None,
                day_wind=_text(cast.get("daywind")) or None,
                night_wind=_text(cast.get("nightwind")) or None,
            )
            for cast in forecasts[0].get("casts", [])
        ]

    async def _driving_route(self, name: str, origin: str, destination: str) -> RouteExperience:
        payload = await self._get(
            "/direction/driving",
            {"origin": origin, "destination": destination, "strategy": 0, "extensions": "base"},
        )
        paths = payload.get("route", {}).get("paths", [])
        if not paths:
            raise AmapError(f"没有找到{name}的驾车路线")
        path = paths[0]
        duration = max(1, round(_number(path.get("duration")) / 60))
        distance = _number(path.get("distance")) / 1000
        return RouteExperience(
            participant_name=name,
            duration_minutes=duration,
            distance_km=round(distance, 1),
            summary=f"驾车约 {duration} 分钟，{distance:.1f} 公里",
        )

    async def _transit_route(
        self,
        participant: Participant,
        destination: PoiPlace,
        origin: str,
        target: str,
    ) -> RouteExperience:
        city = participant.adcode or participant.city_code or participant.city
        destination_city = destination.adcode or destination.city
        if not city or not destination_city:
            raise AmapError(f"缺少{participant.name}公共交通规划所需的城市信息")
        payload = await self._get(
            "/direction/transit/integrated",
            {
                "origin": origin,
                "destination": target,
                "city": city,
                "cityd": destination_city,
                "strategy": 0,
                "nightflag": 0,
                "extensions": "base",
            },
        )
        transits = payload.get("route", {}).get("transits", [])
        if not transits:
            raise AmapError(f"没有找到{participant.name}的公共交通路线")
        transit = transits[0]
        duration = max(1, round(_number(transit.get("duration")) / 60))
        walking_distance = _number(transit.get("walking_distance"))
        distance = _number(transit.get("distance")) / 1000
        transit_leg_count = 0
        for segment in transit.get("segments", []):
            bus = segment.get("bus", {}) if isinstance(segment, dict) else {}
            if isinstance(bus, dict) and bus.get("buslines"):
                transit_leg_count += 1
            railway = segment.get("railway", {}) if isinstance(segment, dict) else {}
            if isinstance(railway, dict) and railway.get("name"):
                transit_leg_count += 1
        transfers = max(0, transit_leg_count - 1)
        walking_minutes = round(walking_distance / 75)
        return RouteExperience(
            participant_name=participant.name,
            duration_minutes=duration,
            transfers=transfers,
            walking_minutes=walking_minutes,
            distance_km=round(distance, 1),
            summary=f"公共交通约 {duration} 分钟，换乘 {transfers} 次，步行约 {walking_minutes} 分钟",
        )

    async def _search_origin_pois(
        self,
        query: str,
        city: str | None,
    ) -> list[GeocodedOrigin]:
        params: dict[str, object] = {
            "keywords": query,
            "offset": 6,
            "page": 1,
            "extensions": "base",
        }
        if city:
            params["city"] = city
            params["citylimit"] = "true"
        payload = await self._get("/place/text", params)
        results: list[GeocodedOrigin] = []
        for item in payload.get("pois", []):
            location = _text(item.get("location")).split(",")
            if len(location) != 2:
                continue
            city_name = _first_text(item.get("cityname"))
            district = _first_text(item.get("adname"))
            address = _first_text(item.get("address"))
            name = _text(item.get("name"))
            formatted = "".join(
                part for part in (city_name, district, address, name) if part
            )
            results.append(
                GeocodedOrigin(
                    query=query,
                    formatted_address=formatted or name or query,
                    longitude=_number(location[0]),
                    latitude=_number(location[1]),
                    city=city_name,
                    city_code=_first_text(item.get("citycode")),
                    adcode=_first_text(item.get("adcode")),
                )
            )
        return results

    async def _get(self, path: str, params: dict[str, object]) -> dict:
        response = await self.client.get(
            f"{self.BASE_URL}{path}",
            params={"key": self.api_key, **params},
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != "1":
            info = payload.get("info") or "未知错误"
            raise AmapError(f"高德服务返回错误：{info}")
        return payload
