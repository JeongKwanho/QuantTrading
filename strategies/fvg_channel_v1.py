"""
Fair Value Gap channel strategy - V1.

Long setup:
  1st entry: bullish FVG upper bound, 25%
  2nd entry: FVG midpoint, 25%
  3rd entry: FVG lower bound, 50%

Short setup:
  1st entry: bearish FVG lower bound, 25%
  2nd entry: FVG midpoint, 25%
  3rd entry: FVG upper bound, 50%
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from patterns.fair_value_gap import FairValueGap, FairValueGapPattern
from strategies.base import BaseStrategy, FillEvent, MarketData, Signal


PositionSide = Literal["LONG", "SHORT"]


@dataclass
class _FVGSetup:
    side: PositionSide
    lower: float
    upper: float
    mid: float
    detected_ts: datetime
    sl_price: float
    tp1_price: float | None
    tp2_price: float
    prior_highs: list[float]
    prior_lows: list[float]
    bars_waited: int = 0
    entry1_done: bool = False
    entry2_done: bool = False
    entry3_done: bool = False


class FVGChannelV1(BaseStrategy):
    name = "fvg_channel_v1"
    parameters = {
        "trend_window": 8,
        "pivot_k": 2,
        "min_gap_size": 0.0,
        "min_gap_pct": 0.001,
        "middle_range_multiplier": 1.2,
        "min_trend_candle_ratio": 0.55,
        "liquidity_lookback": 20,
        "setup_expiry_bars": 80,
    }

    def __init__(self, leverage: int = 1, **kwargs) -> None:
        super().__init__(leverage, **kwargs)
        self._pattern = FairValueGapPattern(
            trend_window=self.parameters["trend_window"],
            pivot_k=self.parameters["pivot_k"],
            min_gap_size=self.parameters["min_gap_size"],
            min_gap_pct=self.parameters["min_gap_pct"],
            middle_range_multiplier=self.parameters["middle_range_multiplier"],
            min_trend_candle_ratio=self.parameters["min_trend_candle_ratio"],
        )
        self._history: list[MarketData] = []
        self._setup: _FVGSetup | None = None
        self._reset_position()

    def _reset_position(self) -> None:
        self._position_side: PositionSide | None = None
        self._position_qty = 0.0
        self._avg_price = 0.0
        self._tp1_done = False

    def on_data(self, data: MarketData) -> list[Signal]:
        self._history.append(data)
        max_keep = max(
            self.parameters["liquidity_lookback"] + 10,
            self.parameters["trend_window"] + 10,
        )
        if len(self._history) > max_keep:
            self._history = self._history[-max_keep:]

        self._pattern.evaluate(data)

        if self._position_qty > 0.0:
            exit_sigs = self._check_exit(data)
            if exit_sigs:
                return exit_sigs
            if not self._tp1_done:
                return self._check_additional_entries(data)
            return []

        if self._setup is None and self._pattern.last_fvg is not None:
            self._on_new_fvg(self._pattern.last_fvg)

        if self._setup is None:
            return []

        self._setup.bars_waited += 1
        if self._setup.bars_waited > self.parameters["setup_expiry_bars"]:
            self._setup = None
            return []

        return self._check_first_entry(data)

    def on_fill(self, fill: FillEvent) -> None:
        if self._position_side is None:
            return

        entry_direction = "BUY" if self._position_side == "LONG" else "SELL"
        if fill.direction == entry_direction:
            total_cost = self._avg_price * self._position_qty + fill.price * fill.quantity
            self._position_qty += fill.quantity
            self._avg_price = total_cost / self._position_qty
            return

        self._position_qty = max(0.0, self._position_qty - fill.quantity)
        if self._position_qty <= 0.0:
            self._setup = None
            self._reset_position()

    def on_stop(self) -> None:
        self._pattern.reset()
        self._history.clear()
        self._setup = None
        self._reset_position()

    def _on_new_fvg(self, fvg: FairValueGap) -> None:
        if self._position_qty > 0.0:
            return

        side: PositionSide = "LONG" if fvg.direction == "bullish" else "SHORT"
        lower = fvg.lower
        upper = fvg.upper
        mid = (lower + upper) / 2
        fvg_candles = self._history[-3:]
        prior = self._history[:-3]
        lookback = self.parameters["liquidity_lookback"]
        prior_window = prior[-lookback:] if lookback > 0 else prior

        if side == "LONG":
            sl_price = min(c.low for c in fvg_candles)
        else:
            sl_price = max(c.high for c in fvg_candles)

        self._setup = _FVGSetup(
            side=side,
            lower=lower,
            upper=upper,
            mid=mid,
            detected_ts=fvg.end_timestamp,
            sl_price=sl_price,
            tp1_price=None,
            tp2_price=0.0,
            prior_highs=[c.high for c in prior_window],
            prior_lows=[c.low for c in prior_window],
        )

    def _check_first_entry(self, data: MarketData) -> list[Signal]:
        setup = self._setup
        if setup is None:
            return []
        if data.timestamp == setup.detected_ts:
            return []

        self._update_tp1_candidate(data)

        if setup.side == "LONG":
            if data.low > setup.upper:
                return []
            setup.entry1_done = True
            self._position_side = "LONG"
            setup.tp2_price = self._calc_long_tp2(setup, setup.tp1_price or data.high)
            signals = [self._entry_signal(data, "BUY", 0.25, setup.upper, "fvg_long_entry1")]
            signals.extend(self._check_additional_entries(data))
            return signals

        if data.high < setup.lower:
            return []
        setup.entry1_done = True
        self._position_side = "SHORT"
        setup.tp2_price = self._calc_short_tp2(setup, setup.tp1_price or data.low)
        signals = [self._entry_signal(data, "SELL", 0.25, setup.lower, "fvg_short_entry1")]
        signals.extend(self._check_additional_entries(data))
        return signals

    def _check_additional_entries(self, data: MarketData) -> list[Signal]:
        setup = self._setup
        if setup is None:
            return []

        signals: list[Signal] = []
        if setup.side == "LONG":
            if setup.entry1_done and not setup.entry2_done and data.low <= setup.mid:
                setup.entry2_done = True
                signals.append(self._entry_signal(data, "BUY", 0.25, setup.mid, "fvg_long_entry2"))
            if setup.entry2_done and not setup.entry3_done and data.low <= setup.lower:
                setup.entry3_done = True
                signals.append(self._entry_signal(data, "BUY", 0.50, setup.lower, "fvg_long_entry3"))
            return signals

        if setup.entry1_done and not setup.entry2_done and data.high >= setup.mid:
            setup.entry2_done = True
            signals.append(self._entry_signal(data, "SELL", 0.25, setup.mid, "fvg_short_entry2"))
        if setup.entry2_done and not setup.entry3_done and data.high >= setup.upper:
            setup.entry3_done = True
            signals.append(self._entry_signal(data, "SELL", 0.50, setup.upper, "fvg_short_entry3"))
        return signals

    def _check_exit(self, data: MarketData) -> list[Signal]:
        setup = self._setup
        if setup is None or self._position_qty <= 0.0:
            return []

        if setup.side == "LONG":
            return self._check_long_exit(data, setup)
        return self._check_short_exit(data, setup)

    def _check_long_exit(self, data: MarketData, setup: _FVGSetup) -> list[Signal]:
        if not self._tp1_done:
            if data.low <= setup.sl_price:
                return [self._exit_signal(data, "SELL", self._position_qty, setup.sl_price, "stop_loss")]
            if setup.tp1_price is not None and data.high >= setup.tp1_price:
                self._tp1_done = True
                return [self._exit_signal(data, "SELL", self._position_qty * 0.5, setup.tp1_price, "tp1")]
            return []

        if setup.tp2_price > 0.0 and data.high >= setup.tp2_price:
            return [self._exit_signal(data, "SELL", self._position_qty, setup.tp2_price, "tp2")]
        if data.low <= self._avg_price:
            return [self._exit_signal(data, "SELL", self._position_qty, self._avg_price, "sl2")]
        return []

    def _check_short_exit(self, data: MarketData, setup: _FVGSetup) -> list[Signal]:
        if not self._tp1_done:
            if data.high >= setup.sl_price:
                return [self._exit_signal(data, "BUY", self._position_qty, setup.sl_price, "stop_loss")]
            if setup.tp1_price is not None and data.low <= setup.tp1_price:
                self._tp1_done = True
                return [self._exit_signal(data, "BUY", self._position_qty * 0.5, setup.tp1_price, "tp1")]
            return []

        if setup.tp2_price > 0.0 and data.low <= setup.tp2_price:
            return [self._exit_signal(data, "BUY", self._position_qty, setup.tp2_price, "tp2")]
        if data.high >= self._avg_price:
            return [self._exit_signal(data, "BUY", self._position_qty, self._avg_price, "sl2")]
        return []

    def _update_tp1_candidate(self, data: MarketData) -> None:
        setup = self._setup
        if setup is None or setup.entry1_done:
            return
        if setup.side == "LONG":
            setup.tp1_price = data.high if setup.tp1_price is None else max(setup.tp1_price, data.high)
        else:
            setup.tp1_price = data.low if setup.tp1_price is None else min(setup.tp1_price, data.low)

    def _calc_long_tp2(self, setup: _FVGSetup, tp1_price: float) -> float:
        highs = [price for price in setup.prior_highs if price > tp1_price]
        return max(highs) if highs else 0.0

    def _calc_short_tp2(self, setup: _FVGSetup, tp1_price: float) -> float:
        lows = [price for price in setup.prior_lows if price < tp1_price]
        return min(lows) if lows else 0.0

    def _entry_signal(
        self,
        data: MarketData,
        direction: Literal["BUY", "SELL"],
        fraction: float,
        entry_price: float,
        reason: str,
    ) -> Signal:
        return Signal(
            symbol=data.symbol,
            direction=direction,
            quantity=0.0,
            price=None,
            strategy_name=self.name,
            timestamp=data.timestamp,
            metadata={
                "reason": reason,
                "fraction": fraction,
                "entry_price": entry_price,
            },
        )

    def _exit_signal(
        self,
        data: MarketData,
        direction: Literal["BUY", "SELL"],
        quantity: float,
        exit_price: float,
        reason: str,
    ) -> Signal:
        return Signal(
            symbol=data.symbol,
            direction=direction,
            quantity=quantity,
            price=None,
            strategy_name=self.name,
            timestamp=data.timestamp,
            metadata={
                "reason": reason,
                "exit_price": exit_price,
                f"{reason}_price": exit_price,
            },
        )
