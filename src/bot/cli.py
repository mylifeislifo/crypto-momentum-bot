"""Bot CLI: backtest / paper / live / fetch."""
from __future__ import annotations

from pathlib import Path

import typer

from bot.config.loader import load_config
from bot.core.logging import get_logger, setup_logging

app = typer.Typer(add_completion=False, help="Crypto trading bot")


def _load(config: Path, override: Path | None) -> object:
    cfg = load_config(config, *( [override] if override else [] ))
    setup_logging(cfg.logging.level, cfg.logging.json_logs, cfg.logging.dir)
    return cfg


@app.command()
def backtest(
    config: Path = typer.Option(Path("config/default.yaml"), "--config", "-c"),
    override: Path = typer.Option(Path("config/backtest.yaml"), "--override", "-o"),
) -> None:
    """Run a vectorized backtest."""
    cfg = _load(config, override)
    log = get_logger("cli.backtest")
    log.info("backtest_loaded", mode=cfg.mode, exchange=cfg.exchange)
    from bot.backtest.runner import run_backtest

    result = run_backtest(cfg)
    typer.echo(result.summary())


@app.command()
def paper(
    config: Path = typer.Option(Path("config/default.yaml"), "--config", "-c"),
    override: Path = typer.Option(Path("config/paper.yaml"), "--override", "-o"),
) -> None:
    """Run paper trading against live Upbit data."""
    cfg = _load(config, override)
    log = get_logger("cli.paper")
    log.info("paper_loaded", mode=cfg.mode)
    from bot.live.runner import run_live

    run_live(cfg)


@app.command()
def live(
    config: Path = typer.Option(Path("config/default.yaml"), "--config", "-c"),
    override: Path = typer.Option(Path("config/live.yaml"), "--override", "-o"),
    confirm: bool = typer.Option(False, "--i-understand-real-money"),
) -> None:
    """Run live trading on Upbit. Requires explicit confirmation."""
    cfg = _load(config, override)
    log = get_logger("cli.live")
    if not confirm and not cfg.dry_run:
        typer.echo("Refusing to start live with real money without --i-understand-real-money")
        raise typer.Exit(code=2)
    log.info("live_loaded", mode=cfg.mode, dry_run=cfg.dry_run)
    from bot.live.runner import run_live

    run_live(cfg)


@app.command()
def fetch(
    config: Path = typer.Option(Path("config/default.yaml"), "--config", "-c"),
    days: int = typer.Option(180, "--days"),
) -> None:
    """Fetch and cache OHLCV for the universe."""
    cfg = _load(config, None)
    log = get_logger("cli.fetch")
    from bot.data.universe import select_universe
    from bot.data.fetcher import fetch_history_for_symbols

    symbols = select_universe(cfg)
    log.info("fetch_universe", n=len(symbols), symbols=symbols)
    fetch_history_for_symbols(symbols, cfg.data.interval, days, cfg.cache_path())


if __name__ == "__main__":
    app()
