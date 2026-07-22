"""Manual real-API smoke test. It prints no credentials and clears its session."""

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
    database = Path("smoke-test.db")
    repository = SessionRepository(database)
    repository.initialize()
    deepseek = DeepSeekService(settings.deepseek_api_key, settings.deepseek_model, 60)
    amap = AmapService(settings.amap_api_key, 60)
    orchestrator = ConversationOrchestrator(repository, deepseek, RecommendationService(amap))
    session_id = None
    intercity = len(sys.argv) > 1 and sys.argv[1] == "--intercity"
    message = (
        "我从上海五角场出发，小张从苏州工业园区出发，"
        "本周六下午两点都坐公共交通，想找安静、有设计感、适合聊天的咖啡馆。"
        if intercity
        else
        "我从上海五角场出发，小张从上海虹桥火车站出发，"
        "本周六下午两点都坐公共交通，想找安静、有设计感、适合聊天的咖啡馆。"
    )
    try:
        first = await orchestrator.chat(None, message)
        session_id = first.session_id
        print({"phase": first.phase, "reply": first.reply})
        second = await orchestrator.chat(session_id, "确认，这两个出发地正确。")
        print({
            "phase": second.phase,
            "candidate_count": len(second.candidates),
            "candidates": [
                {
                    "name": candidate.name,
                    "score": candidate.score,
                    "routes": [route.summary for route in candidate.routes],
                }
                for candidate in second.candidates
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
