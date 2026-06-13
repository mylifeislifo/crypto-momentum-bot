"""CLI entry point: btcbot paper | btcbot live"""

import asyncio
from typing import Optional

import typer

app = typer.Typer(help="BTC Futures Day Trading Bot")


@app.command()
def paper(
    config: str = typer.Option("config/default.yaml", help="Base config file"),
) -> None:
    """Run in paper trading mode."""
    from .main import main
    asyncio.run(main(config_path=config, override_path="config/paper.yaml"))


@app.command()
def live(
    config: str = typer.Option("config/default.yaml", help="Base config file"),
) -> None:
    """Run in live trading mode. Requires BINANCE_API_KEY in .env"""
    from .main import main
    asyncio.run(main(config_path=config, override_path="config/live.yaml"))


@app.command()
def bitget(
    config: str = typer.Option("config/default.yaml", help="Base config file"),
) -> None:
    """Run the winner-asymmetry bot on Bitget (config/bitget.yaml override).

    Ships in paper mode; flipping to live requires the §1.3 gate + BITGET_* in .env.
    """
    from .main import main
    asyncio.run(main(config_path=config, override_path="config/bitget.yaml"))
