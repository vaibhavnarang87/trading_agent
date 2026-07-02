"""
Universe scan — research beyond the watchlist.

Runs the same deterministic quality-dip screen over a broad large-cap universe
(~100 liquid S&P names, editable below), so the daily briefing can surface
stocks you do NOT already follow. Same rules, same transparency: every hit
carries the exact reasons it passed. Research, not advice.

Efficiency: one batch price download for the whole universe, then earnings
history is fetched ONLY for names that already pass the dip filter (keeps the
daily job fast and rate-limit friendly).
"""
from __future__ import annotations

from .daily_briefing import StockCard, compute_range_pos
from .research.social_sentiment import SocialSnapshot
from .screener import Candidate, ScreenThresholds, run_screen

# Editable. Roughly the S&P 100: liquid, large-cap, equities-only.
UNIVERSE = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "BRK-B", "LLY",
    "V", "UNH", "XOM", "JPM", "JNJ", "PG", "MA", "AVGO", "HD", "CVX", "MRK",
    "ABBV", "COST", "PEP", "ADBE", "KO", "WMT", "CRM", "BAC", "MCD", "ACN",
    "NFLX", "AMD", "CSCO", "TMO", "LIN", "ORCL", "ABT", "CMCSA", "DIS",
    "INTC", "VZ", "WFC", "INTU", "IBM", "TXN", "QCOM", "CAT", "NKE", "PM",
    "GE", "AMGN", "HON", "UNP", "LOW", "SPGI", "RTX", "BA", "GS", "T",
    "ISRG", "BLK", "ELV", "SCHW", "BKNG", "PLD", "SYK", "AXP", "MDT", "LMT",
    "DE", "TJX", "MDLZ", "ADP", "CVS", "GILD", "C", "VRTX", "AMT", "CI",
    "REGN", "MO", "SBUX", "SO", "ZTS", "BMY", "DUK", "BDX", "TGT", "APD",
    "PNC", "CL", "FDX", "ITW", "EMR", "CME", "USB", "MMC", "NOC", "NSC",
    "COP", "EOG", "SLB", "PFE", "DHR",
]

MAX_EARNINGS_LOOKUPS = 15   # cap per run: only the most beaten-down get the extra call
MAX_RESULTS = 5             # surface at most this many universe hits per day


def _batch_closes(symbols: list[str]) -> dict[str, list[float]]:
    """One yfinance batch download -> {symbol: [closes...]}. Missing/failed
    symbols are simply absent."""
    import yfinance as yf
    out: dict[str, list[float]] = {}
    try:
        df = yf.download(symbols, period="1y", interval="1d",
                         group_by="ticker", progress=False, threads=True)
    except Exception:
        return out
    for sym in symbols:
        try:
            closes = df[sym]["Close"].dropna().tolist()
            if closes:
                out[sym] = [float(c) for c in closes]
        except Exception:
            continue
    return out


def _fetch_beat_rate(symbol: str) -> float | None:
    """Earnings beat rate from yfinance earnings history. None on any failure."""
    try:
        import yfinance as yf
        t = yf.Ticker(symbol)
        h = getattr(t, "earnings_history", None)
        if h is None or getattr(h, "empty", True):
            return None
        h = h.dropna(subset=["epsActual", "epsEstimate"])
        if len(h) == 0:
            return None
        beats = (h["epsActual"] > h["epsEstimate"]).sum()
        return float(beats) / float(len(h))
    except Exception:
        return None


def scan_universe(
    sent_index: dict,
    exclude: set[str] | frozenset[str] = frozenset(),
    thresholds: ScreenThresholds | None = None,
) -> list[Candidate]:
    """Screen the universe (minus `exclude`, i.e. your watchlist) and return
    passing candidates only, capped at MAX_RESULTS, most beaten-down first."""
    t = thresholds or ScreenThresholds()
    symbols = [s for s in UNIVERSE if s not in exclude]
    closes_map = _batch_closes(symbols)

    # Stage 1: cheap dip filter on the batch data.
    shortlist: list[tuple[float, str, list[float]]] = []
    for sym, closes in closes_map.items():
        if len(closes) < 60:
            continue
        price = closes[-1]
        rp = compute_range_pos(closes, price)
        if rp is not None and rp <= t.max_range_pos:
            shortlist.append((rp, sym, closes))
    shortlist.sort()                      # most beaten-down first
    shortlist = shortlist[:MAX_EARNINGS_LOOKUPS]

    # Stage 2: earnings quality + sentiment, full screen on the shortlist.
    cards = []
    for rp, sym, closes in shortlist:
        row = sent_index.get(sym)
        social = None
        if row:
            social = SocialSnapshot(
                symbol=sym,
                mentions_24h=row["mentions"],
                mentions_prev_24h=row["prev"],
                sentiment=row.get("sentiment", "neutral"),
                rank=row.get("rank"),
            )
        cards.append(StockCard(
            symbol=sym, price=closes[-1], range_pos=rp,
            next_earnings=None, days_to_earnings=None,
            beat_rate=_fetch_beat_rate(sym), social=social,
        ))

    passing = [c for c in run_screen(cards, t) if c.passed]
    for c in passing:
        c.reasons.append("outside watchlist (universe scan)")
    return passing[:MAX_RESULTS]


def render_universe_section(candidates: list[Candidate]) -> str:
    L = []
    L.append("UNIVERSE SCAN — quality dips beyond the watchlist (research, not advice):")
    L.append("-" * 60)
    if not candidates:
        L.append("  No universe names passed the screen today.")
    else:
        for c in candidates:
            price = f"${c.price:,.2f}" if c.price else "n/a"
            L.append(f"{c.symbol:<6} {price:<11} {'; '.join(c.reasons)}")
    L.append("-" * 60)
    L.append(f"Scanned ~{len(UNIVERSE)} large caps with fixed, shown criteria. A hit")
    L.append("is a research prompt, not a recommendation. You decide and place trades.")
    return "\n".join(L)
