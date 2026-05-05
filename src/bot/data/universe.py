"""KRW universe selection: top-N by 24 h quote volume."""
from __future__ import annotations

import httpx

from bot.core.logging import get_logger

log = get_logger(__name__)

# Tokens that trade on Upbit KRW but aren't "crypto alpha" assets
_EXCLUDE = frozenset(
    {
        "KRW-USDT",
        "KRW-USDC",
        "KRW-DAI",
        "KRW-BUSD",
        "KRW-TUSD",
        "KRW-WBTC",
    }
)

_TICKER_URL = "https://api.upbit.com/v1/ticker"
_MARKET_URL = "https://api.upbit.com/v1/market/all"


def _all_krw_markets() -> list[str]:
    """Return every KRW-* market from Upbit."""
    resp = httpx.get(_MARKET_URL, params={"isDetails": "false"}, timeout=10)
    resp.raise_for_status()
    return [
        m["market"]
        for m in resp.json()
        if m["market"].startswith("KRW-") and m["market"] not in _EXCLUDE
    ]


def _volumes(markets: list[str]) -> dict[str, float]:
    """Return {market: acc_trade_price_24h} for each market."""
    result: dict[str, float] = {}
    batch_size = 100
    for i in range(0, len(markets), batch_size):
        batch = markets[i : i + batch_size]
        resp = httpx.get(
            _TICKER_URL,
            params={"markets": ",".join(batch)},
            timeout=10,
        )
        resp.raise_for_status()
        for t in resp.json():
            result[t["market"]] = float(t.get("acc_trade_price_24h", 0))
    return result


def select_universe(cfg) -> list[str]:
    """Return top-N KRW symbols by 24 h quote volume."""
    n: int = cfg.universe.top_n
    markets = _all_krw_markets()
    vols = _volumes(markets)
    ranked = sorted(markets, key=lambda m: vols.get(m, 0), reverse=True)
    selected = ranked[:n]
    log.info("universe_selected", n=len(selected), symbols=selected)
    return selected
