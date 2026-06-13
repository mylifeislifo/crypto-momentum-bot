"""Bitget USDT-M perpetual (v2 Mix) gateway.

Implements the exchange-agnostic ``FuturesGateway`` contract so the existing
winner-asymmetry L3 exit discipline (breakeven floor + time stop + ATR trail +
place-before-cancel SL in order_manager/risk.trail) runs on Bitget *unchanged* —
this module adds ZERO strategy logic, only Bitget REST plumbing.

Design choices:
  - **One-way (net) position mode** + ``reduceOnly`` for SL/closes. The bot holds
    one net position per symbol; this keeps the mapping from OrderSide/reduce_only
    to Bitget params simple and matches get_position's sign-based side inference.
  - **STOP_MARKET → plan (trigger) order.** Bitget separates resting stops into
    "plan orders" with their own place/cancel endpoints, so place_order routes
    stops to place-plan-order and tracks their ids to route cancel_order correctly.
  - Money values are ``Decimal`` (trading §1.2); leverage is asserted ≤ 2x (§1.1);
    transient calls retry with exponential backoff via tenacity (§4).

Auth: HMAC-SHA256 over ``timestamp + METHOD + requestPath(+query) + body`` →
base64, sent as ACCESS-SIGN with ACCESS-KEY / ACCESS-TIMESTAMP / ACCESS-PASSPHRASE.
Secrets come from the environment (security §1.1) — never hard-coded.

⚠️ Gate status: this gateway is NOT live-validated. Validate on Bitget demo
(product_type "SUSDT-FUTURES") and clear the §1.3 gate + explicit approval
(progressive-gate §2.4) before pointing it at real funds.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import time
from decimal import Decimal
from typing import Any, Optional
from urllib.parse import urlencode

import aiohttp
import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..core.clock import utc_now
from ..core.enums import MarginType, OrderSide, OrderStatus, OrderType, PositionSide, Side
from ..core.types import Fill, Order, Position
from .gateway_base import FuturesGateway

logger = structlog.get_logger(__name__)

_BASE_URL = "https://api.bitget.com"
_MAX_LEVERAGE = 2  # trading §1.1 hard cap
_SUCCESS_CODE = "00000"
# trigger/stop order types route to the plan-order endpoints, not place-order.
_PLAN_TYPES = {OrderType.STOP_MARKET, OrderType.TAKE_PROFIT_MARKET}


class BitgetAPIException(Exception):
    """Non-success Bitget envelope (``code != "00000"``)."""

    def __init__(self, code: str, msg: str) -> None:
        self.code = code
        self.msg = msg
        super().__init__(f"bitget [{code}] {msg}")


_RETRY_POLICY = dict(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, max=4),
    retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError)),
    reraise=True,
)


class BitgetFuturesGateway(FuturesGateway):
    def __init__(
        self,
        api_key: str,
        secret_key: str,
        passphrase: str,
        product_type: str = "USDT-FUTURES",
        margin_coin: str = "USDT",
    ) -> None:
        self._api_key = api_key
        self._secret_key = secret_key
        self._passphrase = passphrase
        self._product_type = product_type
        self._margin_coin = margin_coin
        self._session: Optional[aiohttp.ClientSession] = None
        # orderIds that are plan (trigger) orders → cancel via cancel-plan-order
        self._plan_orders: set[str] = set()

    async def connect(self) -> None:
        self._session = aiohttp.ClientSession()
        # Force one-way (net) mode so reduceOnly semantics hold. Best-effort: ignore
        # "no change" responses (already in that mode).
        await self._set_position_mode_one_way()
        logger.info("bitget.connected", product_type=self._product_type)

    async def disconnect(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
        logger.info("bitget.disconnected")

    # ------------------------------------------------------------------
    # Signing & transport
    # ------------------------------------------------------------------

    def _sign(self, ts: str, method: str, request_path: str, body: str) -> str:
        prehash = ts + method.upper() + request_path + body
        digest = hmac.new(self._secret_key.encode(), prehash.encode(), hashlib.sha256).digest()
        return base64.b64encode(digest).decode()

    @retry(**_RETRY_POLICY)
    async def _request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        body: Optional[dict] = None,
    ) -> Any:
        if self._session is None:
            raise RuntimeError("BitgetFuturesGateway not connected — call connect() first")

        query = f"?{urlencode(params)}" if params else ""
        body_str = json.dumps(body) if body else ""
        request_path = path + query
        ts = str(int(time.time() * 1000))

        headers = {
            "ACCESS-KEY": self._api_key,
            "ACCESS-SIGN": self._sign(ts, method, request_path, body_str),
            "ACCESS-TIMESTAMP": ts,
            "ACCESS-PASSPHRASE": self._passphrase,
            "Content-Type": "application/json",
            "locale": "en-US",
        }
        async with self._session.request(
            method, _BASE_URL + request_path, data=body_str or None, headers=headers
        ) as resp:
            payload = await resp.json()

        if payload.get("code") != _SUCCESS_CODE:
            raise BitgetAPIException(payload.get("code", "unknown"), payload.get("msg", ""))
        return payload.get("data")

    # ------------------------------------------------------------------
    # FuturesGateway interface
    # ------------------------------------------------------------------

    async def set_leverage(self, symbol: str, leverage: int) -> None:
        assert leverage <= _MAX_LEVERAGE, (
            f"leverage {leverage} exceeds hard cap {_MAX_LEVERAGE} (trading §1.1)"
        )
        await self._request(
            "POST", "/api/v2/mix/account/set-leverage",
            body={
                "symbol": symbol,
                "productType": self._product_type,
                "marginCoin": self._margin_coin,
                "leverage": str(leverage),
            },
        )
        logger.info("bitget.leverage_set", symbol=symbol, leverage=leverage)

    async def set_margin_mode(self, symbol: str, margin_type: MarginType) -> None:
        mode = "isolated" if margin_type == MarginType.ISOLATED else "crossed"
        try:
            await self._request(
                "POST", "/api/v2/mix/account/set-margin-mode",
                body={
                    "symbol": symbol,
                    "productType": self._product_type,
                    "marginCoin": self._margin_coin,
                    "marginMode": mode,
                },
            )
            logger.info("bitget.margin_mode_set", symbol=symbol, mode=mode)
        except BitgetAPIException as e:
            # idempotent setup call — a "no change / already set" response is benign
            if self._is_no_change(e):
                logger.debug("bitget.margin_mode_already_set", mode=mode)
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
        bitget_side = "buy" if side == OrderSide.BUY else "sell"
        common = {
            "symbol": symbol,
            "productType": self._product_type,
            "marginMode": "isolated",
            "marginCoin": self._margin_coin,
            "size": str(qty),
            "side": bitget_side,
            "reduceOnly": "YES" if reduce_only else "NO",
        }
        if client_order_id:
            common["clientOid"] = client_order_id

        is_plan = order_type in _PLAN_TYPES
        if is_plan:
            if stop_price is None:
                raise ValueError(f"{order_type.value} requires a stop_price")
            body = {
                **common,
                "planType": "normal_plan",
                "orderType": "market",
                "triggerPrice": str(stop_price),
                "triggerType": "mark_price",
            }
            data = await self._request("POST", "/api/v2/mix/order/place-plan-order", body=body)
        else:
            body = {**common, "orderType": "market" if order_type == OrderType.MARKET else "limit"}
            if order_type == OrderType.LIMIT:
                if price is None:
                    raise ValueError("LIMIT order requires a price")
                body["price"] = str(price)
                body["force"] = "gtc"
            data = await self._request("POST", "/api/v2/mix/order/place-order", body=body)

        order_id = str(data["orderId"])
        if is_plan:
            self._plan_orders.add(order_id)

        logger.info(
            "bitget.order_placed",
            order_id=order_id, type=order_type.value, side=bitget_side,
            qty=str(qty), stop=str(stop_price) if stop_price else None, plan=is_plan,
        )
        return Order(
            id=order_id,
            client_order_id=str(data.get("clientOid", client_order_id or "")),
            symbol=symbol,
            side=side,
            position_side=position_side,
            order_type=order_type,
            qty=qty,
            price=price,
            stop_price=stop_price,
            status=OrderStatus.NEW if is_plan else OrderStatus.FILLED,
            ts=utc_now(),
            reduce_only=reduce_only,
        )

    @retry(**_RETRY_POLICY)
    async def cancel_order(self, symbol: str, order_id: str) -> None:
        try:
            if order_id in self._plan_orders:
                await self._request(
                    "POST", "/api/v2/mix/order/cancel-plan-order",
                    body={
                        "symbol": symbol,
                        "productType": self._product_type,
                        "marginCoin": self._margin_coin,
                        "planType": "normal_plan",
                        "orderIdList": [{"orderId": order_id}],
                    },
                )
                self._plan_orders.discard(order_id)
            else:
                await self._request(
                    "POST", "/api/v2/mix/order/cancel-order",
                    body={
                        "symbol": symbol,
                        "productType": self._product_type,
                        "marginCoin": self._margin_coin,
                        "orderId": order_id,
                    },
                )
            logger.debug("bitget.order_cancelled", order_id=order_id)
        except BitgetAPIException as e:
            # order already gone (filled/cancelled) → treat as success
            if self._is_not_found(e):
                logger.debug("bitget.order_already_gone", order_id=order_id)
                self._plan_orders.discard(order_id)
            else:
                raise

    async def get_position(self, symbol: str) -> Optional[Position]:
        data = await self._request(
            "GET", "/api/v2/mix/position/single-position",
            params={"symbol": symbol, "productType": self._product_type,
                    "marginCoin": self._margin_coin},
        )
        for row in data or []:
            qty = Decimal(str(row.get("total", "0")))
            if qty == 0:
                continue
            side = Side.LONG if row.get("holdSide") == "long" else Side.SHORT
            return Position(
                position_id=row.get("holdSide", "net"),
                symbol=row.get("symbol", symbol),
                side=side,
                position_side=PositionSide.LONG if side == Side.LONG else PositionSide.SHORT,
                qty=qty,
                entry_price=Decimal(str(row.get("openPriceAvg", "0"))),
                current_price=Decimal(str(row.get("markPrice", "0"))),
                unrealized_pnl=Decimal(str(row.get("unrealizedPL", "0"))),
                leverage=int(Decimal(str(row.get("leverage", "1")))),
                sl_order_id="",
                sl_price=Decimal("0"),
                opened_at=utc_now(),
                updated_at=utc_now(),
            )
        return None

    async def get_balance(self) -> Decimal:
        data = await self._request(
            "GET", "/api/v2/mix/account/accounts",
            params={"productType": self._product_type},
        )
        for acct in data or []:
            if acct.get("marginCoin") == self._margin_coin:
                return Decimal(str(acct.get("available", "0")))
        return Decimal("0")

    async def close_all_positions(self, symbol: str) -> list[Fill]:
        pos = await self.get_position(symbol)
        if pos is None or pos.qty <= 0:
            return []
        close_side = OrderSide.SELL if pos.side == Side.LONG else OrderSide.BUY
        pos_side = PositionSide.LONG if pos.side == Side.LONG else PositionSide.SHORT
        try:
            order = await self.place_order(
                symbol=symbol, side=close_side, position_side=pos_side,
                order_type=OrderType.MARKET, qty=pos.qty, reduce_only=True,
            )
            logger.info("bitget.position_closed", symbol=symbol, qty=str(pos.qty))
            return [Fill(
                order_id=order.id,
                symbol=symbol,
                side=pos.side,
                position_side=pos_side,
                qty=pos.qty,
                avg_price=pos.current_price,
                commission=Decimal("0"),
                commission_asset=self._margin_coin,
                ts=utc_now(),
                is_entry=False,
            )]
        except Exception as exc:
            logger.error("bitget.close_position_failed", error=str(exc))
            return []

    @property
    def is_paper(self) -> bool:
        return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _set_position_mode_one_way(self) -> None:
        try:
            await self._request(
                "POST", "/api/v2/mix/account/set-position-mode",
                body={"productType": self._product_type, "posMode": "one_way_mode"},
            )
            logger.info("bitget.position_mode_set", mode="one_way_mode")
        except BitgetAPIException as e:
            if self._is_no_change(e):
                logger.debug("bitget.position_mode_already_one_way")
            else:
                # non-fatal on startup; surfaced but does not abort connect()
                logger.warning("bitget.position_mode_set_failed", code=e.code, msg=e.msg)

    @staticmethod
    def _is_no_change(e: BitgetAPIException) -> bool:
        text = (e.msg or "").lower()
        return any(s in text for s in ("no change", "not change", "already", "same"))

    @staticmethod
    def _is_not_found(e: BitgetAPIException) -> bool:
        text = (e.msg or "").lower()
        return any(s in text for s in ("not exist", "not found", "does not exist", "已撤销"))
