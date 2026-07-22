from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import ROOT_DIR, get_settings
from app.models import ChatRequest, ChatResponse, CreateSessionResponse
from app.repositories.sessions import SessionRepository
from app.repositories.venue_profiles import VenueProfileRepository
from app.services.amap import AmapService
from app.services.deepseek import DeepSeekError, DeepSeekService
from app.services.atmosphere import AtmosphereService
from app.services.search import build_search_provider
from app.services.orchestrator import ConversationOrchestrator
from app.services.intercity import IntercityRecommendationService
from app.services.recommender import RecommendationError, RecommendationService


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    repository = SessionRepository(settings.database_path, settings.session_ttl_hours)
    repository.initialize()
    repository.purge_expired()
    venue_profiles = VenueProfileRepository(
        settings.database_path,
        settings.venue_profile_cache_days,
    )
    venue_profiles.initialize()
    deepseek = DeepSeekService(
        settings.deepseek_api_key,
        settings.deepseek_model,
        settings.request_timeout_seconds,
        profile_model=settings.deepseek_profile_model,
    )
    amap = AmapService(settings.amap_api_key, settings.request_timeout_seconds)
    search_provider = build_search_provider(settings)
    atmosphere = (
        AtmosphereService(
            search_provider,
            deepseek,
            venue_profiles,
            settings.atmosphere_candidate_limit,
        )
        if search_provider else None
    )
    app.state.sessions = repository
    app.state.deepseek = deepseek
    app.state.amap = amap
    app.state.orchestrator = ConversationOrchestrator(
        repository,
        deepseek,
        RecommendationService(amap, atmosphere, deepseek),
        IntercityRecommendationService(amap, atmosphere, deepseek),
    )
    yield
    if search_provider:
        await search_provider.close()
    await deepseek.close()
    await amap.close()


app = FastAPI(
    title="GravityWell",
    description="多人公平会面地点推荐 Agent",
    version="0.1.0",
    lifespan=lifespan,
)

STATIC_DIR = ROOT_DIR / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def sessions(request: Request) -> SessionRepository:
    return request.app.state.sessions


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "index.html",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/sessions", response_model=CreateSessionResponse)
async def create_session(request: Request) -> CreateSessionResponse:
    state = sessions(request).create()
    return CreateSessionResponse(session_id=state.session_id)


@app.post("/api/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest, request: Request) -> ChatResponse:
    try:
        return await request.app.state.orchestrator.chat(
            payload.session_id,
            payload.message,
            payload.mode,
        )
    except (DeepSeekError, RecommendationError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"外部服务暂时不可用：{exc}") from exc


@app.delete("/api/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def restart_session(session_id: str, request: Request) -> Response:
    sessions(request).delete(session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/api/sessions/{session_id}/accept", response_model=ChatResponse)
async def accept_recommendation(session_id: str, request: Request) -> ChatResponse:
    repository = sessions(request)
    state = repository.get(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="会话不存在或已经过期")
    if not state.candidates:
        raise HTTPException(status_code=409, detail="当前还没有可采纳的推荐")
    repository.delete(session_id)
    return ChatResponse(
        session_id=None,
        phase="completed",
        mode=state.requirements.mode,
        reply="本次方案已采纳。出发地、对话和推荐结果均已清除。",
        cleared=True,
    )
