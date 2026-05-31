"""Combined observation judge for recent and spatially-related evidence."""

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from detection.base import PatternResult
from detection.fair_value_gap import FairValueGap, FairValueGapPattern
from detection.order_block import OrderBlock, OrderBlockPattern
from detection.trend_line import TrendChannel, TrendLinePattern, TrendLinePatternUp
from strategies.base import MarketData


EvidenceKind = Literal["trend_line", "order_block", "fair_value_gap"]
EvidenceDirection = Literal["long", "short"]


@dataclass
class DetectionItem:
    """One detector result inside the recent lookback window."""

    name: str
    detected: bool
    direction: str | None = None
    strength: float = 0.0
    currently_detected: bool = False
    last_detected_bar: int | None = None
    bars_since_detected: int | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class Evidence:
    """A recent detector output used for spatial relationship matching."""

    evidence_id: str
    kind: EvidenceKind
    direction: EvidenceDirection
    detected_bar: int
    detected_timestamp: object
    lower: float
    upper: float
    last_touched_bar: int | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class RelationshipMatch:
    """Best matching observation setup for the current candle."""

    state: bool = False
    direction: EvidenceDirection | None = None
    score: int = 0
    evidence_ids: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    reason: str | None = None


@dataclass
class DetectionSnapshot:
    """Full detector and relationship state after evaluating one candle."""

    symbol: str
    timestamp: object
    minimum_required: int
    lookback_bars: int
    bar_index: int
    items: dict[str, DetectionItem]
    relationship: RelationshipMatch = field(default_factory=RelationshipMatch)

    @property
    def detected_count(self) -> int:
        return sum(1 for item in self.items.values() if item.detected)

    @property
    def has_minimum_detections(self) -> bool:
        return self.detected_count >= self.minimum_required

    @property
    def all_three_detected(self) -> bool:
        """Legacy rolling count retained for diagnostics."""
        return self.detected_count >= 3

    @property
    def relationship_state(self) -> bool:
        """Observation signal: a trend channel and compatible zone are related."""
        return self.relationship.state

    def detected_names(self) -> list[str]:
        return [name for name, item in self.items.items() if item.detected]


class CombinedDetectionJudge:
    """
    Evaluate detectors and relate their recent evidence without placing orders.

    A relationship signal is direction-aware:
      - long: downtrend channel + bullish OB and/or bullish FVG
      - short: uptrend channel + bearish OB and/or bearish FVG

    Evidence may arrive in any order. A relationship signal requires all three
    pieces of evidence: a channel-overlapping touched OB, a channel-overlapping
    touched FVG, and overlap between the OB and FVG zones.
    """

    parameters = {
        "minimum_required": 3,
        "lookback_bars": 15,
        "trend_window": 50,
        "trend_pivot_k": 2,
        "ob_window": 10,
        "ob_pivot_k": 2,
        "ob_trend_window": 30,
        "fvg_trend_window": 8,
        "fvg_pivot_k": 2,
        "fvg_min_gap_size": 0.0,
        "fvg_min_gap_pct": 0.001,
        "fvg_middle_range_multiplier": 1.2,
        "fvg_min_trend_candle_ratio": 0.55,
    }

    def __init__(self, **kwargs) -> None:
        self.parameters = dict(self.__class__.parameters)
        self.parameters.update(kwargs)
        self.downtrend = TrendLinePattern(
            window=self.parameters["trend_window"],
            pivot_k=self.parameters["trend_pivot_k"],
        )
        self.uptrend = TrendLinePatternUp(
            window=self.parameters["trend_window"],
            pivot_k=self.parameters["trend_pivot_k"],
        )
        self.order_block = OrderBlockPattern(
            window=self.parameters["ob_window"],
            pivot_k=self.parameters["ob_pivot_k"],
            trend_window=self.parameters["ob_trend_window"],
        )
        self.fair_value_gap = FairValueGapPattern(
            trend_window=self.parameters["fvg_trend_window"],
            pivot_k=self.parameters["fvg_pivot_k"],
            min_gap_size=self.parameters["fvg_min_gap_size"],
            min_gap_pct=self.parameters["fvg_min_gap_pct"],
            middle_range_multiplier=self.parameters["fvg_middle_range_multiplier"],
            min_trend_candle_ratio=self.parameters["fvg_min_trend_candle_ratio"],
        )
        self._bar_index = -1
        self._last_detected_bar: dict[str, int] = {}
        self._evidence: dict[str, Evidence] = {}
        self._active_trend_ids: set[str] = set()
        self._seen_zone_ids: set[str] = set()
        self._emitted_relationship_ids: set[tuple[str, ...]] = set()

    def evaluate(self, data: MarketData) -> DetectionSnapshot:
        self._bar_index += 1
        down_result = self.downtrend.evaluate(data)
        up_result = self.uptrend.evaluate(data)
        ob_result = self.order_block.evaluate(data)
        fvg_result = self.fair_value_gap.evaluate(data)
        self._active_trend_ids.clear()
        self._collect_evidence(data)
        self._update_zone_touches(data)
        self._expire_evidence()

        items = {
            "trend_line": self._trend_item(down_result, up_result),
            "order_block": self._order_block_item(ob_result),
            "fair_value_gap": self._fair_value_gap_item(fvg_result),
        }
        return DetectionSnapshot(
            symbol=data.symbol,
            timestamp=data.timestamp,
            minimum_required=self.parameters["minimum_required"],
            lookback_bars=self.parameters["lookback_bars"],
            bar_index=self._bar_index,
            items=items,
            relationship=self._find_best_relationship(),
        )

    def reset(self) -> None:
        self.downtrend.reset()
        self.uptrend.reset()
        self.order_block.reset()
        self.fair_value_gap.reset()
        self._bar_index = -1
        self._last_detected_bar.clear()
        self._evidence.clear()
        self._active_trend_ids.clear()
        self._seen_zone_ids.clear()
        self._emitted_relationship_ids.clear()

    def _collect_evidence(self, data: MarketData) -> None:
        down = self.downtrend.downtrend_channel
        up = self.uptrend.uptrend_channel
        if down is not None:
            self._remember_trend("long", down, data.timestamp)
        if up is not None:
            self._remember_trend("short", up, data.timestamp)
        for ob in (self.order_block.bullish_ob, self.order_block.bearish_ob):
            if ob is not None and ob.valid:
                direction = "long" if ob.direction == "bullish" else "short"
                self._remember_zone("order_block", direction, ob.timestamp, ob.ob_close, ob.ob_open, {
                    "order_block": self._order_block_details(ob),
                })
        fvg = self.fair_value_gap.last_fvg
        if fvg is not None:
            direction = "long" if fvg.direction == "bullish" else "short"
            self._remember_zone("fair_value_gap", direction, fvg.end_timestamp, fvg.lower, fvg.upper, {
                "fair_value_gap": self._fvg_details(fvg),
            })

    def _remember_trend(self, direction: EvidenceDirection, channel: TrendChannel, timestamp: object) -> None:
        evidence_id = (
            f"trend_line:{direction}:{channel.l1_idx}:{channel.l2_idx}:"
            f"{channel.l1_price:.12g}:{channel.l2_price:.12g}"
        )
        self._active_trend_ids.add(evidence_id)
        if evidence_id in self._evidence:
            evidence = self._evidence[evidence_id]
            evidence.detected_bar = self._bar_index
            evidence.detected_timestamp = timestamp
            evidence.lower = channel.lower_now
            evidence.upper = channel.upper_now
            evidence.details = {"channel": self._channel_details(channel), "slope": channel.slope}
            return
        self._evidence[evidence_id] = Evidence(
            evidence_id=evidence_id,
            kind="trend_line",
            direction=direction,
            detected_bar=self._bar_index,
            detected_timestamp=timestamp,
            lower=channel.lower_now,
            upper=channel.upper_now,
            details={"channel": self._channel_details(channel), "slope": channel.slope},
        )

    def _remember_zone(
        self,
        kind: Literal["order_block", "fair_value_gap"],
        direction: EvidenceDirection,
        timestamp: object,
        lower: float,
        upper: float,
        details: dict[str, Any],
    ) -> None:
        evidence_id = f"{kind}:{direction}:{timestamp}"
        if evidence_id in self._seen_zone_ids:
            return
        self._seen_zone_ids.add(evidence_id)
        self._evidence[evidence_id] = Evidence(
            evidence_id=evidence_id,
            kind=kind,
            direction=direction,
            detected_bar=self._bar_index,
            detected_timestamp=timestamp,
            lower=min(lower, upper),
            upper=max(lower, upper),
            details=details,
        )

    def _update_zone_touches(self, data: MarketData) -> None:
        for evidence in self._evidence.values():
            if evidence.kind != "trend_line" and self._ranges_overlap(data.low, data.high, evidence.lower, evidence.upper):
                evidence.last_touched_bar = self._bar_index

    def _expire_evidence(self) -> None:
        lookback = self.parameters["lookback_bars"]
        self._evidence = {
            evidence_id: evidence
            for evidence_id, evidence in self._evidence.items()
            if self._bar_index - evidence.detected_bar <= lookback
        }

    def _find_best_relationship(self) -> RelationshipMatch:
        candidates: list[RelationshipMatch] = []
        for direction in ("long", "short"):
            trends = [
                trend
                for trend in self._recent_evidence("trend_line", direction)
                if trend.evidence_id in self._active_trend_ids
            ]
            obs = self._recent_evidence("order_block", direction)
            fvgs = self._recent_evidence("fair_value_gap", direction)
            for trend in trends:
                matched_obs = [ob for ob in obs if self._zone_matches_trend(ob, trend)]
                matched_fvgs = [fvg for fvg in fvgs if self._zone_matches_trend(fvg, trend)]
                for ob in matched_obs:
                    for fvg in matched_fvgs:
                        if self._ranges_overlap(ob.lower, ob.upper, fvg.lower, fvg.upper):
                            candidates.append(self._make_match(direction, [trend, ob, fvg], "trend + overlapping OB + FVG"))
        if not candidates:
            return RelationshipMatch()
        best = max(candidates, key=self._latest_match_bar)
        relationship_id = tuple(sorted(best.evidence_ids))
        if relationship_id in self._emitted_relationship_ids:
            return RelationshipMatch()
        self._emitted_relationship_ids.add(relationship_id)
        return best

    def _recent_evidence(self, kind: EvidenceKind, direction: EvidenceDirection) -> list[Evidence]:
        return [
            evidence
            for evidence in self._evidence.values()
            if evidence.kind == kind and evidence.direction == direction
        ]

    def _zone_matches_trend(self, zone: Evidence, trend: Evidence) -> bool:
        if zone.last_touched_bar is None:
            return False
        lower, upper = self._projected_channel(trend)
        return self._ranges_overlap(zone.lower, zone.upper, lower, upper)

    def _projected_channel(self, trend: Evidence) -> tuple[float, float]:
        slope = float(trend.details.get("slope", 0.0))
        bars = self._bar_index - trend.detected_bar
        return trend.lower + slope * bars, trend.upper + slope * bars

    def _make_match(
        self,
        direction: EvidenceDirection,
        evidence: list[Evidence],
        reason: str,
    ) -> RelationshipMatch:
        return RelationshipMatch(
            state=True,
            direction=direction,
            score=len(evidence),
            evidence_ids=[item.evidence_id for item in evidence],
            evidence=[asdict(item) for item in evidence],
            reason=reason,
        )

    def _latest_match_bar(self, match: RelationshipMatch) -> int:
        return max((item["detected_bar"] for item in match.evidence), default=-1)

    @staticmethod
    def _ranges_overlap(lower_a: float, upper_a: float, lower_b: float, upper_b: float) -> bool:
        return max(lower_a, lower_b) <= min(upper_a, upper_b)

    def _trend_item(self, down_result: PatternResult, up_result: PatternResult) -> DetectionItem:
        down = self.downtrend.downtrend_channel
        up = self.uptrend.uptrend_channel
        current = down is not None or up is not None
        direction = "downtrend" if down is not None and up is None else "uptrend" if up is not None and down is None else "mixed" if current else None
        recent, last_bar, bars_since = self._recent_state("trend_line", current)
        return DetectionItem(
            name="trend_line", detected=recent, direction=direction,
            strength=max(down_result.strength, up_result.strength),
            currently_detected=current, last_detected_bar=last_bar,
            bars_since_detected=bars_since,
            details={"downtrend": self._channel_details(down), "uptrend": self._channel_details(up)},
        )

    def _order_block_item(self, result: PatternResult) -> DetectionItem:
        bullish = self.order_block.bullish_ob
        bearish = self.order_block.bearish_ob
        current = any(ob is not None and ob.valid for ob in (bullish, bearish))
        recent, last_bar, bars_since = self._recent_state("order_block", current)
        direction = "mixed" if bullish and bearish else "bullish" if bullish else "bearish" if bearish else None
        return DetectionItem(
            name="order_block", detected=recent, direction=direction,
            strength=result.strength, currently_detected=current,
            last_detected_bar=last_bar, bars_since_detected=bars_since,
            details={"bullish_ob": self._order_block_details(bullish), "bearish_ob": self._order_block_details(bearish)},
        )

    def _fair_value_gap_item(self, result: PatternResult) -> DetectionItem:
        fvg = self.fair_value_gap.last_fvg
        current = fvg is not None
        recent, last_bar, bars_since = self._recent_state("fair_value_gap", current)
        return DetectionItem(
            name="fair_value_gap", detected=recent,
            direction=fvg.direction if fvg is not None else None,
            strength=result.strength, currently_detected=current,
            last_detected_bar=last_bar, bars_since_detected=bars_since,
            details={"last_fvg": self._fvg_details(fvg)},
        )

    def _recent_state(self, name: str, current: bool) -> tuple[bool, int | None, int | None]:
        if current:
            self._last_detected_bar[name] = self._bar_index
        last_bar = self._last_detected_bar.get(name)
        if last_bar is None:
            return False, None, None
        bars_since = self._bar_index - last_bar
        return bars_since <= self.parameters["lookback_bars"], last_bar, bars_since

    @staticmethod
    def _channel_details(channel: TrendChannel | None) -> dict[str, Any] | None:
        if channel is None:
            return None
        return {
            "direction": channel.direction, "slope": channel.slope,
            "lower_now": channel.lower_now, "upper_now": channel.upper_now,
            "channel_gap": channel.channel_gap,
            "l1_idx": channel.l1_idx, "l1_price": channel.l1_price,
            "l2_idx": channel.l2_idx, "l2_price": channel.l2_price,
            "h1_idx": channel.h1_idx, "h1_price": channel.h1_price,
        }

    @staticmethod
    def _order_block_details(ob: OrderBlock | None) -> dict[str, Any] | None:
        if ob is None:
            return None
        return {
            "direction": ob.direction, "ob_open": ob.ob_open, "ob_close": ob.ob_close,
            "ob_high": ob.ob_high, "ob_low": ob.ob_low,
            "timestamp": ob.timestamp, "valid": ob.valid,
        }

    @staticmethod
    def _fvg_details(fvg: FairValueGap | None) -> dict[str, Any] | None:
        if fvg is None:
            return None
        return {
            "direction": fvg.direction, "trend": fvg.trend,
            "lower": fvg.lower, "upper": fvg.upper,
            "start_timestamp": fvg.start_timestamp,
            "middle_timestamp": fvg.middle_timestamp,
            "end_timestamp": fvg.end_timestamp,
            "gap_size": fvg.gap_size, "gap_pct": fvg.gap_pct,
            "middle_range": fvg.middle_range, "side_range_max": fvg.side_range_max,
            "filled": fvg.filled, "filled_timestamp": fvg.filled_timestamp,
        }
