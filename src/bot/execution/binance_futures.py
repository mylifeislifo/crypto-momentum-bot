"""Binance USDT-M Futures gateway.

On startup: set_leverage(2) + set_margin_mode(ISOLATED).
Uses python-binance AsyncClient for REST; tenacity for retries.
Keeps ListenKey alive every 50 minutes in a background task.
"""

import asyncio
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import structlog
from binance import AsyncClient
from binance.exceptions import BinanceAPIException
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..core.clock import utc_now
from ..core.enums import MarginType, OrderSide, OrderStatus, OrderType, PositionSide, Side
from ..core.types import Fill, Order, Position
from .gateway_base import FuturesGateway

logger = structlog.get_logger(__name__)

_RETRY_POLICY = dict(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, max=4),
    retry=retry_if_exception_type((BinanceAPIException, asyncio.TimeoutError)),
    reraise=True,
)


class BinanceFuturesGateway(FuturesGateway):
    def __init__(self, api_key: str, secret_key: str, testnet: bool = False) -> None:
        self._api_key = api_key
        self._secret_key = secret_key
        self._testnet = testnet
        self._client: Optional[AsyncClient] = None
        self._listen_key: Optional[str] = None
        self._keepalive_task: Optional[asyncio.Task] = None

    async def connect(self) -> None:
        self._client = await AsyncClient.create(
            api_key=self._api_key,
            api_secret=self._secret_key,
            testnet=self._testnet,
        )
        logger.info("binance.connected", testnet=self._testnet)

    async def disconnect(self) -> None:
        if self._keepalive_task:
            self._keepalive_task.cancel()
        if self._client:
            await self._client.close_connection()
        logger.info("binance.disconnected")

    # ------------------------------------------------------------------
    # FuturesGateway interface
    # ------------------------------------------------------------------

    async def set_leverage(self, symbol: str, leverage: int) -> None:
        await self._client.futures_change_leverage(symbol=symbol, leverage=leverage)
        logger.info("binance.leverage_set", symbol=symbol, leverage=leverage)

    async def set_margin_mode(self, symbol: str, margin_type: MarginType) -> None:
        try:
            await self._client.futures_change_margin_type(
                symbol=symbol, marginType=margin_type.value
            )
            logger.info("binance.margin_mode_set", symbol=symbol, mode=margin_type.value)
        except BinanceAPIException as e:
            # code -4046: already in that margin type — not an error
            if e.code == -4046:
                logger.debug("binance.margin_mode_already_set", mode=margin_type.value)
            else:
                raise

    @retry(**_RETRY_POLICY)
    async def place_order(
        self,
        symbol: str,
        side: OrderSide,
        position_side: PositionSide,
        order_type: OrderType,
        qty: Decimal,
        price: Optional[Decimal] = None,
        stop_price: Optional[Decimal] = None,
        reduce_only: bool = False,
        client_order_id: Optional[str] = None,
    ) -> Order:
        params: dict = {
            "symbol": symbol,
            "side": side.value,
            "positionSide": position_side.value,
            "type": order_type.value,
            "quantity": str(qty),
        }
        if price is not None:
            params["price"] = str(price)
            params["timeInForce"] = "GTC"
        if stop_price is not None:
            params["stopPrice"] = str(stop_price)
        if reduce_only:
            params["reduceOnly"] = "true"
        if client_order_id:
            params["newClientOrderId"] = client_order_id

        raw = await self._client.futures_create_order(**params)
        logger.info(
            "binance.order_placed",
            order_id=raw["orderId"],
            type=order_type.value,
            side=side.value,
            qty=str(qty),
            stop=str(stop_price) if stop_price else None,
        )
        return self._parse_order(raw, side, position_side, order_type, qty, price, stop_price, reduce_only)

    @retry(**_RETRY_POLICY)
    async def cancel_order(self, symbol: str, order_id: str) -> None:
        try:
            await self._client.futures_cancel_order(symbol=symbol, orderId=order_id)
            logger.debug("binance.order_cancelled", order_id=order_id)
        except BinanceAPIException as e:
            # -2011: order already closed/cancelled — treat as success
            if e.code == -2011:
                logger.debug("binance.order_already_gone", order_id=order_id)
            else:
                raise

    async def get_position(self, symbol: str) -> Optional[Position]:
        rows = await self._client.futures_position_information(symbol=symbol)
        for row in rows:
            amt = Decimal(row["positionAmt"])
            if amt == 0:
                continue
            side = Side.LONG if amt > 0 else Side.SHORT
            return Position(
                position_id=row.get("positionSide", "BOTH"),
                symbol=row["symbol"],
                side=side,
                position_side=PositionSide(row.get("positionSide", "BOTH")),
                qty=abs(amt),
                entry_price=Decimal(row["entryPrice"]),
                current_price=Decimal(row["markPrice"]),
                unrealized_pnl=Decimal(row["unRealizedProfit"]),
                leverage=int(row["leverage"]),
                sl_order_id="",
                sl_price=Decimal("0"),
                opened_at=utc_now(),
                updated_at=utc_now(),
            )
        return None

    async def get_balance(self) -> Decimal:
        assets = await self._client.futures_account_balance()
        for asset in assets:
            if asset["asset"] == "USDT":
                return Decimal(asset["availableBalance"])
        return Decimal("0")

    async def close_all_positions(self, symbol: str) -> list[Fill]:
        rows = await self._client.futures_position_information(symbol=symbol)
        fills = []
        for row in rows:
            amt = Decimal(row["positionAmt"])
            if amt == 0:
                continue
            close_side = OrderSide.SELL if amt > 0 else OrderSide.BUY
            pos_side = PositionSide(row.get("positionSide", "BOTH"))
            try:
                order = await self.place_order(
                    symbol=symbol,
                    side=close_side,
                    position_side=pos_side,
                    order_type=OrderType.MARKET,
                    qty=abs(amt),
                    reduce_only=True,
                )
                logger.info("binance.position_closed", symbol=symbol, qty=str(abs(amt)))
                fills.append(Fill(
                    order_id=order.id,
                    symbol=symbol,
                    side=Side.LONG if amt > 0 else Side.SHORT,
                    position_side=pos_side,
                    qty=abs(amt),
                    avg_price=Decimal(row["markPrice"]),
                    commission=Decimal("0"),
                    commission_asset="USDT",
                    ts=utc_now(),
                    is_entry=False,
                ))
            except Exception as exc:
                logger.error("binance.close_position_failed", error=str(exc))
        return fills

    # ------------------------------------------------------------------
    # ListenKey keepalive
    # ------------------------------------------------------------------

    async def start_listen_key_keepalive(self) -> None:
        self._listen_key = (await self._client.futures_stream_get_listen_key())["listenKey"]
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())

    async def _keepalive_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(50 * 60)  # 50 minutes
                await self._client.futures_stream_keepalive(self._listen_key)
                logger.debug("binance.listen_key_refreshed")
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning("binance.listen_key_refresh_failed", error=str(exc))

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_order(
        raw: dict,
        side: OrderSide,
        position_side: PositionSide,
        order_type: OrderType,
        qty: Decimal,
        price: Optional[Decimal],
        stop_price: Optional[Decimal],
        reduce_only: bool,
    ) -> Order:
        status_map = {
            "NEW": OrderStatus.NEW,
            "FILLED": OrderStatus.FILLED,
            "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
            "CANCELED": OrderStatus.CANCELED,
        }
        return Order(
            id=str(raw["orderId"]),
            client_order_id=raw.get("clientOrderId", ""),
            symbol=raw["symbol"],
            side=side,
            position_side=position_side,
            order_type=order_type,
            qty=qty,
            price=price,
            stop_price=stop_price,
            status=status_map.get(raw.get("status", "NEW"), OrderStatus.NEW),
            ts=datetime.fromtimestamp(raw["updateTime"] / 1000, tz=timezone.utc),
            reduce_only=reduce_only,
        )
