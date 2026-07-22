from app.models import MeetingRequirements, PoiPlace


EXPERIENCE_WORDS = ("设计感", "安静", "聊天", "约会", "氛围", "私密", "久坐")
TAKEAWAY_MARKERS = ("外卖", "外送", "取餐", "档口", "便利店", "美宜佳", "加油站", "服务区")
QUICK_COFFEE_MARKERS = ("瑞幸", "库迪", "挪瓦", "幸运咖", "麦咖啡")
EXPERIENCE_MARKERS = ("酒店", "艺术", "书店", "画廊", "空间", "庭院", "独立", "公馆", "美术馆")


def search_terms_for_requirements(requirements: MeetingRequirements) -> list[str]:
    """Expand natural preferences into varied map object categories."""

    terms = list(requirements.search_keywords)
    all_text = " ".join(
        filter(None, [requirements.activity, *requirements.atmosphere, *requirements.constraints])
    )
    activity_text = " ".join(
        filter(None, [requirements.activity, *requirements.constraints])
    )

    # An explicit venue category is a hard semantic boundary. Atmosphere refines it.
    activity_changes_category = any(
        word in activity_text
        for word in ("散步", "户外", "逛街", "购物", "看展", "展览", "夜游", "城市漫游", "亲子")
    )
    if any("咖啡" in term for term in terms) and not activity_changes_category:
        coffee_terms = [term for term in terms if "咖啡" in term]
        if any(word in all_text for word in EXPERIENCE_WORDS):
            coffee_terms.extend(["独立咖啡馆", "精品咖啡馆"])
        return list(dict.fromkeys(coffee_terms))[:6]

    category_rules = (
        (("散步", "户外", "自然", "绿地", "遛弯"), ("公园", "绿道", "滨水空间")),
        (("街区", "城市漫游", "建筑", "拍照"), ("特色街区", "步行街", "创意园区")),
        (("夜景", "夜游"), ("滨水公园", "步行街", "观景台")),
        (("看展", "展览", "美术馆", "博物馆"), ("美术馆", "博物馆", "艺术中心")),
        (("逛街", "购物", "热闹"), ("商业街", "商圈", "购物中心")),
        (("亲子", "带孩子"), ("公园", "博物馆", "亲子乐园")),
    )
    for triggers, additions in category_rules:
        source_text = all_text if triggers[0] in ("散步", "夜景") else activity_text
        if any(trigger in source_text for trigger in triggers):
            terms.extend(additions)
    if not terms and requirements.activity:
        terms.append(requirements.activity)
    return list(dict.fromkeys(term for term in terms if term))[:8]


def infer_place_kind(name: str, type_name: str | None) -> str:
    text = f"{name} {type_name or ''}"
    if any(marker in text for marker in ("公园", "绿地", "绿道", "植物园", "森林")):
        return "park"
    if any(marker in text for marker in ("街区", "步行街", "商业街", "商圈", "广场", "创意园区")):
        return "district"
    if any(marker in text for marker in ("景区", "风景名胜", "美术馆", "博物馆", "艺术馆", "艺术中心", "展览馆")):
        return "attraction"
    return "venue"


def venue_fit_score(place: PoiPlace, requirements: MeetingRequirements) -> float:
    """Estimate whether the POI form fits the requested experience before costly routing."""

    text = f"{place.name} {place.type_name or ''} {place.address}"
    activity_text = " ".join(
        filter(None, [requirements.activity, *requirements.atmosphere])
    )
    keywords = requirements.search_keywords or ([requirements.activity] if requirements.activity else [])
    if any("咖啡" in keyword for keyword in keywords) and "咖啡" not in text:
        return 0.0
    keyword_matches = sum(1 for keyword in keywords if keyword and keyword in text)
    score = 0.55 + (0.25 * keyword_matches / len(keywords) if keywords else 0.1)

    experience_sensitive = any(word in activity_text for word in EXPERIENCE_WORDS)
    if experience_sensitive:
        if any(marker in text for marker in TAKEAWAY_MARKERS):
            return 0.05
        if any(marker in text for marker in QUICK_COFFEE_MARKERS):
            score -= 0.22
        if any(marker in text for marker in EXPERIENCE_MARKERS):
            score += 0.2
    if any(word in activity_text for word in ("散步", "户外", "公园", "自然")):
        if place.place_kind in ("park", "district"):
            score += 0.25
        elif place.place_kind == "venue":
            score -= 0.1
    if any(word in activity_text for word in ("热闹", "逛街", "夜游")):
        if place.place_kind == "district":
            score += 0.25
    return max(0.0, min(1.0, score))
