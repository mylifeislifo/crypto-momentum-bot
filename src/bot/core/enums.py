from enum import Enum


class Mode(str, Enum):
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


class TimeInForce(str, Enum):
    GTC = "gtc"
    IOC = "ioc"
    FOK = "fok"


class OrderStatus(str, Enum):
    PENDING = "pending"
    OPEN = "open"
    FILLED = "filled"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class Regime(str, Enum):
    RISK_ON = "risk_on"
    RISK_OFF = "risk_off"


class Interval(str, Enum):
    M1 = "minute1"
    M5 = "minute5"
    M15 = "minute15"
    M60 = "minute60"
    M240 = "minute240"
    D1 = "day"
