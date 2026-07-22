from app.models import ActivityCategory, MeetingRequirements, PoiPlace


TAKEAWAY_MARKERS = ("外卖", "外送", "取餐", "档口", "便利店", "美宜佳", "加油站", "服务区")
QUICK_COFFEE_MARKERS = ("瑞幸", "库迪", "挪瓦", "幸运咖", "麦咖啡")
EXPERIENCE_MARKERS = ("酒店", "艺术", "书店", "画廊", "空间", "庭院", "独立", "公馆", "美术馆")


# This is a stable product ontology, not a growing list of user phrases.
CATEGORY_DEFAULTS: dict[ActivityCategory, tuple[list[str], list[str]]] = {
    ActivityCategory.CAFE: (["咖啡馆"], ["venue"]),
    ActivityCategory.DINING: (["餐厅"], ["venue"]),
    ActivityCategory.DRINKS: (["酒吧"], ["venue"]),
    ActivityCategory.EXHIBITION: (["美术馆", "博物馆", "艺术中心"], ["attraction"]),
    ActivityCategory.STREET_WALK: (["历史文化街区", "特色街区", "步行街"], ["district", "attraction"]),
    ActivityCategory.PARK_WALK: (["公园", "绿道", "滨水空间"], ["park", "district"]),
    ActivityCategory.SHOPPING: (["商业街", "商圈", "购物中心"], ["district", "venue"]),
    ActivityCategory.SIGHTSEEING: (["景区", "旅游景点", "城市地标"], ["attraction", "park", "district"]),
    ActivityCategory.CONVERSATION: (["咖啡馆", "茶馆", "休闲空间"], ["venue"]),
    ActivityCategory.OTHER: (["休闲场所"], ["venue", "park", "district", "attraction"]),
}


def apply_category_defaults(requirements: MeetingRequirements) -> MeetingRequirements:
    """Fill missing execution fields from the LLM-selected semantic category."""

    if requirements.activity_category is None:
        return requirements
    terms, kinds = CATEGORY_DEFAULTS[requirements.activity_category]
    if not requirements.search_keywords:
        requirements.search_keywords = list(terms)
    if not requirements.target_place_kinds:
        requirements.target_place_kinds = list(kinds)
    return requirements


def search_terms_for_requirements(requirements: MeetingRequirements) -> list[str]:
    """Use the LLM search plan, with category defaults only as a bounded fallback."""

    apply_category_defaults(requirements)
    if requirements.search_keywords:
        terms = list(dict.fromkeys(term for term in requirements.search_keywords if term))
        if _effective_category(requirements) == ActivityCategory.CAFE:
            coffee_terms = [term for term in terms if "咖啡" in term]
            return (coffee_terms or CATEGORY_DEFAULTS[ActivityCategory.CAFE][0])[:8]
        return terms[:8]
    return [requirements.activity] if requirements.activity else ["休闲场所"]


def infer_place_kind(name: str, type_name: str | None) -> str:
    poi_type = type_name or ""
    text = f"{name} {poi_type}"
    if any(marker in poi_type for marker in ("公园", "绿地", "植物园", "森林")):
        return "park"
    if any(marker in poi_type for marker in ("特色商业街", "步行街", "商圈", "广场")):
        return "district"
    if any(marker in poi_type for marker in ("景区", "风景名胜", "美术馆", "博物馆", "艺术馆", "艺术中心", "展览馆")):
        return "attraction"
    if any(marker in poi_type for marker in ("餐饮服务", "购物服务", "生活服务", "住宿服务", "公司企业")):
        return "venue"
    if any(marker in text for marker in ("公园", "绿地", "绿道", "植物园", "森林")):
        return "park"
    if any(marker in text for marker in ("街区", "步行街", "商业街", "商圈", "广场", "创意园区", "历史文化街", "风貌区")):
        return "district"
    if any(marker in text for marker in ("景区", "风景名胜", "美术馆", "博物馆", "艺术馆", "艺术中心", "展览馆")):
        return "attraction"
    return "venue"


def venue_fit_score(place: PoiPlace, requirements: MeetingRequirements) -> float:
    """Score map facts against the semantic plan; hard facts remain deterministic."""

    apply_category_defaults(requirements)
    if requirements.target_place_kinds and place.place_kind not in requirements.target_place_kinds:
        return 0.0

    text = f"{place.name} {place.type_name or ''} {place.address}"
    keywords = requirements.search_keywords or ([requirements.activity] if requirements.activity else [])
    category = _effective_category(requirements)
    if category == ActivityCategory.CAFE and "咖啡" not in text:
        return 0.0
    keyword_matches = sum(1 for keyword in keywords if keyword and keyword in text)
    score = 0.55 + (0.25 * keyword_matches / len(keywords) if keywords else 0.1)

    experience_sensitive = bool(requirements.atmosphere) and category in {
        ActivityCategory.CAFE,
        ActivityCategory.DINING,
        ActivityCategory.DRINKS,
        ActivityCategory.CONVERSATION,
    }
    if experience_sensitive:
        if any(marker in text for marker in TAKEAWAY_MARKERS):
            return 0.05
        if any(marker in text for marker in QUICK_COFFEE_MARKERS):
            score -= 0.22
        if any(marker in text for marker in EXPERIENCE_MARKERS):
            score += 0.2

    profile = place.atmosphere_profile
    if profile and profile.confidence > 0:
        # The LLM-generated evidence profile carries open-ended atmosphere semantics.
        desired = [1 - profile.quick_service] if experience_sensitive else []
        activity_text = " ".join(filter(None, [requirements.activity, *requirements.atmosphere]))
        if "安静" in activity_text or "私密" in activity_text:
            desired.append(profile.quiet)
        if any(word in activity_text for word in ("设计", "特别", "艺术", "氛围")):
            desired.append(profile.design)
        if "聊天" in activity_text or "久坐" in activity_text:
            desired.append(profile.conversation_friendly)
        if "约会" in activity_text or "浪漫" in activity_text:
            desired.append(profile.date_friendly)
        if desired:
            evidence_score = sum(desired) / len(desired)
            evidence_weight = min(0.55, profile.confidence * 0.55)
            score = score * (1 - evidence_weight) + evidence_score * evidence_weight
    return max(0.0, min(1.0, score))


def _effective_category(requirements: MeetingRequirements) -> ActivityCategory | None:
    """Migrate pre-refactor sessions without reinterpreting arbitrary user language."""

    if requirements.activity_category is not None:
        return requirements.activity_category
    if any("咖啡" in keyword for keyword in requirements.search_keywords):
        return ActivityCategory.CAFE
    return None
