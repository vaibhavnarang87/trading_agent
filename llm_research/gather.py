"""
Research gatherer — collects the raw text the analyzer reads.

gather() takes an injected search function so it has no hard dependency: on your
machine wire search_fn to a news source (a news API, or Claude's own web_search
tool via the Anthropic API). It returns concatenated headline/article text plus
the source URLs, which feed analyzer.analyze().

This step pulls FACTS. It makes no judgments — that's the analyzer's structured
extraction, and the decision is your rules'.
"""
from __future__ import annotations

from typing import Callable

# search_fn(query) -> list of {"title": str, "snippet": str, "url": str}
SearchFn = Callable[[str], list[dict]]


def gather(symbol: str, search_fn: SearchFn, max_items: int = 8) -> tuple[str, list[str]]:
    queries = [
        f"{symbol} stock news today",
        f"{symbol} earnings guidance",
        f"{symbol} analyst price target",
    ]
    seen, lines, sources = set(), [], []
    for q in queries:
        for item in search_fn(q)[:max_items]:
            url = item.get("url", "")
            if url in seen:
                continue
            seen.add(url)
            title = item.get("title", "").strip()
            snippet = item.get("snippet", "").strip()
            if title or snippet:
                lines.append(f"- {title}: {snippet}")
                sources.append(url)
    text = f"Research for {symbol}:\n" + "\n".join(lines)
    return text, sources
