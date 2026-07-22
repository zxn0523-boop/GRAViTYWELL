import asyncio

from app.models import MeetingRequirements, PoiPlace, VenueAtmosphereProfile
from app.repositories.venue_profiles import VenueProfileRepository
from app.services.deepseek import DeepSeekService
from app.services.search import SearchProvider


ATMOSPHERE_MARKERS = (
    "安静", "聊天", "约会", "设计", "氛围", "特别", "私密", "浪漫", "久坐", "景观", "艺术",
)


class AtmosphereService:
    """Adds optional, evidence-grounded atmosphere profiles to Amap POIs."""

    def __init__(
        self,
        provider: SearchProvider,
        deepseek: DeepSeekService,
        repository: VenueProfileRepository,
        candidate_limit: int = 5,
    ) -> None:
        self.provider = provider
        self.deepseek = deepseek
        self.repository = repository
        self.candidate_limit = max(1, candidate_limit)

    def is_needed(self, requirements: MeetingRequirements) -> bool:
        text = " ".join(
            filter(None, [requirements.activity, *requirements.atmosphere, *requirements.constraints])
        )
        return any(marker in text for marker in ATMOSPHERE_MARKERS)

    async def enrich(
        self,
        places: list[PoiPlace],
        requirements: MeetingRequirements,
    ) -> list[PoiPlace]:
        if not places or not self.is_needed(requirements):
            return places

        targets = self._balanced_targets(places)
        uncached: list[PoiPlace] = []
        for place in targets:
            cached = self.repository.get(place.poi_id, self.provider.name)
            if cached:
                place.atmosphere_profile = cached
            else:
                uncached.append(place)
        if not uncached:
            return places

        evidence_groups = await asyncio.gather(
            *(self._search_place(place, requirements) for place in uncached),
            return_exceptions=True,
        )
        assessable = [
            (place, evidence)
            for place, evidence in zip(uncached, evidence_groups, strict=True)
            if isinstance(evidence, list) and evidence
        ]
        if not assessable:
            return places

        assessments = await self.deepseek.build_venue_profiles(requirements, assessable)
        by_id = {assessment.poi_id: assessment for assessment in assessments}
        for place, evidence in assessable:
            assessment = by_id.get(place.poi_id)
            if not assessment:
                continue
            profile = VenueAtmosphereProfile(
                **assessment.model_dump(exclude={"poi_id"}),
                provider=evidence[0].source,
                evidence=evidence[:3],
            )
            place.atmosphere_profile = profile
            self.repository.save(place.poi_id, profile, self.provider.name)
        return places

    async def _search_place(self, place: PoiPlace, requirements: MeetingRequirements):
        preferences = " ".join(requirements.atmosphere + requirements.constraints)
        query = f'"{place.name}" {place.city or ""} 环境 氛围 评价 {preferences}'.strip()
        # Participant origins and route data are deliberately excluded from this query.
        return await self.provider.search(query, limit=4)

    def _balanced_targets(self, places: list[PoiPlace]) -> list[PoiPlace]:
        by_city: dict[str, list[PoiPlace]] = {}
        for place in places:
            by_city.setdefault(place.city or "未知城市", []).append(place)
        selected: list[PoiPlace] = []
        while len(selected) < self.candidate_limit and any(by_city.values()):
            for group in by_city.values():
                if group and len(selected) < self.candidate_limit:
                    selected.append(group.pop(0))
        return selected
