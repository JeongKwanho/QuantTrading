"""
Fair Value Gap pattern detection.

This detector is observation-only. It finds bullish FVGs that break above a
downtrend line while preserving the prior liquidity low.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from patterns.base import BasePattern, PatternResult
from strategies.base import MarketData


FVGDirection = Literal["bullish", "bearish"]
TrendDirection = Literal["up", "down"]


@dataclass
class FVGStructure:
    trend_h1_idx: int
    trend_h1_price: float
    trend_h1_timestamp: datetime
    trend_h2_idx: int
    trend_h2_price: float
    trend_h2_timestamp: datetime
    trend_slope: float
    liquidity_idx: int
    liquidity_price: float
    liquidity_timestamp: datetime


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
    structure: FVGStructure | None = None
    filled_timestamp: datetime | None = None

    @property
    def filled(self) -> bool:
        return self.filled_timestamp is not None


class FairValueGapPattern(BasePattern):
    """
    Three-candle bullish FVG detector with a downtrend-break filter.

    bullish FVG: candle_1.high < candle_3.low

    The middle candle must be longer than both side candles, measured by
    full candle range: high - low.

    Structure filter:
    1. Connect two pivot highs. The line slope must be below zero.
    2. Find the lowest pivot low under that line and draw a horizontal
       liquidity line.
    3. From that low until the FVG candle, price must not break below the
       liquidity line, and the FVG candle must close above the downtrend line.
    """

    def __init__(
        self,
        trend_window: int = 60,
        pivot_k: int = 2,
        min_gap_pct: float = 0.0,
        **kwargs,
    ) -> None:
        self.trend_window = trend_window
        self.pivot_k = pivot_k
        self.min_gap_pct = min_gap_pct
        self._history: list[MarketData] = []
        self.fvgs: list[FairValueGap] = []
        self.last_fvg: FairValueGap | None = None

    @property
    def name(self) -> str:
        return "fair_value_gap"

    def evaluate(self, data: MarketData) -> PatternResult:
        self._history.append(data)
        max_keep = max(self.trend_window * 3, self.pivot_k * 4 + 10)
        if len(self._history) > max_keep:
            self._history = self._history[-max_keep:]

        self._update_fills(data)
        self.last_fvg = None

        if len(self._history) < max(self.pivot_k * 2 + 3, 3):
            return self._no_signal()

        c1 = self._history[-3]
        c2 = self._history[-2]
        c3 = self._history[-1]

        if c1.high >= c3.low:
            return self._no_signal()
        if not self._middle_candle_is_largest(c1, c2, c3):
            return self._no_signal()

        structure = self._find_bullish_break_structure(fvg_idx=len(self._history) - 1)
        if structure is None:
            return self._no_signal()

        lower = c1.high
        upper = c3.low
        fvg = self._make_fvg("bullish", "down", lower, upper, c1, c2, c3, structure)

        if fvg.gap_pct < self.min_gap_pct:
            return self._no_signal()

        self.fvgs.append(fvg)
        self.last_fvg = fvg

        return PatternResult(
            detected=True,
            direction="BUY",
            strength=0.0,
            name=self.name,
        )

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
        structure: FVGStructure | None = None,
    ) -> FairValueGap:
        mid = (lower + upper) / 2
        return FairValueGap(
            direction=direction,
            trend=trend,
            lower=lower,
            upper=upper,
            start_timestamp=c1.timestamp,
            middle_timestamp=c2.timestamp,
            end_timestamp=c3.timestamp,
            gap_size=upper - lower,
            gap_pct=(upper - lower) / mid if mid > 0 else 0.0,
            structure=structure,
        )

    def _update_fills(self, data: MarketData) -> None:
        for fvg in self.fvgs:
            if fvg.filled:
                continue
            if fvg.direction == "bullish" and data.low <= fvg.lower:
                fvg.filled_timestamp = data.timestamp

    def _middle_candle_is_largest(
        self,
        c1: MarketData,
        c2: MarketData,
        c3: MarketData,
    ) -> bool:
        c1_range = c1.high - c1.low
        c2_range = c2.high - c2.low
        c3_range = c3.high - c3.low
        return c1_range < c2_range and c3_range < c2_range

    def _find_bullish_break_structure(self, fvg_idx: int) -> FVGStructure | None:
        pivot_highs = [idx for idx in self._find_pivots(is_low=False) if idx < fvg_idx]
        pivot_lows = [idx for idx in self._find_pivots(is_low=True) if idx < fvg_idx]
        if len(pivot_highs) < 2 or not pivot_lows:
            return None

        for h1_idx, h2_idx in self._downtrend_high_pairs(pivot_highs):
            h1_price = self._history[h1_idx].high
            h2_price = self._history[h2_idx].high
            slope = (h2_price - h1_price) / (h2_idx - h1_idx)
            if slope >= 0:
                continue

            trend_at_fvg = self._line_price(h1_idx, h1_price, slope, fvg_idx)
            if self._history[fvg_idx].close <= trend_at_fvg:
                continue

            low_candidates = [
                idx for idx in pivot_lows
                if h1_idx < idx < fvg_idx
                and self._history[idx].low < self._line_price(h1_idx, h1_price, slope, idx)
            ]
            if not low_candidates:
                continue

            liquidity_idx = min(low_candidates, key=lambda idx: self._history[idx].low)
            liquidity_price = self._history[liquidity_idx].low

            if self._breaks_liquidity(liquidity_idx, fvg_idx, liquidity_price):
                continue

            return FVGStructure(
                trend_h1_idx=h1_idx,
                trend_h1_price=h1_price,
                trend_h1_timestamp=self._history[h1_idx].timestamp,
                trend_h2_idx=h2_idx,
                trend_h2_price=h2_price,
                trend_h2_timestamp=self._history[h2_idx].timestamp,
                trend_slope=slope,
                liquidity_idx=liquidity_idx,
                liquidity_price=liquidity_price,
                liquidity_timestamp=self._history[liquidity_idx].timestamp,
            )

        return None

    def _downtrend_high_pairs(self, pivot_highs: list[int]) -> list[tuple[int, int]]:
        pairs: list[tuple[int, int]] = []
        highs = sorted(pivot_highs)
        for i in range(len(highs) - 1):
            for j in range(i + 1, len(highs)):
                pairs.append((highs[i], highs[j]))
        return sorted(pairs, key=lambda pair: pair[1], reverse=True)

    def _line_price(self, start_idx: int, start_price: float, slope: float, idx: int) -> float:
        return start_price + slope * (idx - start_idx)

    def _breaks_liquidity(self, liquidity_idx: int, fvg_idx: int, liquidity_price: float) -> bool:
        for idx in range(liquidity_idx + 1, fvg_idx):
            if self._history[idx].low < liquidity_price:
                return True
        return False

    def _find_pivots(self, is_low: bool) -> list[int]:
        n = len(self._history)
        win_start = max(0, n - self.trend_window)
        k = self.pivot_k
        pivots: list[int] = []

        for i in range(win_start, n - k):
            lo = max(0, i - k)
            hi = min(n - 1, i + k)
            ref = self._history[i].low if is_low else self._history[i].high

            is_pivot = True
            for j in range(lo, hi + 1):
                if j == i:
                    continue
                val = self._history[j].low if is_low else self._history[j].high
                if is_low and val <= ref:
                    is_pivot = False
                    break
                if not is_low and val >= ref:
                    is_pivot = False
                    break

            if is_pivot:
                pivots.append(i)

        return pivots

    def _no_signal(self) -> PatternResult:
        return PatternResult(detected=False, direction=None, strength=0.0, name=self.name)
