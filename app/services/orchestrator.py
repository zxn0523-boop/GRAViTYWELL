import logging
import re
from time import perf_counter

from app.models import (
    ChatResponse,
    ConversationMessage,
    ExtractionResult,
    MeetingMode,
    SessionPhase,
    SessionState,
)
from app.repositories.sessions import SessionRepository
from app.services.conversation import merge_requirements, next_missing_question
from app.services.deepseek import DeepSeekService
from app.services.intercity import IntercityRecommendationService
from app.services.recommender import RecommendationService


logger = logging.getLogger(__name__)


class ConversationOrchestrator:
    def __init__(
        self,
        sessions: SessionRepository,
        deepseek: DeepSeekService,
        recommender: RecommendationService,
        intercity_recommender: IntercityRecommendationService | None = None,
    ) -> None:
        self.sessions = sessions
        self.deepseek = deepseek
        self.recommender = recommender
        self.intercity_recommender = intercity_recommender

    async def chat(
        self,
        session_id: str | None,
        user_message: str,
        mode: MeetingMode = MeetingMode.AUTO,
    ) -> ChatResponse:
        request_started = perf_counter()
        timings_ms: dict[str, int] = {}
        state = self.sessions.get(session_id) if session_id else None
        if state is None:
            state = self.sessions.create()
            state.mode_auto = mode == MeetingMode.AUTO
            state.requested_mode = None if state.mode_auto else mode
            state.requirements.mode = MeetingMode.AUTO
        elif mode != MeetingMode.AUTO and state.requested_mode != mode:
            self.sessions.delete(state.session_id)
            state = self.sessions.create()
            state.mode_auto = False
            state.requested_mode = mode
            state.requirements.mode = MeetingMode.AUTO
        elif mode == MeetingMode.AUTO:
            state.mode_auto = True
            state.requested_mode = None

        previous_origins = {
            participant.name: participant.origin_text
            for participant in state.requirements.participants
        }
        previous_preferred_area = state.requirements.preferred_area_text
        state.history.append(ConversationMessage(role="user", content=user_message))
        understanding_started = perf_counter()
        extracted = self._fast_path_extraction(state, user_message)
        if extracted is not None:
            timings_ms["本地意图判断"] = self._elapsed_ms(understanding_started)
        else:
            extracted = await self.deepseek.extract_requirements(
                state.requirements,
                user_message,
                history=state.history,
                candidates=state.candidates,
            )
            timings_ms["需求理解"] = self._elapsed_ms(understanding_started)

        if extracted.intent == "restart":
            self.sessions.delete(state.session_id)
            timings_ms["总耗时"] = self._elapsed_ms(request_started)
            self._log_timings(state.session_id, timings_ms)
            return ChatResponse(
                session_id=None,
                phase="completed",
                mode=mode,
                reply="本次会话已清空。可以重新告诉我参与者、出发地、时间和想做的事。",
                timings_ms=timings_ms,
                cleared=True,
            )

        if extracted.intent == "accept":
            if not state.candidates:
                return self._save_reply(
                    state,
                    "目前还没有可以采纳的推荐。请先完成会面地点计算。",
                    timings_ms=timings_ms,
                    request_started=request_started,
                )
            self.sessions.delete(state.session_id)
            timings_ms["总耗时"] = self._elapsed_ms(request_started)
            self._log_timings(state.session_id, timings_ms)
            return ChatResponse(
                session_id=None,
                phase="completed",
                mode=mode,
                reply="已采纳本次推荐。本组对话、出发地和计算结果均已清空。",
                timings_ms=timings_ms,
                cleared=True,
            )

        state.requirements = merge_requirements(
            state.requirements,
            extracted,
            user_message,
        )
        current_origins = {
            participant.name: participant.origin_text
            for participant in state.requirements.participants
        }
        if (previous_origins and current_origins != previous_origins) or (
            state.requirements.preferred_area_text != previous_preferred_area
        ):
            state.origins_confirmed = False
            state.candidates = []
            state.phase = SessionPhase.COLLECTING
            state.requirements.mode = MeetingMode.AUTO

        if extracted.intent == "confirm_origins" and state.phase == SessionPhase.CONFIRMING_ORIGINS:
            state.origins_confirmed = True
            state.phase = SessionPhase.READY

        missing_question = next_missing_question(state.requirements)
        if missing_question:
            state.phase = SessionPhase.COLLECTING
            return self._save_reply(
                state,
                extracted.question or missing_question,
                timings_ms=timings_ms,
                request_started=request_started,
            )

        if not state.origins_confirmed:
            origins_started = perf_counter()
            state.requirements = await self.recommender.resolve_origins(state.requirements)
            state.requirements.mode = self._detect_mode(state)
            timings_ms["地址解析"] = self._elapsed_ms(origins_started)
            state.phase = SessionPhase.CONFIRMING_ORIGINS
            lines = [
                f"- {participant.name}：{participant.formatted_address}"
                for participant in state.requirements.participants
            ]
            if state.requirements.preferred_area_text:
                lines.append(
                    f"- 优先会面区域：{state.requirements.preferred_area_formatted_address}"
                )
            reply = "我把出发地解析成了：\n" + "\n".join(lines) + "\n\n这些地点正确吗？确认后我再开始路线计算。"
            mode_label = "邻城" if state.requirements.mode == MeetingMode.INTERCITY else "同城"
            if state.requested_mode and state.requested_mode != state.requirements.mode:
                requested_label = "邻城" if state.requested_mode == MeetingMode.INTERCITY else "同城"
                reply = (
                    f"你选择了{requested_label}模式，但根据真实地址应属于{mode_label}场景。"
                    "我不会为满足模式而改用异地同名地点；请确认地址，或补充更明确的城市。\n\n"
                    + reply
                )
            elif state.requested_mode:
                reply = f"你选择的{mode_label}模式与地址一致。\n\n" + reply
            elif state.mode_auto:
                reply = f"已根据出发城市自动切换为{mode_label}模式。\n\n" + reply
            else:
                reply = f"当前使用{mode_label}模式。\n\n" + reply
            if state.requirements.mode == MeetingMode.INTERCITY:
                cities = {participant.city for participant in state.requirements.participants if participant.city}
                if len(cities) < 2:
                    reply += "\n\n目前两个地点被识别在同一城市；如果确实如此，请切换到同城模式。"
            return self._save_reply(
                state,
                reply,
                timings_ms=timings_ms,
                request_started=request_started,
            )

        if state.requirements.mode == MeetingMode.INTERCITY:
            if self.intercity_recommender is None:
                raise RuntimeError("邻城推荐服务尚未配置")
            state.phase = SessionPhase.COMPARING_CITIES
            state.candidates = await self.intercity_recommender.recommend(
                state.requirements,
                timings_ms=timings_ms,
            )
        else:
            state.candidates = await self.recommender.recommend(
                state.requirements,
                timings_ms=timings_ms,
            )
        explanation_started = perf_counter()
        narrative = await self.deepseek.explain_recommendations(
            state.requirements,
            state.candidates,
        )
        timings_ms["结果说明"] = self._elapsed_ms(explanation_started)
        reasons = {item.poi_id: item.reason for item in narrative.explanations}
        for candidate in state.candidates:
            candidate.recommendation_reason = reasons.get(candidate.poi_id)
        state.phase = SessionPhase.RECOMMENDED
        follow_up = "你可以继续说“更安静”“少换乘”或“照顾某个人”；满意后说“采纳”。"
        if state.requirements.mode == MeetingMode.INTERCITY:
            follow_up = "你可以继续说“改去另一座城市”“少换乘”或“照顾某个人”；跨城班次请在出发前复核。满意后说“采纳”。"
        reply = narrative.intro + "\n" + follow_up
        return self._save_reply(
            state,
            reply,
            state.candidates,
            timings_ms=timings_ms,
            request_started=request_started,
        )

    def _save_reply(
        self,
        state: SessionState,
        reply: str,
        candidates=None,
        timings_ms: dict[str, int] | None = None,
        request_started: float | None = None,
    ) -> ChatResponse:
        timings_ms = timings_ms or {}
        state.history.append(ConversationMessage(role="assistant", content=reply))
        state.history = state.history[-20:]
        save_started = perf_counter()
        self.sessions.save(state)
        timings_ms["会话保存"] = self._elapsed_ms(save_started)
        if request_started is not None:
            timings_ms["总耗时"] = self._elapsed_ms(request_started)
        self._log_timings(state.session_id, timings_ms)
        return ChatResponse(
            session_id=state.session_id,
            phase=state.phase,
            mode=state.requirements.mode,
            reply=reply,
            candidates=candidates or [],
            timings_ms=timings_ms,
        )

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return round((perf_counter() - started) * 1000)

    @staticmethod
    def _fast_path_extraction(
        state: SessionState,
        user_message: str,
    ) -> ExtractionResult | None:
        """Handle unambiguous commands locally so a simple confirmation never waits for an LLM."""

        normalized = re.sub(r"[\s，,。.!！?？]", "", user_message).lower()
        if normalized in {"重新开始", "重来", "清空会话"}:
            return ExtractionResult(intent="restart")
        if normalized in {"采纳", "接受推荐", "就这样"}:
            return ExtractionResult(intent="accept")
        if state.phase == SessionPhase.CONFIRMING_ORIGINS and normalized in {
            "确认",
            "正确",
            "是",
            "是的",
            "对",
            "对的",
            "没错",
            "没问题",
            "地址正确",
            "地点正确",
            "可以",
        }:
            return ExtractionResult(intent="confirm_origins")
        return None

    @staticmethod
    def _log_timings(session_id: str, timings_ms: dict[str, int]) -> None:
        summary = ", ".join(f"{label}={value}ms" for label, value in timings_ms.items())
        logger.info("chat_timing session=%s %s", session_id, summary)

    @staticmethod
    def _detect_mode(state: SessionState) -> MeetingMode:
        origin_cities = {
            participant.city.removesuffix("市")
            for participant in state.requirements.participants
            if participant.city
        }
        target_city = (state.requirements.preferred_area_city or "").removesuffix("市")
        if target_city and any(city != target_city for city in origin_cities):
            return MeetingMode.INTERCITY
        return MeetingMode.INTERCITY if len(origin_cities) > 1 else MeetingMode.SAME_CITY
