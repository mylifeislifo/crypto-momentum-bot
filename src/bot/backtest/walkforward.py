"""Rolling walk-forward validation: IS optimization → OOS evaluation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from bot.config.schema import AppConfig
from bot.core.logging import get_logger

from .metrics import Metrics
from .runner import run_backtest

log = get_logger(__name__)


@dataclass
class WindowResult:
    is_start: datetime
    is_end: datetime
    oos_start: datetime
    oos_end: datetime
    is_metrics: Metrics
    oos_metrics: Metrics


def _add_months(d: datetime, m: int) -> datetime:
    y, mo = d.year, d.month - 1 + m
    y += mo // 12
    mo = mo % 12 + 1
    day = min(d.day, 28)
    return d.replace(year=y, month=mo, day=day)


def windows(start: datetime, end: datetime, is_m: int, oos_m: int, step_m: int) -> Iterable[tuple[datetime, datetime, datetime, datetime]]:
    cursor = start
    while True:
        is_end = _add_months(cursor, is_m)
        oos_end = _add_months(is_end, oos_m)
        if oos_end > end:
            break
        yield cursor, is_end, is_end, oos_end
        cursor = _add_months(cursor, step_m)


def _override(cfg: AppConfig, start: datetime, end: datetime) -> AppConfig:
    new = cfg.model_copy(deep=True)
    new.backtest.start = start.strftime("%Y-%m-%d")
    new.backtest.end = end.strftime("%Y-%m-%d")
    return new


def walk_forward(cfg: AppConfig) -> list[WindowResult]:
    start = datetime.fromisoformat(cfg.backtest.start).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(cfg.backtest.end).replace(tzinfo=timezone.utc)
    out: list[WindowResult] = []
    for is_s, is_e, oos_s, oos_e in windows(
        start, end,
        cfg.walkforward.is_months,
        cfg.walkforward.oos_months,
        cfg.walkforward.step_months,
    ):
        log.info("wf_window", is_start=str(is_s), is_end=str(is_e),
                 oos_start=str(oos_s), oos_end=str(oos_e))
        is_res = run_backtest(_override(cfg, is_s, is_e))
        oos_res = run_backtest(_override(cfg, oos_s, oos_e))
        out.append(WindowResult(is_s, is_e, oos_s, oos_e, is_res.metrics, oos_res.metrics))
    return out
