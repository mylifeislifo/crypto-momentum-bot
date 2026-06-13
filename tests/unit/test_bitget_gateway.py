"""Bitget gateway tests — request construction, signing, parsing, routing.

HTTP is mocked with a tiny fake aiohttp session (no network, no aioresponses —
which is incompatible with the installed aiohttp). Verifies the gateway maps the
exchange-agnostic FuturesGateway contract onto Bitget v2 Mix correctly so the
winner-asymmetry order_manager can drive it unchanged.
"""

import base64
import hashlib
import hmac
import json
from decimal import Decimal

import pytest

from bot.config.schema import AppConfig, ExchangeCfg, Secrets
from bot.core.enums import Exchange, MarginType, OrderSide, OrderType, PositionSide, Side
from bot.execution.bitget_futures import _BASE_URL, BitgetAPIException, BitgetFuturesGateway


def _ok(data):
    return {"code": "00000", "msg": "success", "requestTime": 0, "data": data}


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self):
        return self._payload


class FakeSession:
    """Routes requests by URL substring; records every call for assertions."""

    def __init__(self):
        self.routes: list[tuple[str, dict]] = []
        self.calls: list[dict] = []

    def add(self, substr: str, payload: dict) -> "FakeSession":
        self.routes.append((substr, payload))
        return self

    def request(self, method, url, data=None, headers=None):
        self.calls.append({"method": method, "url": url, "data": data, "headers": headers})
        for substr, payload in self.routes:
            if substr in url:
                return _FakeResp(payload)
        raise AssertionError(f"no fake route for {url}")

    async def close(self):
        pass

    # helpers for assertions
    def last(self) -> dict:
        return self.calls[-1]

    def body(self, idx: int = -1) -> dict:
        return json.loads(self.calls[idx]["data"])

    def path(self, idx: int = -1) -> str:
        return self.calls[idx]["url"].removeprefix(_BASE_URL).split("?")[0]


def _gw(session: FakeSession, **kw) -> BitgetFuturesGateway:
    gw = BitgetFuturesGateway("key", "secret", "passphrase", **kw)
    gw._session = session
    return gw


class TestPlaceOrder:
    async def test_market_order_body_and_parse(self):
        s = FakeSession().add("place-order", _ok({"orderId": "123", "clientOid": "c1"}))
        order = await _gw(s).place_order(
            symbol="BTCUSDT", side=OrderSide.BUY, position_side=PositionSide.LONG,
            order_type=OrderType.MARKET, qty=Decimal("0.01"),
        )
        body = s.body()
        assert body["side"] == "buy"
        assert body["orderType"] == "market"
        assert body["size"] == "0.01"
        assert body["reduceOnly"] == "NO"
        assert body["productType"] == "USDT-FUTURES"
        assert order.id == "123"
        assert order.status.value == "FILLED"

    async def test_reduce_only_and_sell_side(self):
        s = FakeSession().add("place-order", _ok({"orderId": "1"}))
        await _gw(s).place_order(
            symbol="BTCUSDT", side=OrderSide.SELL, position_side=PositionSide.LONG,
            order_type=OrderType.MARKET, qty=Decimal("0.01"), reduce_only=True,
        )
        body = s.body()
        assert body["side"] == "sell"
        assert body["reduceOnly"] == "YES"

    async def test_stop_market_routes_to_plan_endpoint(self):
        s = FakeSession().add("place-plan-order", _ok({"orderId": "plan-9", "clientOid": "sl1"}))
        gw = _gw(s)
        order = await gw.place_order(
            symbol="BTCUSDT", side=OrderSide.SELL, position_side=PositionSide.LONG,
            order_type=OrderType.STOP_MARKET, qty=Decimal("0.01"),
            stop_price=Decimal("49000"), reduce_only=True,
        )
        assert s.path().endswith("place-plan-order")
        body = s.body()
        assert body["planType"] == "normal_plan"
        assert body["orderType"] == "market"
        assert body["triggerPrice"] == "49000"
        assert order.status.value == "NEW"
        assert "plan-9" in gw._plan_orders

    async def test_stop_without_price_raises(self):
        with pytest.raises(ValueError, match="stop_price"):
            await _gw(FakeSession()).place_order(
                symbol="BTCUSDT", side=OrderSide.SELL, position_side=PositionSide.LONG,
                order_type=OrderType.STOP_MARKET, qty=Decimal("0.01"),
            )


class TestCancelRouting:
    async def test_plan_order_cancels_via_plan_endpoint(self):
        s = (FakeSession()
             .add("place-plan-order", _ok({"orderId": "plan-9"}))
             .add("cancel-plan-order", _ok({"successList": []})))
        gw = _gw(s)
        await gw.place_order(
            symbol="BTCUSDT", side=OrderSide.SELL, position_side=PositionSide.LONG,
            order_type=OrderType.STOP_MARKET, qty=Decimal("0.01"), stop_price=Decimal("49000"),
        )
        await gw.cancel_order("BTCUSDT", "plan-9")
        assert s.path().endswith("cancel-plan-order")
        assert "plan-9" not in gw._plan_orders

    async def test_regular_order_cancels_via_order_endpoint(self):
        s = FakeSession().add("cancel-order", _ok({"orderId": "5"}))
        await _gw(s).cancel_order("BTCUSDT", "5")
        assert s.path().endswith("cancel-order")

    async def test_cancel_swallows_not_found(self):
        s = FakeSession().add("cancel-order",
                              {"code": "43025", "msg": "order not exist", "data": None})
        await _gw(s).cancel_order("BTCUSDT", "gone")  # must not raise


class TestQueries:
    async def test_get_position_long(self):
        rows = [{"symbol": "BTCUSDT", "holdSide": "long", "total": "0.5",
                 "openPriceAvg": "50000", "markPrice": "50500",
                 "unrealizedPL": "250", "leverage": "2"}]
        pos = await _gw(FakeSession().add("single-position", _ok(rows))).get_position("BTCUSDT")
        assert pos is not None
        assert pos.side == Side.LONG
        assert pos.qty == Decimal("0.5")
        assert pos.entry_price == Decimal("50000")
        assert pos.leverage == 2

    async def test_get_position_flat_returns_none(self):
        rows = [{"symbol": "BTCUSDT", "holdSide": "long", "total": "0"}]
        pos = await _gw(FakeSession().add("single-position", _ok(rows))).get_position("BTCUSDT")
        assert pos is None

    async def test_get_balance_finds_usdt(self):
        accts = [{"marginCoin": "USDC", "available": "1"},
                 {"marginCoin": "USDT", "available": "1234.5"}]
        bal = await _gw(FakeSession().add("account/accounts", _ok(accts))).get_balance()
        assert bal == Decimal("1234.5")
        assert isinstance(bal, Decimal)


class TestLeverageAndErrors:
    async def test_leverage_cap_enforced(self):
        with pytest.raises(AssertionError, match="hard cap"):
            await _gw(FakeSession()).set_leverage("BTCUSDT", 3)

    async def test_leverage_within_cap_posts(self):
        s = FakeSession().add("set-leverage", _ok({}))
        await _gw(s).set_leverage("BTCUSDT", 2)
        assert s.body()["leverage"] == "2"

    async def test_margin_mode_already_set_is_swallowed(self):
        s = FakeSession().add("set-margin-mode",
                              {"code": "40109", "msg": "The margin mode is not changed", "data": None})
        await _gw(s).set_margin_mode("BTCUSDT", MarginType.ISOLATED)  # must not raise

    async def test_error_envelope_raises(self):
        s = FakeSession().add("account/accounts",
                              {"code": "40001", "msg": "bad request", "data": None})
        with pytest.raises(BitgetAPIException) as ei:
            await _gw(s).get_balance()
        assert ei.value.code == "40001"

    async def test_is_paper_false(self):
        assert _gw(FakeSession()).is_paper is False


class TestSigning:
    async def test_signature_matches_recomputation(self):
        s = FakeSession().add("place-order", _ok({"orderId": "1"}))
        await _gw(s).place_order(
            symbol="BTCUSDT", side=OrderSide.BUY, position_side=PositionSide.LONG,
            order_type=OrderType.MARKET, qty=Decimal("0.01"),
        )
        call = s.last()
        h = call["headers"]
        for key in ("ACCESS-KEY", "ACCESS-SIGN", "ACCESS-TIMESTAMP", "ACCESS-PASSPHRASE"):
            assert key in h
        request_path = call["url"].removeprefix(_BASE_URL)   # POST → no query
        prehash = h["ACCESS-TIMESTAMP"] + "POST" + request_path + call["data"]
        expected = base64.b64encode(
            hmac.new(b"secret", prehash.encode(), hashlib.sha256).digest()
        ).decode()
        assert h["ACCESS-SIGN"] == expected


class TestExchangeConfig:
    def test_default_exchange_is_binance(self):
        assert ExchangeCfg().name == Exchange.BINANCE

    def test_can_select_bitget(self):
        cfg = ExchangeCfg(name="bitget")
        assert cfg.name == Exchange.BITGET
        assert cfg.product_type == "USDT-FUTURES"
        assert cfg.margin_coin == "USDT"

    def test_secrets_have_bitget_fields(self):
        s = Secrets(_env_file=None)
        assert s.bitget_api_key == ""
        assert s.bitget_passphrase == ""

    def test_appconfig_accepts_bitget_exchange(self):
        cfg = AppConfig.model_validate({"exchange": {"name": "bitget", "symbol": "BTCUSDT"}})
        assert cfg.exchange.name == Exchange.BITGET
