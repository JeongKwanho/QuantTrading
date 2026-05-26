"""
Bullish Order Block Pattern Detection.

Downtrend detection: last pivot high < previous pivot high (within window).
OB candle: bearish candle where the next bullish candle's body > bearish candle's body.
OB zone: bearish candle's open ~ close (body only, wicks excluded).
OB invalidation: price closes below ob_low (full candle low).
"""

from dataclasses import dataclass
from datetime import datetime

from patterns.base import BasePattern, PatternResult
from strategies.base import MarketData


@dataclass
class OrderBlock:
    """Detected bullish order block zone."""
    ob_open:   float     # OB candle open  (body top)
    ob_close:  float     # OB candle close (body bottom)
    ob_high:   float     # OB candle high  (including wick)
    ob_low:    float     # OB candle low   (including wick)
    timestamp: datetime
    valid:     bool = True   # False when price closes below ob_low


class OrderBlockPattern(BasePattern):
    """
    Bullish OB detection via pivot-based downtrend filter.

    window  : candles to scan for both pivot detection and OB search
    pivot_k : pivot confirmation half-width (same as TrendLinePattern)
    """

    def __init__(
        self,
        window: int = 10,
        pivot_k: int = 2,
        trend_window: int = 30,
        min_drop_atr: float = 1.0,
        max_ob_range_pos: float = 0.65,
        **kwargs,
    ) -> None:
        self.window       = window
        self.pivot_k      = pivot_k
        self.trend_window = trend_window
        self.min_drop_atr = min_drop_atr
        self.max_ob_range_pos = max_ob_range_pos
        self._history: list[MarketData] = []
        self.bullish_ob: OrderBlock | None = None
        self._invalidated_ob_ts: datetime | None = None

    @property
    def name(self) -> str:
        return "order_block"

    def evaluate(self, data: MarketData) -> PatternResult:
        self._history.append(data)
        max_keep = max(self.window, self.trend_window) * 3
        if len(self._history) > max_keep:
            self._history = self._history[-max_keep:]

        n = len(self._history)
        if n < self.pivot_k * 2 + 2:
            self.bullish_ob = None
            return self._no_signal()

        # 직전 봉에서 무효화된 OB 정리 (타임스탬프 기억 후 제거)
        if self.bullish_ob is not None and not self.bullish_ob.valid:
            self._invalidated_ob_ts = self.bullish_ob.timestamp
            self.bullish_ob = None

        # OB 무효화: 저점(꼬리 포함)이 ob_low 아래로 내려가면 즉시 무효화 후 리턴
        if self.bullish_ob is not None and self.bullish_ob.valid:
            if data.low < self.bullish_ob.ob_low:
                self.bullish_ob.valid = False
                return PatternResult(detected=False, direction=None, strength=0.0, name=self.name)

        # ── 하락 추세 판단: LH + LL 동시 충족 (trend_window 기준) ────────
        pivot_highs = self._find_pivots(is_low=False, use_trend_window=True)
        pivot_lows  = self._find_pivots(is_low=True,  use_trend_window=True)

        if len(pivot_highs) < 2 or len(pivot_lows) < 2:
            return self._no_signal()

        sorted_highs = sorted(pivot_highs)
        sorted_lows  = sorted(pivot_lows)

        last_high = self._history[sorted_highs[-1]].high
        prev_high = self._history[sorted_highs[-2]].high
        last_low  = self._history[sorted_lows[-1]].low
        prev_low  = self._history[sorted_lows[-2]].low

        has_lh_ll = (last_high < prev_high) and (last_low < prev_low)
        has_hh_hl = (last_high > prev_high) and (last_low > prev_low)

        if not has_lh_ll:
            return PatternResult(
                detected=self.bullish_ob is not None and self.bullish_ob.valid,
                direction=None, strength=0.0, name=self.name,
            )

        # ── OB 탐지: 음봉 + 다음 양봉 바디가 더 큰 쌍 (가장 최근) ────
        win_start = max(0, n - self.window)
        found_ob: OrderBlock | None = None

        for i in range(win_start, n - 1):
            bear = self._history[i]
            bull = self._history[i + 1]

            bear_body = bear.open - bear.close   # > 0 이면 음봉
            bull_body = bull.close - bull.open   # > 0 이면 양봉

            if bear_body > 0 and bull_body > bear_body:
                if not self._is_valid_down_leg_ob(i, sorted_highs, has_hh_hl):
                    continue
                candidate = OrderBlock(
                    ob_open   = bear.open,
                    ob_close  = bear.close,
                    ob_high   = bear.high,
                    ob_low    = bear.low,
                    timestamp = bear.timestamp,
                    valid     = True,
                )
                # 무효화된 캔들은 재사용 금지
                if candidate.timestamp != self._invalidated_ob_ts:
                    found_ob = candidate

        if found_ob is not None:
            self.bullish_ob = found_ob
            self._invalidated_ob_ts = None  # 새 OB 확정 시 블랙리스트 해제

        detected = self.bullish_ob is not None and self.bullish_ob.valid
        return PatternResult(
            detected=detected,
            direction="up" if detected else None,
            strength=0.0,
            name=self.name,
        )

    def reset(self) -> None:
        self._history.clear()
        self.bullish_ob = None
        self._invalidated_ob_ts = None

    # ── 피벗 탐색 (TrendLinePattern과 동일 로직) ─────────────────────────

    def _find_pivots(self, is_low: bool, use_trend_window: bool = False) -> list[int]:
        n         = len(self._history)
        win       = self.trend_window if use_trend_window else self.window
        win_start = max(0, n - win)
        k         = self.pivot_k
        pivots: list[int] = []

        for i in range(win_start, n - k):
            lo  = max(0,   i - k)
            hi  = min(n-1, i + k)
            ref = self._history[i].low if is_low else self._history[i].high

            is_pivot = True
            for j in range(lo, hi + 1):
                if j == i:
                    continue
                val = self._history[j].low if is_low else self._history[j].high
                if is_low and val <= ref:
                    is_pivot = False; break
                if not is_low and val >= ref:
                    is_pivot = False; break

            if is_pivot:
                pivots.append(i)

        return pivots

    def _is_valid_down_leg_ob(self, ob_idx: int, pivot_highs: list[int], has_hh_hl: bool) -> bool:
        """Confirm the OB candle sits in a meaningful drop from a previous swing high."""
        if has_hh_hl:
            return False

        prev_highs = [idx for idx in pivot_highs if idx < ob_idx]
        if not prev_highs:
            return False

        high_idx = prev_highs[-1]
        high_price = self._history[high_idx].high
        leg = self._history[high_idx:ob_idx + 1]
        if not leg:
            return False

        leg_low = min(c.low for c in leg)
        drop = high_price - leg_low
        atr = self._calc_atr(end_idx=ob_idx, period=min(14, self.trend_window))
        if atr <= 0.0 or drop < atr * self.min_drop_atr:
            return False

        ob_close = self._history[ob_idx].close
        if high_price <= leg_low:
            return False

        range_pos = (ob_close - leg_low) / (high_price - leg_low)
        return range_pos <= self.max_ob_range_pos

    def _calc_atr(self, end_idx: int, period: int = 14) -> float:
        start = max(1, end_idx - period + 1)
        trs: list[float] = []
        for i in range(start, end_idx + 1):
            cur = self._history[i]
            prev = self._history[i - 1]
            trs.append(max(
                cur.high - cur.low,
                abs(cur.high - prev.close),
                abs(cur.low - prev.close),
            ))
        return sum(trs) / len(trs) if trs else 0.0

    def _no_signal(self) -> PatternResult:
        return PatternResult(detected=False, direction=None, strength=0.0, name=self.name)
