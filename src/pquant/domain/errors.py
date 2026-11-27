"""Project exceptions. Live trading is not an error that can be overridden."""


class PQuantError(Exception):
    """Base error for Personal Quant AI."""


class ConfigError(PQuantError):
    """Configuration is missing or contradicts a v1 safety rule."""


class LiveTradingDisabledError(PQuantError):
    """LIVE mode or a live-order flag was requested. v1 refuses it in code."""


class SafetyError(PQuantError):
    """A call would short, lever, withdraw, place a live order, or run arbitrary code."""


class DataError(PQuantError):
    """Stored or incoming market data cannot be used as requested."""


class DataQualityError(DataError):
    """A hard data-quality check failed."""


class AccountingError(PQuantError):
    """Cash, quantity, or fee accounting broke an invariant."""


class ExchangeError(PQuantError):
    """Public market-data request failed."""


class RateLimitError(ExchangeError):
    """HTTP 429. The caller should back off using Retry-After."""


class BanError(ExchangeError):
    """HTTP 418. Requests must stop until the ban window ends."""


class NetworkError(ExchangeError):
    """Transport failure or HTTP 5xx after the retry budget was spent."""
