from datetime import datetime

from app.models import CandidatePlace, WeatherSummary
from app.services.recommender import RecommendationService


class FakeAmap:
    def __init__(self) -> None:
        self.requested_region = None

    async def weather(self, region: str):
        self.requested_region = region
        return [
            WeatherSummary(
                city="上海市",
                date="2026-07-25",
                day_weather="晴",
                night_weather="多云",
                day_temperature="32",
                night_temperature="27",
            )
        ]


async def test_weather_uses_city_when_poi_has_no_adcode() -> None:
    amap = FakeAmap()
    service = RecommendationService(amap)  # type: ignore[arg-type]
    candidates = [
        CandidatePlace(
            poi_id="one", name="咖啡馆", address="上海",
            longitude=121.4, latitude=31.2, city="上海市", adcode=None,
        )
    ]
    result = await service._load_weather(
        candidates,
        datetime.fromisoformat("2026-07-25T14:00:00+08:00"),
    )
    assert amap.requested_region == "上海市"
    assert result["上海市"].day_weather == "晴"
