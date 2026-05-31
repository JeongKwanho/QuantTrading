"""Bullish and bearish order-block pattern detection."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from detection.base import BasePattern, PatternResult
from strategies.base import MarketData


OrderBlockDirection = Literal["bullish", "bearish"]


@dataclass
class OrderBlock:
    """Detected order-block body zone with wick bounds for invalidation."""

    ob_open: float
    ob_close: float
    ob_high: float
    ob_low: float
    timestamp: datetime
    direction: OrderBlockDirection = "bullish"
    valid: bool = True


class OrderBlockPattern(BasePattern):
    """Detect reversal order blocks after pivot-confirmed directional legs."""

    def __init__(
        self,
        window: int = 10,
        pivot_k: int = 2,
        trend_window: int = 30,
        min_drop_atr: float = 1.0,
        max_ob_range_pos: float = 0.65,
        **kwargs,
    ) -> None:
        self.window = window
        self.pivot_k = pivot_k
        self.trend_window = trend_window
        self.min_drop_atr = min_drop_atr
        self.max_ob_range_pos = max_ob_range_pos
        self._history: list[MarketData] = []
        self.bullish_ob: OrderBlock | None = None
        self.bearish_ob: OrderBlock | None = None
        self._invalidated_ob_ts: dict[OrderBlockDirection, datetime | None] = {
            "bullish": None,
            "bearish": None,
        }

    @property
    def name(self) -> str:
        return "order_block"

    def evaluate(self, data: MarketData) -> PatternResult:
        self._history.append(data)
        max_keep = max(self.window, self.trend_window) * 3
        if len(self._history) > max_keep:
            self._history = self._history[-max_keep:]

        if len(self._history) < self.pivot_k * 2 + 2:
            self.bullish_ob = None
            self.bearish_ob = None
            return self._no_signal()

        self._clear_invalidated()
        self._invalidate_active(data)

        pivot_highs = sorted(self._find_pivots(is_low=False, use_trend_window=True))
        pivot_lows = sorted(self._find_pivots(is_low=True, use_trend_window=True))
        if len(pivot_highs) >= 2 and len(pivot_lows) >= 2:
            last_high = self._history[pivot_highs[-1]].high
            prev_high = self._history[pivot_highs[-2]].high
            last_low = self._history[pivot_lows[-1]].low
            prev_low = self._history[pivot_lows[-2]].low
            has_lh_ll = last_high < prev_high and last_low < prev_low
            has_hh_hl = last_high > prev_high and last_low > prev_low

            if has_lh_ll:
                found = self._find_latest_ob("bullish", pivot_highs)
                if found is not None:
                    self.bullish_ob = found
                    self._invalidated_ob_ts["bullish"] = None
            if has_hh_hl:
                found = self._find_latest_ob("bearish", pivot_lows)
                if found is not None:
                    self.bearish_ob = found
                    self._invalidated_ob_ts["bearish"] = None

        bullish = self.bullish_ob is not None and self.bullish_ob.valid
        bearish = self.bearish_ob is not None and self.bearish_ob.valid
        direction = "BUY" if bullish and not bearish else "SELL" if bearish and not bullish else None
        return PatternResult(
            detected=bullish or bearish,
            direction=direction,
            strength=0.0,
            name=self.name,
        )

    def reset(self) -> None:
        self._history.clear()
        self.bullish_ob = None
        self.bearish_ob = None
        self._invalidated_ob_ts = {"bullish": None, "bearish": None}

    def _clear_invalidated(self) -> None:
        for direction, attr in (("bullish", "bullish_ob"), ("bearish", "bearish_ob")):
            ob = getattr(self, attr)
            if ob is not None and not ob.valid:
                self._invalidated_ob_ts[direction] = ob.timestamp
                setattr(self, attr, None)

    def _invalidate_active(self, data: MarketData) -> None:
        if self.bullish_ob is not None and data.low < self.bullish_ob.ob_low:
            self.bullish_ob.valid = False
            self._invalidated_ob_ts["bullish"] = self.bullish_ob.timestamp
        if self.bearish_ob is not None and data.high > self.bearish_ob.ob_high:
            self.bearish_ob.valid = False
            self._invalidated_ob_ts["bearish"] = self.bearish_ob.timestamp

    def _find_latest_ob(
        self,
        direction: OrderBlockDirection,
        leg_pivots: list[int],
    ) -> OrderBlock | None:
        n = len(self._history)
        found: OrderBlock | None = None
        for idx in range(max(0, n - self.window), n - 1):
            first = self._history[idx]
            second = self._history[idx + 1]
            first_body = first.open - first.close
            second_body = second.close - second.open

            is_bullish = first_body > 0 and second_body > first_body
            is_bearish = first_body < 0 and second_body < first_body
            if direction == "bullish" and not is_bullish:
                continue
            if direction == "bearish" and not is_bearish:
                continue
            if not self._is_valid_leg_ob(idx, leg_pivots, direction):
                continue

            candidate = OrderBlock(
                ob_open=first.open,
                ob_close=first.close,
                ob_high=first.high,
                ob_low=first.low,
                timestamp=first.timestamp,
                direction=direction,
            )
            if candidate.timestamp != self._invalidated_ob_ts[direction]:
                found = candidate
        return found

    def _find_pivots(self, is_low: bool, use_trend_window: bool = False) -> list[int]:
        n = len(self._history)
        window = self.trend_window if use_trend_window else self.window
        start = max(0, n - window)
        pivots: list[int] = []
        for idx in range(start, n - self.pivot_k):
            lo = max(0, idx - self.pivot_k)
            hi = min(n - 1, idx + self.pivot_k)
            ref = self._history[idx].low if is_low else self._history[idx].high
            values = [
                self._history[j].low if is_low else self._history[j].high
                for j in range(lo, hi + 1)
                if j != idx
            ]
            if is_low and all(value > ref for value in values):
                pivots.append(idx)
            if not is_low and all(value < ref for value in values):
                pivots.append(idx)
        return pivots

    def _is_valid_leg_ob(
        self,
        ob_idx: int,
        pivots: list[int],
        direction: OrderBlockDirection,
    ) -> bool:
        previous = [idx for idx in pivots if idx < ob_idx]
        if not previous:
            return False

        pivot_idx = previous[-1]
        pivot_price = (
            self._history[pivot_idx].high
            if direction == "bullish"
            else self._history[pivot_idx].low
        )
        leg = self._history[pivot_idx:ob_idx + 1]
        atr = self._calc_atr(ob_idx, min(14, self.trend_window))
        if not leg or atr <= 0.0:
            return False

        if direction == "bullish":
            leg_edge = min(candle.low for candle in leg)
            distance = pivot_price - leg_edge
            range_pos = (self._history[ob_idx].close - leg_edge) / distance if distance > 0 else 1.0
        else:
            leg_edge = max(candle.high for candle in leg)
            distance = leg_edge - pivot_price
            range_pos = (leg_edge - self._history[ob_idx].close) / distance if distance > 0 else 1.0
        return distance >= atr * self.min_drop_atr and range_pos <= self.max_ob_range_pos

    def _calc_atr(self, end_idx: int, period: int = 14) -> float:
        start = max(1, end_idx - period + 1)
        trs = []
        for idx in range(start, end_idx + 1):
            cur = self._history[idx]
            prev = self._history[idx - 1]
            trs.append(max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close)))
        return sum(trs) / len(trs) if trs else 0.0

    def _no_signal(self) -> PatternResult:
        return PatternResult(detected=False, direction=None, strength=0.0, name=self.name)
