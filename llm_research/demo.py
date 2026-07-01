"""
Demo: research text -> LLM analysis (structured) -> YOUR deterministic rule.

Runs offline with a mocked LLM response (real shape) over the real AMD research
gathered on 2026-06-30. On your machine, drop in the real Anthropic call (set
ANTHROPIC_API_KEY) and a real news fetch — the rest is identical.

The point this demonstrates: the LLM hands your rules a STRUCTURED signal, and a
plain deterministic function makes the call. Swap in your own rule logic.
"""
from __future__ import annotations

from .analyzer import analyze
from .signal import ResearchSignal


# ---- real research text (condensed from what we pulled live for AMD) ----
AMD_RESEARCH = """
AMD hit an all-time high near $580, up ~7% today and ~310% over the past year.
CEO Lisa Su raised the server-CPU TAM forecast from ~$60B (18%/yr) to over
$120B (>35%/yr) by 2030, citing agentic AI driving CPU demand. MI450/Helios
products launch in H2 2026. Cantor Fitzgerald raised its price target to $700
from $500. However, AMD trades around 71x forward earnings and ~177 trailing
P/E; InvestingPro flags the shares as possibly overvalued, and it sits within
1% of its 52-week high. Q1 revenue grew 38% YoY led by the data-center segment.
"""


def mock_llm(_research_text: str) -> str:
    """Stand-in for the Anthropic call. Returns the JSON the analyst would emit.
    Note: facts + sentiment ONLY — no buy/sell view, by design."""
    return """{
      "earnings_beat": true,
      "guidance_direction": "raised",
      "news_sentiment": "positive",
      "sentiment_score": 0.6,
      "key_themes": ["agentic AI CPU demand", "data-center growth", "MI450/Helios launch"],
      "catalysts": ["MI450/Helios launch H2 2026"],
      "risk_flags": ["valuation stretched (~71x fwd)", "near 52-week high"],
      "summary": "Strong AI-driven fundamentals and raised guidance, but the stock is richly valued and near highs."
    }"""


# ---- YOUR deterministic rule (you author this; it touches the decision) ----
def example_rule(sig: ResearchSignal) -> dict:
    """
    A PLACEHOLDER deterministic rule consuming the structured signal. Replace
    with your own logic. It returns a structured intent, NOT an executed trade —
    execution stays behind the risk governor and your place() trigger.

    This example: flag 'interesting_long' only if fundamentals are improving
    AND sentiment is positive AND valuation isn't flagged as stretched.
    Deterministic: same signal in -> same flag out. Backtestable.
    """
    stretched = any("valuation" in r.lower() for r in sig.risk_flags)
    bullish_fundamentals = sig.earnings_beat and sig.guidance_direction == "raised"
    positive = sig.news_sentiment == "positive" and sig.sentiment_score > 0.3

    if bullish_fundamentals and positive and not stretched:
        flag = "fundamentals+sentiment align, valuation OK -> candidate (your call)"
    elif bullish_fundamentals and positive and stretched:
        flag = "fundamentals+sentiment strong BUT valuation stretched -> caution"
    else:
        flag = "no alignment -> stand pat"
    return {"symbol": sig.symbol, "rule_flag": flag}


def main():
    print("=== LLM research layer demo (analysis -> structured -> your rule) ===\n")
    sig = analyze("AMD", AMD_RESEARCH, sources=["live news 2026-06-30"], llm_fn=mock_llm)
    print(sig.render())
    print()
    decision = example_rule(sig)
    print(f"YOUR RULE OUTPUT: {decision['rule_flag']}")
    print("\nNote: the rule produced a FLAG, not a trade. Execution still requires")
    print("the risk governor and your own place() trigger. The LLM analyzed; your")
    print("deterministic rule decided; nothing was placed.")


if __name__ == "__main__":
    main()
