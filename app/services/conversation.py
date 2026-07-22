from app.models import ExtractionResult, MeetingMode, MeetingRequirements


def merge_requirements(
    current: MeetingRequirements,
    extracted: ExtractionResult,
    user_message: str = "",
) -> MeetingRequirements:
    """Apply only fields that the user actually changed or supplied."""

    updated = current.model_copy(deep=True)
    if extracted.participants is not None:
        previous = {
            (participant.name, participant.origin_text): participant
            for participant in current.participants
        }
        merged_participants = []
        for participant in extracted.participants:
            known = previous.get((participant.name, participant.origin_text))
            if known:
                for field in (
                    "transport_mode",
                    "longitude",
                    "latitude",
                    "formatted_address",
                    "city",
                    "city_code",
                    "adcode",
                ):
                    if getattr(participant, field) is None:
                        setattr(participant, field, getattr(known, field))
            merged_participants.append(participant)
        updated.participants = merged_participants
    category_locked = _coffee_category_is_locked(current, user_message)
    for field in (
        "meeting_time",
        "meeting_time_text",
        "atmosphere",
        "constraints",
        "priority",
        "favored_participant",
        "preferred_area_text",
    ):
        value = getattr(extracted, field)
        if value is not None:
            setattr(updated, field, value)

    if extracted.activity is not None and not (
        category_locked and "咖啡" not in extracted.activity
    ):
        updated.activity = extracted.activity

    if extracted.search_keywords is not None:
        if category_locked:
            coffee_keywords = [
                keyword for keyword in extracted.search_keywords if "咖啡" in keyword
            ]
            if coffee_keywords:
                updated.search_keywords = coffee_keywords
        else:
            updated.search_keywords = extracted.search_keywords
    return updated


def _coffee_category_is_locked(
    current: MeetingRequirements,
    user_message: str,
) -> bool:
    current_is_coffee = any("咖啡" in keyword for keyword in current.search_keywords)
    if not current_is_coffee:
        return False
    explicit_category_change = any(
        marker in user_message
        for marker in (
            "不喝咖啡",
            "不想喝咖啡",
            "改成餐厅",
            "换成餐厅",
            "改成吃饭",
            "换成吃饭",
            "想吃饭",
            "去吃饭",
            "改成散步",
            "换成散步",
            "改成看展",
            "换成看展",
        )
    )
    return not explicit_category_change


def next_missing_question(requirements: MeetingRequirements) -> str | None:
    participant_count = len(requirements.participants)
    if participant_count < 2:
        return "这次一共有谁参加？请告诉我至少两个人分别从哪里出发。"
    if participant_count > 4:
        return "第一版一次支持 2～4 人，请先选出这次需要一起计算的四个人。"
    if requirements.mode == MeetingMode.INTERCITY and participant_count != 2:
        return "邻城模式第一版先支持两个人，请告诉我两个人分别从哪里出发。"
    if any(not participant.origin_text.strip() for participant in requirements.participants):
        return "还有人的出发地不明确，请补充一个附近地标、车站或商圈。"
    if requirements.meeting_time is None:
        return "你们计划哪一天、几点左右见面？"
    if not requirements.activity:
        return "这次主要想做什么，例如吃饭、散步、看展或找地方聊天？"
    missing_modes = [
        participant.name
        for participant in requirements.participants
        if participant.transport_mode is None
    ]
    if missing_modes:
        names = "、".join(missing_modes)
        return f"{names}分别准备乘坐公共交通还是驾车？"
    if requirements.priority == "favor_person":
        participant_names = {participant.name for participant in requirements.participants}
        if requirements.favored_participant not in participant_names:
            return "你希望这次优先照顾哪位参与者？"
    if requirements.priority == "location_first" and not requirements.preferred_area_text:
        return "你希望优先在哪个区域附近见面？请给我一个地标、车站或商圈。"
    return None
