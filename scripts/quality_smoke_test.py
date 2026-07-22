"""Real-API regression for city context, weather, atmosphere, and favoring a participant."""

import asyncio
import sys
from pathlib import Path

from app.config import get_settings
from app.repositories.sessions import SessionRepository
from app.services.amap import AmapService
from app.services.deepseek import DeepSeekService
from app.services.orchestrator import ConversationOrchestrator
from app.services.recommender import RecommendationService


async def main() -> None:
    settings = get_settings()
    database = Path("quality-smoke-test.db")
    repository = SessionRepository(database)
    repository.initialize()
    deepseek = DeepSeekService(settings.deepseek_api_key, settings.deepseek_model, 60)
    amap = AmapService(settings.amap_api_key, 60)
    orchestrator = ConversationOrchestrator(repository, deepseek, RecommendationService(amap))
    session_id = None
    metadata_only = "--metadata-only" in sys.argv
    try:
        first = await orchestrator.chat(
            None,
            "我在静安寺，小张在松江大学城，本周五下午两点都乘公共交通，"
            "想找有设计感、安静、适合聊天的咖啡馆。",
        )
        session_id = first.session_id
        state = repository.get(session_id)
        print({
            "phase": first.phase,
            "reply": first.reply,
            "requirements": state.requirements.model_dump(mode="json"),
            "resolved_origins": [
                (participant.name, participant.formatted_address, participant.city)
                for participant in state.requirements.participants
            ]
        })

        before = await orchestrator.chat(session_id, "确认，这两个出发地正确。")
        print({
            "initial_candidates": [
                {
                    "name": candidate.name,
                    "kind": candidate.place_kind,
                    "rating": candidate.map_rating,
                    "opening_hours": candidate.opening_hours,
                    "opening_verified": candidate.opening_verified,
                }
                for candidate in before.candidates
            ],
            "weather_loaded": [bool(candidate.weather) for candidate in before.candidates],
            "xiaozhang_minutes": [
                next(route.duration_minutes for route in candidate.routes if route.participant_name == "小张")
                for candidate in before.candidates
            ],
        })

        if metadata_only:
            return

        favored = await orchestrator.chat(session_id, "我愿意多走一点，这次更照顾小张。")
        favored_state = repository.get(session_id)
        print({
            "priority": favored_state.requirements.priority,
            "favored_participant": favored_state.requirements.favored_participant,
            "favored_candidates": [candidate.name for candidate in favored.candidates],
            "xiaozhang_minutes": [
                next(route.duration_minutes for route in candidate.routes if route.participant_name == "小张")
                for candidate in favored.candidates
            ],
        })
    finally:
        if session_id:
            repository.delete(session_id)
        await deepseek.close()
        await amap.close()
        if database.exists():
            database.unlink()


if __name__ == "__main__":
    asyncio.run(main())
