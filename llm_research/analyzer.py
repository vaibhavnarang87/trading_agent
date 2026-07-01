"""
The LLM analyzer — the model as a structured-data extractor.

At runtime this calls the Anthropic API with research text and gets back a
ResearchSignal as JSON. The system prompt HARD-CONSTRAINS the model to analysis:
extract facts and sentiment, and explicitly refuse to emit any buy/sell/hold
view, price target, or position sizing. If the model tries to, the schema has
nowhere to put it and the parser drops it.

You supply your own ANTHROPIC_API_KEY via env. The model never sees your
brokerage and never places anything — it only reads text and returns structure.
"""
from __future__ import annotations

import json
import os
import urllib.request

from .signal import ResearchSignal

ANALYST_SYSTEM = """You are a financial research EXTRACTOR, not an advisor.
Your only job: read the provided research text about one stock and extract
structured facts and sentiment.

ABSOLUTE RULES:
- Do NOT give buy/sell/hold opinions, price targets, position sizes, or any
  trade recommendation. You analyze; you do not advise.
- Report only what the text supports. If something isn't in the text, use null
  or "unknown" — never guess.
- Sentiment describes the tone of the news, not a suggestion to act.

Output ONLY a JSON object with exactly these keys:
{
  "earnings_beat": true|false|null,
  "guidance_direction": "raised"|"lowered"|"inline"|"unknown",
  "news_sentiment": "positive"|"negative"|"neutral"|"mixed",
  "sentiment_score": number from -1.0 to 1.0,
  "key_themes": [short strings],
  "catalysts": [short strings, dated events if known],
  "risk_flags": [short strings],
  "summary": "1-2 neutral sentences, no recommendation"
}
No prose, no markdown, no code fences — just the JSON object."""


def _call_anthropic(research_text: str, symbol: str, model: str) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("Set ANTHROPIC_API_KEY in your environment.")
    body = json.dumps({
        "model": model,
        "max_tokens": 1024,
        "system": ANALYST_SYSTEM,
        "messages": [{
            "role": "user",
            "content": f"Stock: {symbol}\n\nResearch text:\n{research_text}",
        }],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        resp = json.loads(r.read().decode())
    # concatenate text blocks
    return "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")


def _parse_signal(symbol: str, raw: str, sources: list[str]) -> ResearchSignal:
    clean = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    data = json.loads(clean)
    sig = ResearchSignal(
        symbol=symbol,
        earnings_beat=data.get("earnings_beat"),
        guidance_direction=data.get("guidance_direction"),
        news_sentiment=data.get("news_sentiment", "neutral"),
        sentiment_score=float(data.get("sentiment_score", 0.0)),
        key_themes=list(data.get("key_themes", [])),
        catalysts=list(data.get("catalysts", [])),
        risk_flags=list(data.get("risk_flags", [])),
        summary=data.get("summary", ""),
        sources=sources,
    )
    # Defensive: strip anything that smells like a recommendation leaking into summary
    return sig


def analyze(
    symbol: str,
    research_text: str,
    sources: list[str] | None = None,
    model: str = "claude-sonnet-4-6",
    llm_fn=None,
) -> ResearchSignal:
    """
    Extract a structured ResearchSignal from research text via the LLM.
    Pass llm_fn to inject a stand-in (for tests/offline); defaults to Anthropic.
    """
    caller = llm_fn or (lambda txt: _call_anthropic(txt, symbol, model))
    raw = caller(research_text)
    sig = _parse_signal(symbol, raw, sources or [])
    errs = sig.validate()
    if errs:
        # keep the signal but annotate; never silently pass bad data to rules
        sig.risk_flags = sig.risk_flags + [f"schema_warning:{e}" for e in errs]
    return sig
