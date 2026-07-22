from app.core.venue import venue_fit_score
from app.models import (
    MeetingRequirements,
    PoiPlace,
    SearchEvidence,
    VenueAtmosphereProfile,
    VenueProfileAssessment,
)
from app.repositories.venue_profiles import VenueProfileRepository
from app.services.atmosphere import AtmosphereService
from app.services.search import SearchProvider


def _place(poi_id: str = "p1", city: str = "上海市") -> PoiPlace:
    return PoiPlace(
        poi_id=poi_id,
        name="测试咖啡馆",
        address="测试路",
        longitude=121.4,
        latitude=31.2,
        city=city,
        type_name="咖啡厅",
    )


def test_evidence_profile_changes_atmosphere_fit_score():
    requirements = MeetingRequirements(
        activity="喝咖啡聊天",
        atmosphere=["安静", "有设计感", "适合约会"],
        search_keywords=["咖啡馆"],
    )
    suitable = _place("good")
    suitable.atmosphere_profile = VenueAtmosphereProfile(
        quiet=0.95,
        design=0.9,
        conversation_friendly=0.9,
        date_friendly=0.9,
        quick_service=0.05,
        confidence=0.9,
        summary="证据明确",
        provider="test",
    )
    quick = _place("quick")
    quick.atmosphere_profile = VenueAtmosphereProfile(
        quiet=0.1,
        design=0.1,
        conversation_friendly=0.1,
        date_friendly=0.1,
        quick_service=0.95,
        confidence=0.9,
        summary="以快速取餐为主",
        provider="test",
    )
    assert venue_fit_score(suitable, requirements) > venue_fit_score(quick, requirements)


def test_profile_repository_round_trip_marks_cache(tmp_path):
    repository = VenueProfileRepository(tmp_path / "test.db")
    repository.initialize()
    profile = VenueAtmosphereProfile(
        provider="tavily",
        confidence=0.7,
        summary="适合聊天",
    )
    repository.save("p1", profile, "auto")
    restored = repository.get("p1", "auto")
    assert restored is not None
    assert restored.cached is True
    assert restored.summary == "适合聊天"


class _RecordingProvider(SearchProvider):
    name = "test"

    def __init__(self) -> None:
        self.queries: list[str] = []

    async def search(self, query: str, limit: int = 5):
        self.queries.append(query)
        return [SearchEvidence(title="评价", url="https://review.test", snippet="环境安静", source=self.name)]


class _FakeDeepSeek:
    async def build_venue_profiles(self, requirements, venues):
        return [
            VenueProfileAssessment(
                poi_id=place.poi_id,
                quiet=0.9,
                design=0.7,
                conversation_friendly=0.9,
                date_friendly=0.8,
                quick_service=0.1,
                confidence=0.8,
                summary="公开评价提到环境安静",
            )
            for place, _ in venues
        ]


async def test_atmosphere_search_never_sends_participant_origins(tmp_path):
    provider = _RecordingProvider()
    repository = VenueProfileRepository(tmp_path / "test.db")
    repository.initialize()
    service = AtmosphereService(provider, _FakeDeepSeek(), repository)  # type: ignore[arg-type]
    requirements = MeetingRequirements(
        activity="聊天",
        atmosphere=["安静"],
        constraints=["不要吵"],
    )
    await service.enrich([_place()], requirements)
    assert provider.queries
    assert "上海市" in provider.queries[0]
    assert "测试咖啡馆" in provider.queries[0]
    assert "用户住址" not in provider.queries[0]
