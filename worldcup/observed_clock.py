from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable


def system_utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MonotonicUtcClock:
    """Return timezone-aware UTC observations that never move backwards in one run."""

    def __init__(self, source: Callable[[], Any] | None = None) -> None:
        self._source = source or system_utc_now
        self._last: datetime | None = None

    def now(self) -> datetime:
        try:
            value = self._source()
            if isinstance(value, datetime):
                observed = value
            elif isinstance(value, str):
                observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            else:
                raise ValueError
            if observed.tzinfo is None or observed.utcoffset() is None:
                raise ValueError
            observed = observed.astimezone(timezone.utc)
        except (TypeError, ValueError):
            raise ValueError("observed_clock_invalid") from None
        if self._last is not None and observed < self._last:
            observed = self._last
        self._last = observed
        return observed
