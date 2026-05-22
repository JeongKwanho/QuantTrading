"""
Bullish Order Block Strategy — V1

진입 (OB 존으로 되돌아올 때):
  1차 (25%): low <= ob_open   → RR 체크 후 진입
  2차 (25%): low <= ob_mid    → 1차 체결 후
  3차 (50%): low <= ob_close  → 2차 체결 후

손절:
  SL  : low  <= ob_low        → 전량 매도 (OB 꼬리 이하)

익절:
  TP1 : high >= tp1_price     → 50% 매도
        (tp1 = OB 이후 ~ 1차 진입 봉 사이 최고 고점)
        → SL2 이동: 포지션 평균매수가
  SL2 : TP1 후 low <= avg_price  → 전량 매도
  TP2 : high >= tp2_price     → 전량 매도
        (tp2 = OB 이전 7봉 중 최고 고점)

RR 조건 (1차 진입 전 체크):
  (tp1 - ob_open) / (ob_open - ob_low) >= min_rr
  미달 시 해당 OB 전체 스킵 (1·2·3차 모두 진입 안 함)
"""

from datetime import datetime

from patterns.order_block import OrderBlock, OrderBlockPattern
from strategies.base import BaseStrategy, FillEvent, MarketData, Signal


class OBChannelV1(BaseStrategy):
    name = "ob_channel_v1"
    parameters = {
        "window":       10,
        "pivot_k":      2,
        "trend_window": 30,
        "min_rr":       2.0,
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

    # ── 초기화 ────────────────────────────────────────────────────────────

    def _reset_all(self) -> None:
        self._reset_position()
        self._reset_ob()

    def _reset_position(self) -> None:
        self._in_position:  bool  = False
        self._position_qty: float = 0.0
        self._avg_price:    float = 0.0
        self._tp1_done:     bool  = False

    def _reset_ob(self) -> None:
        self._ob_ts:              datetime | None = None
        self._ob_detected_ts:     datetime | None = None  # 엔건핑 봉 타임스탬프 (진입 금지 봉)
        self._ob_open:            float = 0.0
        self._ob_close:           float = 0.0
        self._ob_mid:             float = 0.0
        self._ob_low:             float = 0.0
        self._tp1_price:          float = 0.0
        self._tp2_price:          float = 0.0
        self._max_high_since_ob:  float = 0.0
        self._entry1_done:        bool  = False
        self._entry2_done:        bool  = False
        self._entry3_done:        bool  = False

    # ── 메인 ──────────────────────────────────────────────────────────────

    def on_data(self, data: MarketData) -> list[Signal]:
        self._pattern.evaluate(data)
        ob = self._pattern.bullish_ob

        # OB 상태 동기화
        if ob is not None and ob.valid:
            if self._ob_ts != ob.timestamp:
                if not self._in_position and ob.timestamp != self._skipped_ob_ts:
                    self._on_new_ob(ob, data)
        else:
            if not self._in_position:
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
                # 같은 봉에서 2차·3차도 터치했으면 함께 반환
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

    # ── OB 신규 감지 ──────────────────────────────────────────────────────

    def _on_new_ob(self, ob: OrderBlock, data: MarketData) -> None:
        self._reset_ob()
        self._ob_ts          = ob.timestamp
        self._ob_detected_ts = data.timestamp   # 엔건핑 봉: 이 봉에서는 진입 안 함
        self._ob_open        = ob.ob_open
        self._ob_close       = ob.ob_close
        self._ob_mid         = (ob.ob_open + ob.ob_close) / 2
        self._ob_low         = ob.ob_low
        self._tp2_price      = self._calc_tp2(ob)
        self._max_high_since_ob = data.high     # TP1 추적은 엔건핑 봉 high부터 시작

    def _calc_tp2(self, ob: OrderBlock) -> float:
        """OB 이전 tp2_lookback 봉 중 최고 고점."""
        hist = self._pattern._history
        ob_idx = next((i for i, h in enumerate(hist) if h.timestamp == ob.timestamp), None)
        if ob_idx is None or ob_idx < 1:
            return 0.0
        lb = self.parameters["tp2_lookback"]
        start = max(0, ob_idx - lb)
        return max(h.high for h in hist[start:ob_idx])

    # ── 1차 진입 + RR 체크 ────────────────────────────────────────────────

    def _check_first_entry(self, data: MarketData) -> list[Signal]:
        self._max_high_since_ob = max(self._max_high_since_ob, data.high)

        # 엔건핑 봉(OB 확인 봉)에서는 진입 불가 — 다음 봉부터 허용
        if data.timestamp == self._ob_detected_ts:
            return []

        if data.low > self._ob_open:
            return []

        tp1     = self._max_high_since_ob
        sl_dist = self._ob_open - self._ob_low
        rr_dist = tp1 - self._ob_open

        if sl_dist <= 0 or rr_dist / sl_dist < self.parameters["min_rr"]:
            self._skipped_ob_ts = self._ob_ts
            self._reset_ob()
            return []

        self._tp1_price   = tp1
        self._entry1_done = True
        self._in_position = True

        return [Signal(
            symbol        = data.symbol,
            direction     = "BUY",
            quantity      = 0.0,
            price         = None,
            strategy_name = self.name,
            timestamp     = data.timestamp,
            metadata      = {
                "reason":       "ob_entry1",
                "fraction":     0.25,
                "entry_price":  self._ob_open,
                "sl":           self._ob_low,
                "tp1":          self._tp1_price,
                "tp2":          self._tp2_price,
            },
        )]

    # ── 2차·3차 추가 진입 ────────────────────────────────────────────────

    def _check_additional_entries(self, data: MarketData) -> list[Signal]:
        signals: list[Signal] = []

        if self._entry1_done and not self._entry2_done:
            if data.low <= self._ob_mid:
                self._entry2_done = True
                signals.append(Signal(
                    symbol        = data.symbol,
                    direction     = "BUY",
                    quantity      = 0.0,
                    price         = None,
                    strategy_name = self.name,
                    timestamp     = data.timestamp,
                    metadata      = {"reason": "ob_entry2", "fraction": 0.25, "entry_price": self._ob_mid},
                ))

        if self._entry2_done and not self._entry3_done:
            if data.low <= self._ob_close:
                self._entry3_done = True
                signals.append(Signal(
                    symbol        = data.symbol,
                    direction     = "BUY",
                    quantity      = 0.0,
                    price         = None,
                    strategy_name = self.name,
                    timestamp     = data.timestamp,
                    metadata      = {"reason": "ob_entry3", "fraction": 0.50, "entry_price": self._ob_close},
                ))

        return signals

    # ── 청산 조건 ─────────────────────────────────────────────────────────

    def _check_exit(self, data: MarketData) -> list[Signal]:
        if self._position_qty <= 0.0:
            return []

        if not self._tp1_done:
            # TP1 전: SL → TP1 순서
            if data.low <= self._ob_low:
                qty = self._position_qty
                return [Signal(
                    symbol        = data.symbol,
                    direction     = "SELL",
                    quantity      = qty,
                    price         = None,
                    strategy_name = self.name,
                    timestamp     = data.timestamp,
                    metadata      = {"reason": "stop_loss", "sl_price": self._ob_low},
                )]

            if data.high >= self._tp1_price:
                sell_qty       = self._position_qty  # 전량 매도 (TP2 임시 제거)
                self._tp1_done = True
                return [Signal(
                    symbol        = data.symbol,
                    direction     = "SELL",
                    quantity      = sell_qty,
                    price         = None,
                    strategy_name = self.name,
                    timestamp     = data.timestamp,
                    metadata      = {"reason": "tp1", "tp1_price": self._tp1_price},
                )]

        else:
            # TP2 임시 비활성화 — TP1 전량 매도로 대체
            if data.low <= self._avg_price:
                qty = self._position_qty
                return [Signal(
                    symbol        = data.symbol,
                    direction     = "SELL",
                    quantity      = qty,
                    price         = None,
                    strategy_name = self.name,
                    timestamp     = data.timestamp,
                    metadata      = {"reason": "sl2", "sl2_price": self._avg_price},
                )]

        return []
