from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class TransportMode(StrEnum):
    TRANSIT = "transit"
    DRIVING = "driving"


class MeetingMode(StrEnum):
    AUTO = "auto"
    SAME_CITY = "same_city"
    INTERCITY = "intercity"


class SessionPhase(StrEnum):
    COLLECTING = "collecting"
    CONFIRMING_ORIGINS = "confirming_origins"
    READY = "ready"
    COMPARING_CITIES = "comparing_cities"
    RECOMMENDED = "recommended"


class Participant(BaseModel):
    name: str
    origin_text: str
    transport_mode: TransportMode | None = None
    longitude: float | None = None
    latitude: float | None = None
    formatted_address: str | None = None
    city: str | None = None
    city_code: str | None = None
    adcode: str | None = None


class MeetingRequirements(BaseModel):
    mode: MeetingMode = MeetingMode.SAME_CITY
    participants: list[Participant] = Field(default_factory=list, max_length=4)
    meeting_time: datetime | None = None
    meeting_time_text: str | None = None
    activity: str | None = None
    atmosphere: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    search_keywords: list[str] = Field(default_factory=list)
    priority: Literal["balanced", "shortest", "few_transfers", "venue_first", "favor_person", "location_first"] = "balanced"
    favored_participant: str | None = None
    preferred_area_text: str | None = None
    preferred_area_longitude: float | None = None
    preferred_area_latitude: float | None = None
    preferred_area_formatted_address: str | None = None
    preferred_area_city: str | None = None
    preferred_area_adcode: str | None = None

    @field_validator("participants")
    @classmethod
    def unique_names(cls, value: list[Participant]) -> list[Participant]:
        seen: set[str] = set()
        result: list[Participant] = []
        for participant in value:
            if participant.name not in seen:
                seen.add(participant.name)
                result.append(participant)
        return result


class ConversationMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime = Field(default_factory=datetime.now)


class RouteExperience(BaseModel):
    participant_name: str
    duration_minutes: int
    transfers: int = 0
    walking_minutes: int = 0
    distance_km: float | None = None
    summary: str = ""


class SearchEvidence(BaseModel):
    title: str
    url: str
    snippet: str = ""
    source: str
    published_at: str | None = None


class VenueAtmosphereProfile(BaseModel):
    quiet: float = Field(default=0.5, ge=0, le=1)
    design: float = Field(default=0.5, ge=0, le=1)
    conversation_friendly: float = Field(default=0.5, ge=0, le=1)
    date_friendly: float = Field(default=0.5, ge=0, le=1)
    quick_service: float = Field(default=0.5, ge=0, le=1)
    confidence: float = Field(default=0, ge=0, le=1)
    summary: str = "公开信息不足，氛围需要到店前确认"
    provider: str
    evidence: list[SearchEvidence] = Field(default_factory=list)
    cached: bool = False


class CandidatePlace(BaseModel):
    poi_id: str
    name: str
    address: str
    longitude: float
    latitude: float
    city: str | None = None
    adcode: str | None = None
    type_name: str | None = None
    place_kind: Literal["venue", "park", "district", "attraction"] = "venue"
    map_rating: float | None = Field(default=None, ge=0, le=5)
    average_cost: float | None = Field(default=None, ge=0)
    opening_hours: str | None = None
    opening_verified: bool | None = None
    routes: list[RouteExperience] = Field(default_factory=list)
    score: float = 0
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    weather: "WeatherSummary | None" = None
    recommendation_reason: str | None = None
    meeting_city: str | None = None
    gateway_name: str | None = None
    intercity_note: str | None = None
    atmosphere_profile: VenueAtmosphereProfile | None = None


class GeocodedOrigin(BaseModel):
    query: str
    formatted_address: str
    longitude: float
    latitude: float
    city: str | None = None
    city_code: str | None = None
    adcode: str | None = None


class PoiPlace(BaseModel):
    poi_id: str
    name: str
    address: str = ""
    longitude: float
    latitude: float
    city: str | None = None
    adcode: str | None = None
    type_name: str | None = None
    place_kind: Literal["venue", "park", "district", "attraction"] = "venue"
    map_rating: float | None = Field(default=None, ge=0, le=5)
    average_cost: float | None = Field(default=None, ge=0)
    opening_hours: str | None = None
    opening_verified: bool | None = None
    atmosphere_profile: VenueAtmosphereProfile | None = None


class WeatherSummary(BaseModel):
    city: str
    date: str
    day_weather: str | None = None
    night_weather: str | None = None
    day_temperature: str | None = None
    night_temperature: str | None = None
    day_wind: str | None = None
    night_wind: str | None = None


class SessionState(BaseModel):
    session_id: str
    phase: SessionPhase = SessionPhase.COLLECTING
    requirements: MeetingRequirements = Field(default_factory=MeetingRequirements)
    history: list[ConversationMessage] = Field(default_factory=list)
    candidates: list[CandidatePlace] = Field(default_factory=list)
    origins_confirmed: bool = False
    mode_auto: bool = True
    requested_mode: MeetingMode | None = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class ChatRequest(BaseModel):
    session_id: str | None = None
    mode: MeetingMode = MeetingMode.AUTO
    message: str = Field(min_length=1, max_length=2000)


class ChatResponse(BaseModel):
    session_id: str | None
    phase: SessionPhase | Literal["completed"]
    mode: MeetingMode = MeetingMode.SAME_CITY
    reply: str
    candidates: list[CandidatePlace] = Field(default_factory=list)
    timings_ms: dict[str, int] = Field(default_factory=dict)
    cleared: bool = False


class CreateSessionResponse(BaseModel):
    session_id: str


class ExtractionResult(BaseModel):
    intent: Literal["update", "confirm_origins", "accept", "restart"] = "update"
    participants: list[Participant] | None = None
    meeting_time: datetime | None = None
    meeting_time_text: str | None = None
    activity: str | None = None
    atmosphere: list[str] | None = None
    constraints: list[str] | None = None
    search_keywords: list[str] | None = None
    priority: Literal["balanced", "shortest", "few_transfers", "venue_first", "favor_person", "location_first"] | None = None
    favored_participant: str | None = None
    preferred_area_text: str | None = None
    question: str | None = None


class CandidateExplanation(BaseModel):
    poi_id: str
    reason: str


class RecommendationNarrative(BaseModel):
    intro: str
    explanations: list[CandidateExplanation] = Field(default_factory=list)


class VenueProfileAssessment(BaseModel):
    poi_id: str
    quiet: float = Field(ge=0, le=1)
    design: float = Field(ge=0, le=1)
    conversation_friendly: float = Field(ge=0, le=1)
    date_friendly: float = Field(ge=0, le=1)
    quick_service: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    summary: str


class VenueProfileBatch(BaseModel):
    profiles: list[VenueProfileAssessment] = Field(default_factory=list)
