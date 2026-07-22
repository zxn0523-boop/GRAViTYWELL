"""Compare configured search providers without printing API keys."""

import argparse
import asyncio
import sys
from time import perf_counter

from app.config import get_settings
from app.services.search import build_available_search_providers


async def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="比较 GravityWell 已配置的搜索引擎")
    parser.add_argument("query", help="公开场所查询，例如：上海 上生新所 咖啡 安静 设计感")
    parser.add_argument("--provider", choices=("all", "zhipu", "bocha", "tavily"), default="all")
    parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args()

    providers = build_available_search_providers(get_settings())
    selected = providers if args.provider == "all" else (
        {args.provider: providers[args.provider]} if args.provider in providers else {}
    )
    if not selected:
        print("没有找到对应的 API Key；请在 api.env 中配置后再试。")
        return

    for name, provider in selected.items():
        started = perf_counter()
        try:
            results = await provider.search(args.query, max(1, min(args.limit, 5)))
            elapsed = perf_counter() - started
            print(f"\n[{name}] {elapsed:.2f} 秒，{len(results)} 条结果")
            for index, item in enumerate(results, 1):
                print(f"{index}. {item.title}\n   {item.url}\n   {item.snippet[:180]}")
        except Exception as exc:
            print(f"\n[{name}] 请求失败：{type(exc).__name__}: {exc}")
        finally:
            await provider.close()


if __name__ == "__main__":
    asyncio.run(main())
