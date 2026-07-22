"""Real DeepSeek extraction check for a single sentence; prints no credentials."""

import asyncio

from app.config import get_settings
from app.models import MeetingRequirements
from app.services.deepseek import DeepSeekService


TEST_MESSAGE = (
    "我从北京西站坐公共交通，小王从顺义驾车，"
    "本周六中午十二点见面，想吃环境安静的北京菜。"
)


async def main() -> None:
    settings = get_settings()
    service = DeepSeekService(settings.deepseek_api_key, settings.deepseek_model, 60)
    try:
        result = await service.extract_requirements(MeetingRequirements(), TEST_MESSAGE)
        print({
            "participants": [
                {
                    "name": participant.name,
                    "origin": participant.origin_text,
                    "mode": participant.transport_mode,
                }
                for participant in (result.participants or [])
            ],
            "meeting_time": result.meeting_time,
            "activity": result.activity,
            "atmosphere": result.atmosphere,
        })
    finally:
        await service.close()


if __name__ == "__main__":
    asyncio.run(main())
