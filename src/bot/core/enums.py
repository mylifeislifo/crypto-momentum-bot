from enum import Enum


class Side(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_MARKET = "STOP_MARKET"
    TAKE_PROFIT_MARKET = "TAKE_PROFIT_MARKET"
    TRAILING_STOP_MARKET = "TRAILING_STOP_MARKET"


class TimeInForce(str, Enum):
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"
    GTX = "GTX"  # post-only


class OrderStatus(str, Enum):
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class Mode(str, Enum):
    PAPER = "paper"
    LIVE = "live"


class MarginType(str, Enum):
    ISOLATED = "ISOLATED"
    CROSSED = "CROSSED"


class PositionSide(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    BOTH = "BOTH"


class SentimentLabel(str, Enum):
    EXTREME_FEAR = "extreme_fear"   # 0-24
    FEAR = "fear"                   # 25-49
    NEUTRAL = "neutral"             # 50-54
    GREED = "greed"                 # 55-74
    EXTREME_GREED = "extreme_greed" # 75-100


class Interval(str, Enum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"


class NotifyEventType(str, Enum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"
    STOP_HIT = "STOP_HIT"
    TRAIL_UPDATE = "TRAIL_UPDATE"
    CIRCUIT_BREAKER = "CIRCUIT_BREAKER"
    ERROR = "ERROR"
    INFO = "INFO"
