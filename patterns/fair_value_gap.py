"""
Fair Value Gap pattern detection.

This detector is observation-only. It detects simple three-candle FVGs with
an immediate prior trend context and a relatively large middle candle.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from patterns.base import BasePattern, PatternResult
from strategies.base import MarketData


FVGDirection = Literal["bullish", "bearish"]
TrendDirection = Literal["up", "down"]


@dataclass
class FairValueGap:
    direction: FVGDirection
    trend: TrendDirection
    lower: float
    upper: float
    start_timestamp: datetime
    middle_timestamp: datetime
    end_timestamp: datetime
    gap_size: float
    gap_pct: float
    middle_range: float
    side_range_max: float
    filled_timestamp: datetime | None = None

    @property
    def filled(self) -> bool:
        return self.filled_timestamp is not None


class FairValueGapPattern(BasePattern):
    """
    Three-candle FVG detector.

    Bullish FVG:
      - price was falling before the three-candle pattern,
      - candle_1.high < candle_3.low,
      - middle candle range is larger than both side candles.

    Bearish FVG:
      - price was rising before the three-candle pattern,
      - candle_1.low > candle_3.high,
      - middle candle range is larger than both side candles.
    """

    def __init__(
        self,
        trend_window: int = 8,
        pivot_k: int = 2,
        min_gap_size: float = 0.0,
        min_gap_pct: float = 0.0,
        middle_range_multiplier: float = 1.2,
        min_trend_candle_ratio: float = 0.55,
        **kwargs,
    ) -> None:
        self.trend_window = trend_window
        self.pivot_k = pivot_k
        self.min_gap_size = min_gap_size
        self.min_gap_pct = min_gap_pct
        self.middle_range_multiplier = middle_range_multiplier
        self.min_trend_candle_ratio = min_trend_candle_ratio
        self._history: list[MarketData] = []
        self.fvgs: list[FairValueGap] = []
        self.last_fvg: FairValueGap | None = None

    @property
    def name(self) -> str:
        return "fair_value_gap"

    def evaluate(self, data: MarketData) -> PatternResult:
        self._history.append(data)
        max_keep = max(self.trend_window + 3, 20)
        if len(self._history) > max_keep:
            self._history = self._history[-max_keep:]

        self._update_fills(data)
        self.last_fvg = None

        if len(self._history) < self.trend_window + 3:
            return self._no_signal()

        c1 = self._history[-3]
        c2 = self._history[-2]
        c3 = self._history[-1]

        if not self._middle_candle_is_large_enough(c1, c2, c3):
            return self._no_signal()

        prior = self._history[-self.trend_window - 3:-3]
        if c1.high < c3.low and self._prior_trend_is(prior, "down"):
            fvg = self._make_fvg("bullish", "down", c1.high, c3.low, c1, c2, c3, prior)
            if self._gap_size_ok(fvg):
                self.fvgs.append(fvg)
                self.last_fvg = fvg
                return PatternResult(
                    detected=True,
                    direction="BUY",
                    strength=self._calc_strength(fvg),
                    name=self.name,
                )

        if c1.low > c3.high and self._prior_trend_is(prior, "up"):
            fvg = self._make_fvg("bearish", "up", c3.high, c1.low, c1, c2, c3, prior)
            if self._gap_size_ok(fvg):
                self.fvgs.append(fvg)
                self.last_fvg = fvg
                return PatternResult(
                    detected=True,
                    direction="SELL",
                    strength=self._calc_strength(fvg),
                    name=self.name,
                )

        return self._no_signal()

    def reset(self) -> None:
        self._history.clear()
        self.fvgs.clear()
        self.last_fvg = None

    def _make_fvg(
        self,
        direction: FVGDirection,
        trend: TrendDirection,
        lower: float,
        upper: float,
        c1: MarketData,
        c2: MarketData,
        c3: MarketData,
        prior: list[MarketData],
    ) -> FairValueGap:
        middle_range = self._range(c2)
        side_range_max = max(self._range(c1), self._range(c3))
        mid = (lower + upper) / 2
        gap_size = upper - lower
        return FairValueGap(
            direction=direction,
            trend=trend,
            lower=lower,
            upper=upper,
            start_timestamp=c1.timestamp,
            middle_timestamp=c2.timestamp,
            end_timestamp=c3.timestamp,
            gap_size=gap_size,
            gap_pct=gap_size / mid if mid > 0 else 0.0,
            middle_range=middle_range,
            side_range_max=side_range_max,
        )

    def _update_fills(self, data: MarketData) -> None:
        for fvg in self.fvgs:
            if fvg.filled:
                continue
            if fvg.direction == "bullish" and data.low <= fvg.lower:
                fvg.filled_timestamp = data.timestamp
            elif fvg.direction == "bearish" and data.high >= fvg.upper:
                fvg.filled_timestamp = data.timestamp

    def _middle_candle_is_large_enough(
        self,
        c1: MarketData,
        c2: MarketData,
        c3: MarketData,
    ) -> bool:
        side_range_max = max(self._range(c1), self._range(c3))
        if side_range_max <= 0.0:
            return False
        return self._range(c2) >= side_range_max * self.middle_range_multiplier

    def _prior_trend_is(self, candles: list[MarketData], direction: TrendDirection) -> bool:
        if len(candles) < 2:
            return False

        first = candles[0].close
        last = candles[-1].close
        if direction == "down" and last >= first:
            return False
        if direction == "up" and last <= first:
            return False

        matching_moves = 0
        total_moves = len(candles) - 1
        for idx in range(1, len(candles)):
            previous = candles[idx - 1].close
            current = candles[idx].close
            if direction == "down" and current < previous:
                matching_moves += 1
            elif direction == "up" and current > previous:
                matching_moves += 1

        return matching_moves / total_moves >= self.min_trend_candle_ratio

    def _calc_strength(self, fvg: FairValueGap) -> float:
        if fvg.side_range_max <= 0.0:
            return 0.0
        range_score = min(1.0, fvg.middle_range / (fvg.side_range_max * 2.0))
        gap_score = min(1.0, fvg.gap_pct / 0.01)
        return round((range_score + gap_score) / 2.0, 4)

    def _gap_size_ok(self, fvg: FairValueGap) -> bool:
        return fvg.gap_size >= self.min_gap_size and fvg.gap_pct >= self.min_gap_pct

    def _range(self, candle: MarketData) -> float:
        return candle.high - candle.low

    def _no_signal(self) -> PatternResult:
        return PatternResult(detected=False, direction=None, strength=0.0, name=self.name)
