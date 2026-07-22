from abc import ABC, abstractmethod
from urllib.parse import urlparse

import httpx

from app.config import Settings
from app.models import SearchEvidence


class SearchProviderError(RuntimeError):
    pass


class SearchProvider(ABC):
    """One small contract shared by every public web-search backend."""

    name: str

    @abstractmethod
    async def search(self, query: str, limit: int = 5) -> list[SearchEvidence]:
        raise NotImplementedError

    async def close(self) -> None:
        return None


class _HttpSearchProvider(SearchProvider):
    def __init__(self, timeout_seconds: float, client: httpx.AsyncClient | None = None) -> None:
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(timeout=timeout_seconds)

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()


class TavilySearchProvider(_HttpSearchProvider):
    name = "tavily"
    endpoint = "https://api.tavily.com/search"

    def __init__(self, api_key: str, timeout_seconds: float = 20, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(timeout_seconds, client)
        self.api_key = api_key

    async def search(self, query: str, limit: int = 5) -> list[SearchEvidence]:
        response = await self.client.post(
            self.endpoint,
            json={
                "api_key": self.api_key,
                "query": query,
                "search_depth": "basic",
                "max_results": max(1, min(limit, 10)),
                "include_answer": False,
                "include_raw_content": False,
            },
        )
        response.raise_for_status()
        return _unique_evidence(
            SearchEvidence(
                title=item.get("title") or "未命名结果",
                url=item.get("url") or "",
                snippet=item.get("content") or "",
                source=self.name,
            )
            for item in response.json().get("results", [])
        )[:limit]


class ZhipuSearchProvider(_HttpSearchProvider):
    name = "zhipu"
    endpoint = "https://open.bigmodel.cn/api/paas/v4/web_search"

    def __init__(self, api_key: str, search_engine: str = "search_std", timeout_seconds: float = 20, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(timeout_seconds, client)
        self.api_key = api_key
        self.search_engine = search_engine

    async def search(self, query: str, limit: int = 5) -> list[SearchEvidence]:
        response = await self.client.post(
            self.endpoint,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "search_query": query,
                "search_engine": self.search_engine,
                "search_intent": False,
                "count": max(1, min(limit, 10)),
                "search_recency_filter": "noLimit",
                "content_size": "medium",
            },
        )
        response.raise_for_status()
        return _unique_evidence(
            SearchEvidence(
                title=item.get("title") or "未命名结果",
                url=item.get("link") or "",
                snippet=item.get("content") or "",
                source=self.name,
                published_at=item.get("publish_date") or None,
            )
            for item in response.json().get("search_result", [])
        )[:limit]


class BochaSearchProvider(_HttpSearchProvider):
    name = "bocha"
    endpoint = "https://api.bochaai.com/v1/web-search"

    def __init__(self, api_key: str, timeout_seconds: float = 20, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(timeout_seconds, client)
        self.api_key = api_key

    async def search(self, query: str, limit: int = 5) -> list[SearchEvidence]:
        response = await self.client.post(
            self.endpoint,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "query": query,
                "summary": True,
                "freshness": "noLimit",
                "count": max(1, min(limit, 10)),
            },
        )
        response.raise_for_status()
        values = response.json().get("data", {}).get("webPages", {}).get("value", [])
        return _unique_evidence(
            SearchEvidence(
                title=item.get("name") or "未命名结果",
                url=item.get("url") or "",
                snippet=item.get("summary") or item.get("snippet") or "",
                source=self.name,
                published_at=item.get("datePublished") or None,
            )
            for item in values
        )[:limit]


class FallbackSearchProvider(SearchProvider):
    """Use the first healthy provider; fall through without breaking recommendations."""

    def __init__(self, providers: list[SearchProvider]) -> None:
        self.providers = providers
        self.name = "auto:" + "+".join(provider.name for provider in providers)

    async def search(self, query: str, limit: int = 5) -> list[SearchEvidence]:
        errors: list[str] = []
        for provider in self.providers:
            try:
                results = await provider.search(query, limit)
                if results:
                    return results
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                errors.append(f"{provider.name}: {type(exc).__name__}")
        if errors:
            raise SearchProviderError("；".join(errors))
        return []

    async def close(self) -> None:
        for provider in self.providers:
            await provider.close()


def build_available_search_providers(settings: Settings) -> dict[str, SearchProvider]:
    available: dict[str, SearchProvider] = {}
    if settings.zhipu_api_key:
        available["zhipu"] = ZhipuSearchProvider(
            settings.zhipu_api_key,
            settings.zhipu_search_engine,
            settings.request_timeout_seconds,
        )
    if settings.bocha_api_key:
        available["bocha"] = BochaSearchProvider(
            settings.bocha_api_key,
            settings.request_timeout_seconds,
        )
    if settings.tavily_api_key:
        available["tavily"] = TavilySearchProvider(
            settings.tavily_api_key,
            settings.request_timeout_seconds,
        )
    return available


def build_search_provider(settings: Settings) -> SearchProvider | None:
    available = build_available_search_providers(settings)
    selected = settings.search_provider.strip().lower()
    if selected != "auto":
        return available.get(selected)
    ordered = [available[name] for name in ("zhipu", "bocha", "tavily") if name in available]
    return FallbackSearchProvider(ordered) if ordered else None


def _unique_evidence(items) -> list[SearchEvidence]:
    result: list[SearchEvidence] = []
    seen: set[str] = set()
    for item in items:
        parsed = urlparse(item.url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            continue
        normalized = item.url.rstrip("/")
        if normalized in seen:
            continue
        seen.add(normalized)
        item.url = normalized
        item.snippet = " ".join(item.snippet.split())[:900]
        result.append(item)
    return result
