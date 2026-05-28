"""
Bullish Order Block Strategy - V1.

Entry:
  1st: low <= ob_open   -> buy 25% after RR check
  2nd: low <= ob_mid    -> buy 25% after 1st fill
  3rd: low <= ob_close  -> buy 50% after 2nd fill

Exit:
  SL  : before TP1, low <= ob_low -> sell 100% at ob_low
  TP1 : high >= tp1_price -> sell 50% at tp1_price
  TP2 : after TP1, high >= previous OB swing high -> sell remaining position
  SL2 : after TP1, low <= avg_price -> sell remaining position at avg_price
"""

from datetime import datetime

from detection.order_block import OrderBlock, OrderBlockPattern
from strategies.base import BaseStrategy, FillEvent, MarketData, Signal


class OBChannelV1(BaseStrategy):
    name = "ob_channel_v1"
    parameters = {
        "window": 10,
        "pivot_k": 2,
        "trend_window": 30,
        "min_rr": 2.0,
        "tp2_lookback": 7,
    }

    def __init__(self, leverage: int = 1, **kwargs) -> None:
        super().__init__(leverage, **kwargs)
        self._pattern = OrderBlockPattern(
            window=self.parameters["window"],
            pivot_k=self.parameters["pivot_k"],
            trend_window=self.parameters["trend_window"],
        )
        self._skipped_ob_ts: datetime | None = None
        self._reset_all()

    def _reset_all(self) -> None:
        self._reset_position()
        self._reset_ob()

    def _reset_position(self) -> None:
        self._in_position: bool = False
        self._position_qty: float = 0.0
        self._avg_price: float = 0.0
        self._tp1_done: bool = False

    def _reset_ob(self) -> None:
        self._ob_ts: datetime | None = None
        self._ob_detected_ts: datetime | None = None
        self._ob_open: float = 0.0
        self._ob_close: float = 0.0
        self._ob_mid: float = 0.0
        self._ob_low: float = 0.0
        self._tp1_price: float = 0.0
        self._tp2_price: float = 0.0
        self._max_high_since_ob: float = 0.0
        self._entry1_done: bool = False
        self._entry2_done: bool = False
        self._entry3_done: bool = False

    def on_data(self, data: MarketData) -> list[Signal]:
        self._pattern.evaluate(data)
        ob = self._pattern.bullish_ob

        if ob is not None and ob.valid:
            if self._ob_ts != ob.timestamp:
                if not self._in_position and ob.timestamp != self._skipped_ob_ts:
                    self._on_new_ob(ob, data)
        elif not self._in_position:
            self._reset_ob()

        if self._in_position:
            exit_sigs = self._check_exit(data)
            if exit_sigs:
                return exit_sigs
            if not self._tp1_done:
                return self._check_additional_entries(data)
            return []

        if self._ob_ts is not None:
            sigs = self._check_first_entry(data)
            if sigs:
                sigs += self._check_additional_entries(data)
            return sigs

        return []

    def on_fill(self, fill: FillEvent) -> None:
        if fill.direction == "BUY":
            total_cost = self._avg_price * self._position_qty + fill.price * fill.quantity
            self._position_qty += fill.quantity
            self._avg_price = total_cost / self._position_qty
        elif fill.direction == "SELL":
            self._position_qty = max(0.0, self._position_qty - fill.quantity)
            if self._position_qty <= 0.0:
                self._avg_price = 0.0
                self._reset_all()

    def on_stop(self) -> None:
        self._pattern.reset()
        self._skipped_ob_ts = None
        self._reset_all()

    def _on_new_ob(self, ob: OrderBlock, data: MarketData) -> None:
        self._reset_ob()
        self._ob_ts = ob.timestamp
        self._ob_detected_ts = data.timestamp
        self._ob_open = ob.ob_open
        self._ob_close = ob.ob_close
        self._ob_mid = (ob.ob_open + ob.ob_close) / 2
        self._ob_low = ob.ob_low
        self._tp2_price = self._calc_tp2(ob)
        self._max_high_since_ob = data.high

    def _calc_tp2(self, ob: OrderBlock) -> float:
        """Use the previous pivot high before the OB as the liquidity target."""
        hist = self._pattern._history
        ob_idx = next((i for i, h in enumerate(hist) if h.timestamp == ob.timestamp), None)
        if ob_idx is None or ob_idx < 1:
            return 0.0

        pivot_highs = [
            i for i in range(ob_idx)
            if self._is_pivot_high(hist, i)
        ]
        if pivot_highs:
            return hist[pivot_highs[-1]].high

        lb = self.parameters["tp2_lookback"]
        start = max(0, ob_idx - lb)
        return max(h.high for h in hist[start:ob_idx])

    def _calc_tp2_above(self, min_price: float) -> float:
        """Find the nearest previous OB pivot high that is still above TP1."""
        hist = self._pattern._history
        ob_idx = next((i for i, h in enumerate(hist) if h.timestamp == self._ob_ts), None)
        if ob_idx is None or ob_idx < 1:
            return 0.0

        pivot_highs = [
            i for i in range(ob_idx)
            if self._is_pivot_high(hist, i) and hist[i].high > min_price
        ]
        if pivot_highs:
            return hist[pivot_highs[-1]].high

        lb = self.parameters["tp2_lookback"]
        start = max(0, ob_idx - lb)
        highs = [h.high for h in hist[start:ob_idx] if h.high > min_price]
        return max(highs) if highs else 0.0

    def _is_pivot_high(self, hist: list[MarketData], idx: int) -> bool:
        k = self.parameters["pivot_k"]
        lo = max(0, idx - k)
        hi = min(len(hist) - 1, idx + k)
        ref = hist[idx].high
        for j in range(lo, hi + 1):
            if j == idx:
                continue
            if hist[j].high >= ref:
                return False
        return True

    def _check_first_entry(self, data: MarketData) -> list[Signal]:
        self._max_high_since_ob = max(self._max_high_since_ob, data.high)

        if data.timestamp == self._ob_detected_ts:
            return []
        if data.low > self._ob_open:
            return []

        tp1 = self._max_high_since_ob
        sl_dist = self._ob_open - self._ob_low
        rr_dist = tp1 - self._ob_open

        if sl_dist <= 0 or rr_dist / sl_dist < self.parameters["min_rr"]:
            self._skipped_ob_ts = self._ob_ts
            self._reset_ob()
            return []

        self._tp1_price = tp1
        if 0.0 < self._tp2_price <= self._tp1_price:
            self._tp2_price = self._calc_tp2_above(self._tp1_price)
        self._entry1_done = True
        self._in_position = True

        return [Signal(
            symbol=data.symbol,
            direction="BUY",
            quantity=0.0,
            price=None,
            strategy_name=self.name,
            timestamp=data.timestamp,
            metadata={
                "reason": "ob_entry1",
                "fraction": 0.25,
                "entry_price": self._ob_open,
                "sl": self._ob_low,
                "tp1": self._tp1_price,
                "tp2": self._tp2_price,
            },
        )]

    def _check_additional_entries(self, data: MarketData) -> list[Signal]:
        signals: list[Signal] = []

        if self._entry1_done and not self._entry2_done and data.low <= self._ob_mid:
            self._entry2_done = True
            signals.append(Signal(
                symbol=data.symbol,
                direction="BUY",
                quantity=0.0,
                price=None,
                strategy_name=self.name,
                timestamp=data.timestamp,
                metadata={"reason": "ob_entry2", "fraction": 0.25, "entry_price": self._ob_mid},
            ))

        if self._entry2_done and not self._entry3_done and data.low <= self._ob_close:
            self._entry3_done = True
            signals.append(Signal(
                symbol=data.symbol,
                direction="BUY",
                quantity=0.0,
                price=None,
                strategy_name=self.name,
                timestamp=data.timestamp,
                metadata={"reason": "ob_entry3", "fraction": 0.50, "entry_price": self._ob_close},
            ))

        return signals

    def _check_exit(self, data: MarketData) -> list[Signal]:
        if self._position_qty <= 0.0:
            return []

        if not self._tp1_done:
            if data.low <= self._ob_low:
                return [Signal(
                    symbol=data.symbol,
                    direction="SELL",
                    quantity=self._position_qty,
                    price=None,
                    strategy_name=self.name,
                    timestamp=data.timestamp,
                    metadata={"reason": "stop_loss", "sl_price": self._ob_low},
                )]

            if data.high >= self._tp1_price:
                self._tp1_done = True
                return [Signal(
                    symbol=data.symbol,
                    direction="SELL",
                    quantity=self._position_qty * 0.5,
                    price=None,
                    strategy_name=self.name,
                    timestamp=data.timestamp,
                    metadata={
                        "reason": "tp1",
                        "tp1_price": self._tp1_price,
                        "new_sl": self._avg_price,
                    },
                )]

        else:
            if self._tp2_price > 0.0 and data.high >= self._tp2_price:
                return [Signal(
                    symbol=data.symbol,
                    direction="SELL",
                    quantity=self._position_qty,
                    price=None,
                    strategy_name=self.name,
                    timestamp=data.timestamp,
                    metadata={"reason": "tp2", "tp2_price": self._tp2_price},
                )]

            if data.low <= self._avg_price:
                return [Signal(
                    symbol=data.symbol,
                    direction="SELL",
                    quantity=self._position_qty,
                    price=None,
                    strategy_name=self.name,
                    timestamp=data.timestamp,
                    metadata={"reason": "sl2", "sl2_price": self._avg_price},
                )]

        return []
