import json

import httpx
import pytest

from app.services.search import (
    BochaSearchProvider,
    FallbackSearchProvider,
    SearchProvider,
    TavilySearchProvider,
    ZhipuSearchProvider,
)


@pytest.mark.parametrize(
    ("provider_factory", "response_body", "expected_url"),
    [
        (
            lambda client: TavilySearchProvider("secret", client=client),
            {"results": [{"title": "T", "url": "https://a.test/item", "content": "安静"}]},
            "https://a.test/item",
        ),
        (
            lambda client: ZhipuSearchProvider("secret", client=client),
            {"search_result": [{"title": "Z", "link": "https://z.test/item", "content": "设计", "publish_date": "2026-01-01"}]},
            "https://z.test/item",
        ),
        (
            lambda client: BochaSearchProvider("secret", client=client),
            {"data": {"webPages": {"value": [{"name": "B", "url": "https://b.test/item", "summary": "适合聊天"}]}}},
            "https://b.test/item",
        ),
    ],
)
async def test_providers_normalize_different_responses(provider_factory, response_body, expected_url):
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert "secret" in request.content.decode() or request.headers.get("Authorization") == "Bearer secret"
        return httpx.Response(200, json=response_body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = provider_factory(client)
    results = await provider.search("场所 氛围", 3)
    assert results[0].url == expected_url
    assert results[0].source == provider.name
    await client.aclose()


class _StubProvider(SearchProvider):
    def __init__(self, name: str, fails: bool) -> None:
        self.name = name
        self.fails = fails

    async def search(self, query: str, limit: int = 5):
        if self.fails:
            raise httpx.ConnectError("offline")
        from app.models import SearchEvidence
        return [SearchEvidence(title="ok", url="https://ok.test", source=self.name)]


async def test_auto_provider_falls_through_to_next_healthy_backend():
    provider = FallbackSearchProvider([_StubProvider("first", True), _StubProvider("second", False)])
    results = await provider.search("test")
    assert results[0].source == "second"
