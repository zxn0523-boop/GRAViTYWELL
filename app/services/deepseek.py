import asyncio
import json
import re
from datetime import date, datetime, timedelta, timezone

import httpx
from pydantic import ValidationError

from app.models import (
    CandidatePlace,
    ConversationMessage,
    ExtractionResult,
    MeetingRequirements,
    RecommendationNarrative,
    PoiPlace,
    SearchEvidence,
    VenueProfileAssessment,
    VenueProfileBatch,
)


class DeepSeekError(RuntimeError):
    pass


EXTRACTION_SYSTEM_PROMPT = """
你是多人约会规划产品的需求接待员，只理解和整理信息，不推荐地点、不计算距离。只输出有效 JSON，不使用 Markdown。
规则：
1. participants 若出现，必须是合并新消息后的完整 2～4 人列表；name 和 origin_text 必须为字符串，不能为 null。“我从/我在”对应 name“我”。
2. transport_mode 只允许 transit、driving 或 null。
3. 相对日期转为 +08:00 ISO 8601。模糊时段默认：凌晨01:00、早上09:00、上午10:00、中午12:00、下午14:00、傍晚18:00、晚上19:00、夜间20:00，不追问几点。
4. search_keywords 给 1～3 个高德可搜索的具体品类词，不写形容词。明确场所类型不可被氛围替换，例如咖啡馆不能改成艺术馆。
5. “照顾某人/离某人近些”：priority=favor_person，并填写 favored_participant。
6. “在某地附近/靠近某商圈或车站/明确去某座城市见面”：preferred_area_text 只填目标地名，priority=location_first；这不同于照顾参与者。
7. intent 只允许 update、confirm_origins、accept、restart。未知信息填 null，不猜地址、时间、交通方式。
8. “第一个/第二个”必须按候选序号理解。question 仅在缺关键信息时填写，且只问一个问题。
9. mode=intercity 表示邻城模式，但你仍只抽取需求；不要自行选择见面城市或中间城市。
返回字段：intent, participants, meeting_time, meeting_time_text, activity, atmosphere, constraints, search_keywords, priority, favored_participant, preferred_area_text, question。
""".strip()


class DeepSeekService:
    ENDPOINT = "https://api.deepseek.com/chat/completions"

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float = 30.0,
        client: httpx.AsyncClient | None = None,
        extraction_attempts: int = 3,
        retry_delay_seconds: float = 0.35,
        profile_model: str | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self.extraction_attempts = max(1, extraction_attempts)
        self.retry_delay_seconds = max(0.0, retry_delay_seconds)
        self.profile_model = profile_model or model

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def extract_requirements(
        self,
        current: MeetingRequirements,
        user_message: str,
        history: list[ConversationMessage] | None = None,
        candidates: list[CandidatePlace] | None = None,
    ) -> ExtractionResult:
        china_time = timezone(timedelta(hours=8), name="Asia/Shanghai")
        now = datetime.now(china_time)
        compact_history = [
            {"role": item.role, "content": item.content}
            for item in (history or [])
            if not (item.role == "user" and item.content == user_message)
        ][-6:]
        compact_current = {
            "mode": current.mode,
            "participants": [
                {
                    "name": item.name,
                    "origin_text": item.origin_text,
                    "transport_mode": item.transport_mode,
                }
                for item in current.participants
            ],
            "meeting_time": current.meeting_time.isoformat() if current.meeting_time else None,
            "meeting_time_text": current.meeting_time_text,
            "activity": current.activity,
            "atmosphere": current.atmosphere,
            "constraints": current.constraints,
            "search_keywords": current.search_keywords,
            "priority": current.priority,
            "favored_participant": current.favored_participant,
            "preferred_area_text": current.preferred_area_text,
        }
        compact_candidates = [
            {
                "index": index,
                "poi_id": item.poi_id,
                "name": item.name,
                "meeting_city": item.meeting_city,
                "gateway_name": item.gateway_name,
                "routes": [
                    {
                        "participant": route.participant_name,
                        "minutes": route.duration_minutes,
                        "transfers": route.transfers,
                    }
                    for route in item.routes
                ],
            }
            for index, item in enumerate(candidates or [], start=1)
        ]
        prompt = f"""
当前中国时间：{now.isoformat()}
当前需求：{json.dumps(compact_current, ensure_ascii=False, default=str)}
最近对话：{json.dumps(compact_history, ensure_ascii=False)}
候选摘要：{json.dumps(compact_candidates, ensure_ascii=False)}
用户新消息：{user_message}
""".strip()

        last_error: Exception | None = None
        for attempt in range(self.extraction_attempts):
            try:
                system_message = EXTRACTION_SYSTEM_PROMPT
                if attempt:
                    system_message += "\n上一次响应为空或无法校验；这次必须返回一个非空的完整 JSON 对象。"
                response = await self.client.post(
                    self.ENDPOINT,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "model": self.model,
                        "messages": [
                            {
                                "role": "system",
                                "content": system_message,
                            },
                            {"role": "user", "content": prompt},
                        ],
                        "response_format": {"type": "json_object"},
                        "thinking": {"type": "disabled"},
                        "temperature": 0.1,
                        "max_tokens": 1400,
                    },
                )
                response.raise_for_status()
                payload = response.json()
                content = payload["choices"][0]["message"]["content"]
                if not content or not content.strip():
                    raise ValueError("DeepSeek 返回了空内容")
                raw_result = json.loads(content)
                normalized = _normalize_extraction_payload(
                    raw_result,
                    current,
                    user_message,
                    now,
                )
                extracted = ExtractionResult.model_validate(normalized)
                if _semantic_extraction_is_suspicious(extracted, current, user_message):
                    raise ValueError("参与者姓名与用户原话不一致")
                return extracted
            except (httpx.HTTPError, KeyError, IndexError, ValueError, ValidationError) as exc:
                last_error = exc
                if attempt < self.extraction_attempts - 1 and self.retry_delay_seconds:
                    await asyncio.sleep(self.retry_delay_seconds * (2**attempt))

        raise DeepSeekError(
            f"需求理解暂时失败（已自动尝试 {self.extraction_attempts} 次）：{last_error}。"
            "当前会话没有被修改，无需重新开始，请直接再发送一次。"
        )

    async def build_venue_profiles(
        self,
        requirements: MeetingRequirements,
        venues: list[tuple[PoiPlace, list[SearchEvidence]]],
    ) -> list[VenueProfileAssessment]:
        """Turn public snippets into structured signals in one economical LLM call."""

        compact = [
            {
                "poi_id": place.poi_id,
                "name": place.name,
                "city": place.city,
                "type": place.type_name,
                "evidence": [
                    {"title": item.title, "snippet": item.snippet[:600], "url": item.url}
                    for item in evidence[:4]
                ],
            }
            for place, evidence in venues
        ]
        prompt = f"""
用户需要：{requirements.model_dump_json(exclude_none=True)}
候选场所及公开搜索摘要：{json.dumps(compact, ensure_ascii=False)}

请逐个判断场所氛围，只能依据给出的名称、类型和摘要，不能凭空补充事实。
quiet、design、conversation_friendly、date_friendly、quick_service、confidence 均为 0～1。
证据矛盾或不足时 confidence 应低，其他分数靠近 0.5；summary 用一句中文说明证据结论或不确定性。
返回 JSON：{{"profiles":[{{"poi_id":"原样复制","quiet":0.5,"design":0.5,"conversation_friendly":0.5,"date_friendly":0.5,"quick_service":0.5,"confidence":0.3,"summary":"..."}}]}}
""".strip()
        try:
            response = await self.client.post(
                self.ENDPOINT,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.profile_model,
                    "messages": [
                        {"role": "system", "content": "你是场所公开信息分析器，只输出有效 JSON。"},
                        {"role": "user", "content": prompt},
                    ],
                    "response_format": {"type": "json_object"},
                    "thinking": {"type": "disabled"},
                    "temperature": 0.1,
                    "max_tokens": 1300,
                },
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            return VenueProfileBatch.model_validate_json(content).profiles
        except (httpx.HTTPError, KeyError, IndexError, ValueError, ValidationError):
            return []

    async def explain_recommendations(
        self,
        requirements: MeetingRequirements,
        candidates: list[CandidatePlace],
    ) -> RecommendationNarrative:
        compact_candidates = [
            {
                "poi_id": candidate.poi_id,
                "name": candidate.name,
                "score": candidate.score,
                "score_breakdown": candidate.score_breakdown,
                "place_kind": candidate.place_kind,
                "map_rating": candidate.map_rating,
                "opening_hours": candidate.opening_hours,
                "opening_verified": candidate.opening_verified,
                "routes": [route.model_dump() for route in candidate.routes],
                "weather": candidate.weather.model_dump() if candidate.weather else None,
                "warnings": candidate.warnings,
                "meeting_city": candidate.meeting_city,
                "gateway_name": candidate.gateway_name,
                "intercity_note": candidate.intercity_note,
                "atmosphere_profile": (
                    {
                        "summary": candidate.atmosphere_profile.summary,
                        "confidence": candidate.atmosphere_profile.confidence,
                        "provider": candidate.atmosphere_profile.provider,
                    }
                    if candidate.atmosphere_profile else None
                ),
            }
            for candidate in candidates
        ]
        prompt = f"""
你是多人约会规划产品的结果说明员。下面所有数字都来自地图和确定性评分程序。
你只能解释这些数据，不能添加不存在的营业时间、票价、评分、距离或路线。

需求：{requirements.model_dump_json(exclude_none=True)}
候选：{json.dumps(compact_candidates, ensure_ascii=False)}

返回 JSON：
{{
  "intro": "一句话说明本次排序重点",
  "explanations": [{{"poi_id": "原样复制", "reason": "两句以内说明为何适合及主要取舍"}}]
}}
每个候选必须恰好对应一条 explanation。
""".strip()
        try:
            response = await self.client.post(
                self.ENDPOINT,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": "只输出有效 JSON，不使用 Markdown。"},
                        {"role": "user", "content": prompt},
                    ],
                    "response_format": {"type": "json_object"},
                    "thinking": {"type": "disabled"},
                    "temperature": 0.2,
                    "max_tokens": 1000,
                },
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            return RecommendationNarrative.model_validate_json(content)
        except (httpx.HTTPError, KeyError, IndexError, ValueError, ValidationError):
            if requirements.mode.value == "intercity":
                return RecommendationNarrative(
                    intro="已分别比较双方所在城市，并按门到门耗时、换乘、场所匹配和天气完成排序。",
                    explanations=[],
                )
            return RecommendationNarrative(
                intro="已按需求匹配、交通公平、整体便利、换乘和天气完成排序。",
                explanations=[],
            )


def _normalize_extraction_payload(
    payload: object,
    current: MeetingRequirements,
    user_message: str,
    now: datetime | None = None,
) -> dict:
    """Repair small, recoverable JSON defects before strict Pydantic validation."""

    if not isinstance(payload, dict):
        raise ValueError("DeepSeek 返回的最外层不是 JSON 对象")
    result = dict(payload)
    participants = result.get("participants")
    if isinstance(participants, list):
        global_transport_mode = _global_transport_mode(user_message)
        normalized_participants = []
        for index, raw_participant in enumerate(participants):
            item = dict(raw_participant) if isinstance(raw_participant, dict) else {}
            current_match = _match_current_participant(item, index, current)

            name = item.get("name")
            if not isinstance(name, str) or not name.strip():
                if current_match:
                    name = current_match.name
                elif index == 0 and re.search(r"(?:^|[，。；;\s])我(?:从|在|住|由)", user_message):
                    name = "我"
                else:
                    name = f"参与者{index + 1}"
            item["name"] = str(name).strip()

            origin = item.get("origin_text")
            if not isinstance(origin, str):
                item["origin_text"] = current_match.origin_text if current_match else ""

            mode = item.get("transport_mode")
            mode_aliases = {
                "公共交通": "transit",
                "公交": "transit",
                "地铁": "transit",
                "public_transport": "transit",
                "驾车": "driving",
                "开车": "driving",
                "car": "driving",
            }
            if mode in mode_aliases:
                item["transport_mode"] = mode_aliases[mode]
            elif mode is None and current_match and current_match.transport_mode is not None:
                item["transport_mode"] = current_match.transport_mode.value
            elif mode is None and global_transport_mode is not None:
                item["transport_mode"] = global_transport_mode
            elif mode not in (None, "transit", "driving"):
                item["transport_mode"] = None
            normalized_participants.append(item)
        result["participants"] = normalized_participants

    for field in ("atmosphere", "constraints", "search_keywords"):
        value = result.get(field)
        if isinstance(value, str):
            result[field] = [value]
    if result.get("meeting_time") == "":
        result["meeting_time"] = None
    _apply_fuzzy_time_default(result, current, user_message, now or datetime.now().astimezone())
    if result.get("preferred_area_text"):
        result["priority"] = "location_first"
    return result


def _global_transport_mode(user_message: str) -> str | None:
    all_people = r"(?:都|全都|全部|所有人|两个人都|三个人都|四个人都)"
    if re.search(all_people + r".{0,6}(?:坐|乘坐|乘|使用)?(?:公共交通|公交|地铁)", user_message):
        return "transit"
    if re.search(all_people + r".{0,6}(?:驾车|开车|自驾)", user_message):
        return "driving"
    return None


FUZZY_PERIOD_HOURS = (
    ("凌晨", 1),
    ("早上", 9),
    ("上午", 10),
    ("中午", 12),
    ("下午", 14),
    ("傍晚", 18),
    ("晚上", 19),
    ("夜间", 20),
)
WEEKDAY_INDEX = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}


def _apply_fuzzy_time_default(
    result: dict,
    current: MeetingRequirements,
    user_message: str,
    now: datetime,
) -> None:
    """Turn a date plus a vague Chinese period into a stable routing time."""

    if re.search(r"[零〇一二两三四五六七八九十百\d]{1,3}\s*(?:点|时|:|：)", user_message):
        return
    period_hour = next((hour for marker, hour in FUZZY_PERIOD_HOURS if marker in user_message), None)
    if period_hour is None and "明晚" in user_message:
        period_hour = 19
    if period_hour is None:
        return

    model_time = result.get("meeting_time")
    if model_time:
        try:
            parsed = datetime.fromisoformat(str(model_time).replace("Z", "+00:00"))
            result["meeting_time"] = parsed.replace(hour=period_hour, minute=0, second=0, microsecond=0).isoformat()
            return
        except ValueError:
            pass

    target_date = _relative_date_from_text(user_message, now.date())
    if target_date is None and current.meeting_time is not None:
        target_date = current.meeting_time.date()
    if target_date is not None:
        result["meeting_time"] = datetime.combine(
            target_date,
            datetime.min.time().replace(hour=period_hour),
            tzinfo=now.tzinfo,
        ).isoformat()


def _relative_date_from_text(text: str, today: date) -> date | None:
    if "后天" in text:
        return today + timedelta(days=2)
    if "明天" in text or "明日" in text or "明晚" in text:
        return today + timedelta(days=1)
    if "今天" in text or "今日" in text or "今晚" in text:
        return today
    weekday_match = re.search(r"(下周|本周|这周|周)([一二三四五六日天])", text)
    if not weekday_match:
        return None
    prefix, weekday_text = weekday_match.groups()
    target_weekday = WEEKDAY_INDEX[weekday_text]
    if prefix == "下周":
        delta = 7 - today.weekday() + target_weekday
    elif prefix in ("本周", "这周"):
        delta = target_weekday - today.weekday()
        if delta < 0:
            delta += 7
    else:
        delta = (target_weekday - today.weekday()) % 7
    return today + timedelta(days=delta)


def _match_current_participant(item: dict, index: int, current: MeetingRequirements):
    origin = item.get("origin_text")
    if isinstance(origin, str) and origin:
        matched = next(
            (participant for participant in current.participants if participant.origin_text == origin),
            None,
        )
        if matched:
            return matched
    if index < len(current.participants):
        return current.participants[index]
    return None


def _semantic_extraction_is_suspicious(
    extracted: ExtractionResult,
    current: MeetingRequirements,
    user_message: str,
) -> bool:
    participants = extracted.participants or []
    known_names = {participant.name for participant in current.participants}
    for participant in participants:
        if participant.name == "我" or participant.name in known_names:
            continue
        if participant.name not in user_message:
            return True
    return False
