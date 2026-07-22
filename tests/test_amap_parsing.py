import httpx

from app.models import Participant, PoiPlace, TransportMode
from app.services.amap import AmapService


def mock_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/geocode/geo"):
        return httpx.Response(200, json={
            "status": "1",
            "geocodes": [{
                "formatted_address": "上海市杨浦区五角场",
                "location": "121.5148,31.3013",
                "city": "上海市",
                "citycode": "021",
                "adcode": "310110",
            }],
        })
    if path.endswith("/direction/driving"):
        return httpx.Response(200, json={
            "status": "1",
            "route": {"paths": [{"duration": "2400", "distance": "28500"}]},
        })
    if path.endswith("/place/around"):
        return httpx.Response(200, json={
            "status": "1",
            "pois": [{
                "id": "poi-1",
                "name": "测试艺术咖啡",
                "address": "测试路1号",
                "location": "121.4,31.2",
                "cityname": "上海市",
                "adcode": "310106",
                "type": "餐饮服务;咖啡厅",
                "biz_ext": {"rating": "4.7", "cost": "68", "open_time": "10:00-22:00"},
            }],
        })
    return httpx.Response(404, json={"status": "0", "info": "NOT_FOUND"})


async def test_geocode_and_driving_route() -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    service = AmapService("test-key", client=client)
    origins = await service.geocode("五角场")
    assert origins[0].formatted_address == "上海市杨浦区五角场"

    participant = Participant(
        name="我",
        origin_text="五角场",
        longitude=origins[0].longitude,
        latitude=origins[0].latitude,
        transport_mode=TransportMode.DRIVING,
    )
    destination = PoiPlace(
        poi_id="poi-1",
        name="测试地点",
        longitude=121.4,
        latitude=31.2,
    )
    route = await service.route(participant, destination)
    assert route.duration_minutes == 40
    assert route.distance_km == 28.5
    await client.aclose()


async def test_poi_metadata_is_parsed_for_ranking_and_hours() -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    service = AmapService("test-key", client=client)
    places = await service.search_nearby(["咖啡馆"], 121.4, 31.2)
    assert places[0].map_rating == 4.7
    assert places[0].average_cost == 68
    assert places[0].opening_hours == "10:00-22:00"
    await client.aclose()
